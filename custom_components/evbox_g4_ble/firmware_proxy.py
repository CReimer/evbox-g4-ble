"""Temporary HTTPS-to-FTP bridge for EVBox firmware updates."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from functools import partial
import ipaddress
import logging
from pathlib import Path
import secrets
import shutil
import socket
import tempfile
from urllib.parse import urlsplit

import aioftp
import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN
from .ftp_server import EVBoxFTPServer
from .firmware_proxy_state import (
    FirmwareProxyRegistry,
    FirmwareUpdateState,
    activate_proxy,
    get_update_state,
    proxy_is_active,
    release_proxy,
    reserve_proxy,
    running_proxies,
    update_transfer,
)
from .protocol import firmware_update_payload, wifi_status

_LOGGER = logging.getLogger(__name__)

_MAX_FIRMWARE_SIZE = 64 * 1024 * 1024
_DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=180)
_FTP_LIFETIME = 15 * 60
_PROXIES = "firmware_proxies"
SIGNAL_FIRMWARE_UPDATE = f"{DOMAIN}_firmware_update"


def _route_ipv4(remote_address: str) -> str:
    """Return the local IPv4 address used to reach the charger."""
    remote = ipaddress.ip_address(remote_address)
    if remote.version != 4:
        raise ValueError("the charger did not report an IPv4 address")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((str(remote), 9))
        return str(sock.getsockname()[0])


async def _download_firmware(hass: HomeAssistant, url: str) -> bytes:
    """Download one bounded firmware image through Home Assistant's session."""
    try:
        async with async_get_clientsession(hass).get(
            url, timeout=_DOWNLOAD_TIMEOUT
        ) as response:
            response.raise_for_status()
            length = response.content_length
            if length is not None and length > _MAX_FIRMWARE_SIZE:
                raise HomeAssistantError(
                    "Die Firmwaredatei ist größer als 64 MiB"
                )
            payload = bytearray()
            async for chunk in response.content.iter_chunked(64 * 1024):
                payload.extend(chunk)
                if len(payload) > _MAX_FIRMWARE_SIZE:
                    raise HomeAssistantError(
                        "Die Firmwaredatei ist größer als 64 MiB"
                    )
    except HomeAssistantError:
        raise
    except (aiohttp.ClientError, TimeoutError) as err:
        raise HomeAssistantError(
            f"Die Firmwaredatei konnte nicht heruntergeladen werden: {err}"
        ) from err
    if not payload:
        raise HomeAssistantError("Die heruntergeladene Firmwaredatei ist leer")
    return bytes(payload)


@dataclass(eq=False)
class _FirmwareProxy:
    """A short-lived, credential-protected, read-only FTP server."""

    hass: HomeAssistant
    directory: Path
    server: aioftp.Server
    location: str
    charger_ip: str
    state: FirmwareUpdateState
    _cleanup_task: asyncio.Task[None] | None = field(default=None, init=False)
    _closed: bool = field(default=False, init=False)

    def arm_cleanup(self) -> None:
        """Keep the file available long enough for the charger to fetch it."""
        self._cleanup_task = self.hass.async_create_task(
            self._async_expire(), "EVBox firmware FTP bridge cleanup"
        )

    async def _async_expire(self) -> None:
        await asyncio.sleep(_FTP_LIFETIME)
        await self.async_close(
            "Timed out waiting for the firmware installation to finish"
        )

    async def async_close(self, error: str | None = None) -> None:
        """Close the server and remove its downloaded firmware file."""
        if self._closed:
            return
        self._closed = True
        proxies = self.hass.data.get(DOMAIN, {}).get(_PROXIES, {})
        release_proxy(proxies, self.charger_ip, self.state, self, error)
        async_dispatcher_send(self.hass, SIGNAL_FIRMWARE_UPDATE)
        await self.server.close()
        await self.hass.async_add_executor_job(shutil.rmtree, self.directory, True)


def _remove_stale_directories(storage_path: str) -> None:
    """Remove bridge directories left behind by an interrupted HA process."""
    for directory in Path(storage_path).glob("evbox-firmware-*"):
        if directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)


async def async_cleanup_firmware_proxies(hass: HomeAssistant) -> None:
    """Close live proxies and remove files left by an earlier process."""
    proxies = hass.data.get(DOMAIN, {}).get(_PROXIES, {})
    for proxy in running_proxies(proxies):
        await proxy.async_close()
    await hass.async_add_executor_job(
        _remove_stale_directories, hass.config.path(".storage")
    )


def _proxy_registry(hass: HomeAssistant) -> FirmwareProxyRegistry:
    """Return the per-charger firmware proxy registry."""
    return hass.data.setdefault(DOMAIN, {}).setdefault(_PROXIES, {})


def firmware_update_in_progress(
    hass: HomeAssistant, charger_ip: str
) -> bool:
    """Return whether this charger already has an active update bridge."""
    return proxy_is_active(_proxy_registry(hass), charger_ip)


def firmware_update_state(
    hass: HomeAssistant, charger_ip: str
) -> FirmwareUpdateState | None:
    """Return the last locally observed update state for a charger."""
    return get_update_state(_proxy_registry(hass), charger_ip)


def mark_firmware_installed(hass: HomeAssistant, charger_ip: str) -> None:
    """Finish a proxied update after BLE reports the target version."""
    state = firmware_update_state(hass, charger_ip)
    if state is None or state.phase == "installed":
        return
    update_transfer(
        state,
        "installed",
        state.total_bytes,
        state.total_bytes,
    )
    async_dispatcher_send(hass, SIGNAL_FIRMWARE_UPDATE)


async def _async_create_proxy(
    hass: HomeAssistant, source_url: str, charger_ip: str
) -> _FirmwareProxy:
    proxies = _proxy_registry(hass)
    state = reserve_proxy(proxies, charger_ip)
    if state is None:
        raise HomeAssistantError(
            "Für diese Wallbox läuft bereits ein Firmwareupdate"
        )
    async_dispatcher_send(hass, SIGNAL_FIRMWARE_UPDATE)

    last_percentage = -1

    def report_transfer(
        phase: str,
        transferred_bytes: int,
        total_bytes: int,
        error: str | None,
    ) -> None:
        nonlocal last_percentage
        percentage = (
            transferred_bytes * 100 // total_bytes if total_bytes else 0
        )
        if phase == "downloading" and percentage == last_percentage:
            return
        last_percentage = percentage
        update_transfer(
            state,
            phase,
            transferred_bytes,
            total_bytes,
            error,
        )
        if error:
            _LOGGER.error("EVBox firmware update failed: %s", error)
        async_dispatcher_send(hass, SIGNAL_FIRMWARE_UPDATE)

    try:
        payload = await _download_firmware(hass, source_url)
        local_ip = await hass.async_add_executor_job(_route_ipv4, charger_ip)
        directory = Path(
            await hass.async_add_executor_job(
                partial(
                    tempfile.mkdtemp,
                    prefix="evbox-firmware-",
                    dir=hass.config.path(".storage"),
                )
            )
        )
        filename = f"{secrets.token_hex(8)}.evb"
        await hass.async_add_executor_job(
            (directory / filename).write_bytes, payload
        )
        username = "evbox"
        password = secrets.token_hex(16)
        user = aioftp.User(
            username,
            password,
            base_path=directory,
            permissions=[aioftp.Permission("/", readable=True, writable=False)],
            maximum_connections=1,
        )
        server = EVBoxFTPServer(
            [user],
            idle_timeout=10 * 60,
            maximum_connections=1,
            ipv4_pasv_forced_response_address=local_ip,
            transfer_callback=report_transfer,
        )
        await server.start(local_ip, 0)
    except (OSError, ValueError) as err:
        release_proxy(proxies, charger_ip, state)
        async_dispatcher_send(hass, SIGNAL_FIRMWARE_UPDATE)
        if "directory" in locals():
            await hass.async_add_executor_job(shutil.rmtree, directory, True)
        raise HomeAssistantError(
            f"Die lokale FTP-Freigabe konnte nicht gestartet werden: {err}"
        ) from err
    except BaseException:
        release_proxy(proxies, charger_ip, state)
        async_dispatcher_send(hass, SIGNAL_FIRMWARE_UPDATE)
        if "directory" in locals():
            await hass.async_add_executor_job(shutil.rmtree, directory, True)
        raise

    location = (
        f"ftp://{username}:{password}@{local_ip}:{server.server_port}/{filename}"
    )
    proxy = _FirmwareProxy(
        hass, directory, server, location, charger_ip, state
    )
    activate_proxy(proxies, charger_ip, state, proxy)
    async_dispatcher_send(hass, SIGNAL_FIRMWARE_UPDATE)
    _LOGGER.info(
        "Temporary EVBox firmware FTP bridge started on %s:%s",
        local_ip,
        server.server_port,
    )
    return proxy


async def async_start_firmware_update(
    hass: HomeAssistant, coordinator, source_url: str
) -> tuple[object, bool]:
    """Start an update, proxying web downloads to the charger's FTP client."""
    scheme = urlsplit(source_url).scheme.lower()
    proxy: _FirmwareProxy | None = None
    if scheme in ("http", "https"):
        charger_ip = wifi_status(coordinator.data.get("wifi_status")).get(
            "ip_address"
        )
        if not charger_ip:
            raise HomeAssistantError(
                "Für HTTPS-Updates muss die Wallbox per WLAN verbunden sein "
                "und eine IPv4-Adresse melden"
            )
        proxy = await _async_create_proxy(hass, source_url, str(charger_ip))
        location = proxy.location
    elif scheme == "ftp":
        location = source_url
    else:
        raise HomeAssistantError(
            "Firmware-URLs müssen mit https://, http:// oder ftp:// beginnen"
        )

    try:
        result = await coordinator.client.ocpp(
            "UpdateFirmware", firmware_update_payload(location)
        )
    except Exception:
        if proxy is not None:
            await proxy.async_close("Firmware update command failed")
        raise
    if proxy is not None:
        proxy.arm_cleanup()
    return result, proxy is not None

"""BLE client for EVBox Elvi legacy chargers."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from contextlib import suppress
import logging
from typing import Any

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .const import (
    CHARACTERISTIC_UUID,
    CHUNK_SIZE,
    COMMAND_TIMEOUT,
    ESP32_CHUNK_SIZE,
    ESP32_NOTIFY_UUID,
    ESP32_WRITE_UUID,
    KEY_CONNECTOR_LIST,
    KEY_AUTO_START,
    KEY_LOCAL_AUTH_LIST_ENABLED,
    KEY_SERIAL_AS_CONNECTOR_ID,
    KEY_SERVER_URL,
    WIFI_CONNECT_TIMEOUT,
)
from .protocol import (
    EVBoxProtocolError,
    FrameDecoder,
    build_data_transfer,
    build_ocpp_call,
    build_ocpp_call_result,
    backend_companion_values,
    chunks,
    configuration_values,
    configuration_boolean,
    parse_response,
    parse_event_payload,
    connection_information,
    data_transfer_event_details,
    satellite_scan_results,
    wifi_status,
)

_LOGGER = logging.getLogger(__name__)

# The legacy Elvi protocol expects the client identifier used by the official
# EVBox Connect SDK. This is a protocol value, not a user-visible device name.
EVBOX_CLIENT_NAME = "Android"


class EVBoxConnectionError(Exception):
    """Raised when the charger cannot be reached or authenticated."""


class _ResponseRouter:
    """Route all notifications from one BLE session to pending requests."""

    def __init__(self) -> None:
        self._decoder = FrameDecoder()
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._markers: dict[str, asyncio.Future[Any]] = {}
        self._event_message_ids: dict[str, str] = {}

    def notification(self, _sender: Any, data: bytearray) -> None:
        try:
            for message in self._decoder.feed(data):
                event = data_transfer_event_details(message)
                if event is not None:
                    marker, payload, event_message_id = event
                    self._event_message_ids[marker] = event_message_id
                    marker_future = self._markers.get(marker)
                    if marker_future is not None and not marker_future.done():
                        marker_future.set_result(payload)
                    # Unsolicited firmware events are valid but do not answer a
                    # pending command and must not poison the BLE session.
                    continue
                matched_event = False
                for marker, marker_future in self._markers.items():
                    if marker in message and not marker_future.done():
                        marker_future.set_result(parse_event_payload(message, marker))
                        matched_event = True
                try:
                    response = parse_response(message)
                except EVBoxProtocolError:
                    # Firmware sends Wi-Fi/RF/connection events as OCPP CALLs,
                    # not CALLRESULTs. They are complete once routed above.
                    if matched_event:
                        continue
                    raise
                future = self._pending.get(response.message_id)
                if future is not None and not future.done():
                    future.set_result(response.payload)
        except Exception as err:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(err)
            for future in self._markers.values():
                if not future.done():
                    future.set_exception(err)

    def expect_marker(self, marker: str) -> asyncio.Future[Any]:
        """Register for a charger notification whose raw frame contains marker."""
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._markers[marker] = future
        return future

    async def wait_for_marker(
        self, marker: str, future: asyncio.Future[Any], timeout: float
    ) -> Any:
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            self._markers.pop(marker, None)

    def discard_marker(
        self, marker: str, future: asyncio.Future[Any]
    ) -> None:
        """Remove a marker and consume an error set before it was awaited."""
        if self._markers.get(marker) is future:
            self._markers.pop(marker, None)
        if not future.done():
            future.cancel()
        elif not future.cancelled():
            future.exception()

    def take_event_message_id(self, marker: str) -> str | None:
        """Return and clear the charger CALL ID associated with a marker."""
        return self._event_message_ids.pop(marker, None)

    async def request(
        self,
        client: BleakClientWithServiceCache,
        request_id: str,
        raw: str,
        write_uuid: str,
        chunk_size: int,
        response_marker: str | None = None,
    ) -> Any:
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        marker_future = (
            self.expect_marker(response_marker) if response_marker else None
        )
        try:
            for part in chunks(raw, chunk_size):
                await client.write_gatt_char(write_uuid, part, response=True)
            if marker_future is None:
                return await asyncio.wait_for(future, COMMAND_TIMEOUT)
            loop = asyncio.get_running_loop()
            deadline = loop.time() + WIFI_CONNECT_TIMEOUT
            done, _ = await asyncio.wait(
                (future, marker_future),
                timeout=WIFI_CONNECT_TIMEOUT,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise TimeoutError
            if marker_future in done:
                return marker_future.result()

            # evbWifiSet can first acknowledge the command without returning
            # a network state and only then emit evbWifiStatusNotification.
            # A real direct status is final enough for the UI; a generic
            # acknowledgement must not win that race and become a spurious
            # "connection failed" result.
            direct_value = future.result()
            if wifi_status(direct_value).get("status") != "unknown":
                return direct_value
            remaining = max(0.0, deadline - loop.time())
            try:
                return await asyncio.wait_for(marker_future, remaining)
            except TimeoutError:
                return direct_value
        finally:
            self._pending.pop(request_id, None)
            if response_marker:
                self._markers.pop(response_marker, None)


class EVBoxClient:
    """Serialize authenticated BLE sessions through a HA Bluetooth adapter."""

    def __init__(self, hass: HomeAssistant, address: str, security_code: str) -> None:
        self.hass = hass
        self.address = address
        self._security_code = security_code
        self._lock = asyncio.Lock()

    async def _connect(self) -> BleakClientWithServiceCache:
        device = bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)
        if device is None:
            raise EVBoxConnectionError("No connectable Bluetooth proxy can currently reach the charger")
        try:
            return await establish_connection(
                BleakClientWithServiceCache,
                device,
                "EVBox Elvi",
                max_attempts=3,
                # Legacy Elvi firmware can expose stale cached handles after a
                # previous client session; discover the GATT table each time.
                use_services_cache=False,
            )
        except Exception as err:
            raise EVBoxConnectionError("Bluetooth connection failed") from err

    async def _evb(
        self,
        client: BleakClientWithServiceCache,
        router: _ResponseRouter,
        command: str,
        values: Iterable[Any] = (),
        write_uuid: str = CHARACTERISTIC_UUID,
        chunk_size: int = CHUNK_SIZE,
        response_marker: str | None = None,
    ) -> Any:
        request_id, raw = build_data_transfer(command, values)
        return await router.request(
            client,
            request_id,
            raw,
            write_uuid,
            chunk_size,
            response_marker,
        )

    async def _ocpp(
        self,
        client: BleakClientWithServiceCache,
        router: _ResponseRouter,
        action: str,
        payload: Mapping[str, Any],
        write_uuid: str = CHARACTERISTIC_UUID,
        chunk_size: int = CHUNK_SIZE,
    ) -> Any:
        request_id, raw = build_ocpp_call(action, payload)
        return await router.request(client, request_id, raw, write_uuid, chunk_size)

    async def _acknowledge_event(
        self,
        client: BleakClientWithServiceCache,
        router: _ResponseRouter,
        marker: str,
        write_uuid: str,
        chunk_size: int,
    ) -> None:
        """Complete a charger-initiated DataTransfer like EVBox Connect."""
        message_id = router.take_event_message_id(marker)
        if message_id is None:
            return
        raw = build_ocpp_call_result(message_id, {"status": "Accepted"})
        for part in chunks(raw, chunk_size):
            await client.write_gatt_char(write_uuid, part, response=True)

    async def _authenticate(
        self,
        client: BleakClientWithServiceCache,
        router: _ResponseRouter,
        write_uuid: str,
        chunk_size: int,
    ) -> None:
        result = await self._evb(
            client,
            router,
            "evbBTConnect",
            (EVBOX_CLIENT_NAME, self._security_code),
            write_uuid,
            chunk_size,
        )
        if (
            result is False
            or isinstance(result, str)
            and result.lower() == "false"
            or isinstance(result, Mapping)
            and result.get("status") is False
        ):
            raise EVBoxConnectionError("EVBox security code was rejected")

    async def session(self, operations: list[tuple[str, str, Any]]) -> list[Any]:
        """Run operations in one authenticated connection."""
        async with self._lock:
            client = await self._connect()
            router = _ResponseRouter()
            notifying = False
            try:
                is_esp32 = (
                    client.services.get_characteristic(ESP32_WRITE_UUID) is not None
                    and client.services.get_characteristic(ESP32_NOTIFY_UUID) is not None
                )
                write_uuid = ESP32_WRITE_UUID if is_esp32 else CHARACTERISTIC_UUID
                notify_uuid = ESP32_NOTIFY_UUID if is_esp32 else CHARACTERISTIC_UUID
                chunk_size = ESP32_CHUNK_SIZE if is_esp32 else CHUNK_SIZE
                # Legacy Elvi firmware permits the CCCD to be enabled once per
                # GATT connection. Keep one notification subscription open for
                # authentication and every following command, like EVBox Connect.
                await client.start_notify(notify_uuid, router.notification)
                notifying = True
                await self._authenticate(client, router, write_uuid, chunk_size)
                results = []
                for kind, name, payload in operations:
                    if kind in ("evb", "optional_evb", "wifi_set"):
                        try:
                            results.append(
                                await self._evb(
                                    client,
                                    router,
                                    name,
                                    payload,
                                    write_uuid,
                                    chunk_size,
                                    "evbWifiStatusNotification"
                                    if kind == "wifi_set"
                                    else None,
                                )
                            )
                        except (TimeoutError, EVBoxProtocolError):
                            if kind != "optional_evb":
                                raise
                            _LOGGER.debug(
                                "EVBox firmware does not provide optional command %s",
                                name,
                            )
                            results.append(None)
                    elif kind in ("ocpp", "optional_ocpp"):
                        try:
                            results.append(
                                await self._ocpp(
                                    client,
                                    router,
                                    name,
                                    payload,
                                    write_uuid,
                                    chunk_size,
                                )
                            )
                        except (TimeoutError, EVBoxProtocolError):
                            if kind != "optional_ocpp":
                                raise
                            _LOGGER.debug(
                                "EVBox firmware does not provide optional OCPP command %s",
                                name,
                            )
                            results.append(None)
                    elif kind == "auto_start":
                        local_auth_payload = await self._ocpp(
                            client,
                            router,
                            "GetConfiguration",
                            {"key": [KEY_LOCAL_AUTH_LIST_ENABLED]},
                            write_uuid,
                            chunk_size,
                        )
                        local_auth = configuration_boolean(
                            local_auth_payload, KEY_LOCAL_AUTH_LIST_ENABLED
                        )
                        if local_auth is None:
                            raise EVBoxProtocolError(
                                "Elvi did not return LocalAuthListEnabled"
                            )
                        if not local_auth:
                            await self._ocpp(
                                client,
                                router,
                                "ChangeConfiguration",
                                {
                                    "key": KEY_LOCAL_AUTH_LIST_ENABLED,
                                    "value": "true",
                                },
                                write_uuid,
                                chunk_size,
                            )
                        results.append(
                            await self._ocpp(
                                client,
                                router,
                                "ChangeConfiguration",
                                {"key": KEY_AUTO_START, "value": str(payload)},
                                write_uuid,
                                chunk_size,
                            )
                        )
                    elif kind == "connection_info":
                        marker = "evbConnectionInfo"
                        future = router.expect_marker(marker)
                        try:
                            await self._ocpp(
                                client,
                                router,
                                "ChangeConfiguration",
                                {"key": "evb_Trigger", "value": "ConnectionInfo"},
                                write_uuid,
                                chunk_size,
                            )
                            value = await router.wait_for_marker(
                                marker, future, COMMAND_TIMEOUT
                            )
                            await self._acknowledge_event(
                                client, router, marker, write_uuid, chunk_size
                            )
                            results.append(connection_information(value))
                        except (TimeoutError, EVBoxProtocolError):
                            _LOGGER.debug(
                                "EVBox did not provide optional connection information"
                            )
                            results.append({})
                        finally:
                            router.discard_marker(marker, future)
                    elif kind == "rf_scan":
                        marker = "evbRFScan"
                        future = router.expect_marker(marker)
                        timeout = int(payload)
                        try:
                            await self._ocpp(
                                client,
                                router,
                                "ChangeConfiguration",
                                {
                                    "key": "evb_Trigger",
                                    "value": f"RFScan,{timeout}",
                                },
                                write_uuid,
                                chunk_size,
                            )
                            value = await router.wait_for_marker(
                                marker, future, timeout + COMMAND_TIMEOUT
                            )
                            await self._acknowledge_event(
                                client, router, marker, write_uuid, chunk_size
                            )
                            results.append(satellite_scan_results(value))
                        finally:
                            router.discard_marker(marker, future)
                    else:
                        raise EVBoxProtocolError(f"Unsupported session operation {kind}")
                return results
            except (EVBoxProtocolError, EVBoxConnectionError):
                raise
            except Exception as err:
                _LOGGER.debug("EVBox BLE session failed: %s", type(err).__name__)
                raise EVBoxConnectionError("EVBox BLE session failed") from err
            finally:
                if notifying and client.is_connected:
                    with suppress(Exception):
                        await client.stop_notify(notify_uuid)
                if client.is_connected:
                    with suppress(Exception):
                        await client.disconnect()

    async def evb(self, command: str, values: Iterable[Any] = ()) -> Any:
        return (await self.session([("evb", command, tuple(values))]))[0]

    async def ocpp(self, action: str, payload: Mapping[str, Any]) -> Any:
        return (await self.session([("ocpp", action, dict(payload))]))[0]

    async def get_configuration(self, keys: Iterable[str]) -> dict[str, Any]:
        # Elvi firmware rejects one GetConfiguration request containing the
        # complete app key set. The official app reads these values as small
        # requests, so keep one authenticated BLE session and request each key
        # separately.
        key_list = list(keys)
        if not key_list:
            return {}
        payloads = await self.session(
            [
                ("optional_ocpp", "GetConfiguration", {"key": [key]})
                for key in key_list
            ]
        )
        result: dict[str, Any] = {}
        for payload in payloads:
            if payload is None:
                continue
            try:
                result.update(configuration_values(payload))
            except EVBoxProtocolError:
                # Some old firmware acknowledges an unknown key without a
                # GetConfiguration body. Treat that key as unsupported just
                # like an explicit CALLERROR, not as a failed integration.
                _LOGGER.debug(
                    "EVBox firmware returned no configuration body for an optional key"
                )
        return result

    async def set_configuration(self, key: str, value: Any) -> Any:
        return await self.ocpp("ChangeConfiguration", {"key": key, "value": str(value).lower() if isinstance(value, bool) else str(value)})

    async def set_server(self, url: str) -> list[Any]:
        """Set the server and its hidden Everon compatibility flags like the app."""
        companion = backend_companion_values(url)
        return await self.session(
            [
                ("ocpp", "ChangeConfiguration", {"key": KEY_SERVER_URL, "value": url}),
                (
                    "ocpp",
                    "ChangeConfiguration",
                    {
                        "key": KEY_SERIAL_AS_CONNECTOR_ID,
                        "value": str(companion[KEY_SERIAL_AS_CONNECTOR_ID]).lower(),
                    },
                ),
                (
                    "ocpp",
                    "ChangeConfiguration",
                    {
                        "key": KEY_CONNECTOR_LIST,
                        "value": str(companion[KEY_CONNECTOR_LIST]).lower(),
                    },
                ),
            ]
        )

    async def set_auto_start(self, value: str) -> Any:
        """Set AutoStart after the hidden local-list precondition used by the app."""
        return (await self.session([("auto_start", "", value)]))[0]

    async def connection_info(self) -> dict[str, Any]:
        """Request the asynchronous connection information shown by the app."""
        return (await self.session([("connection_info", "", None)]))[0]

    async def set_wifi(self, values: Iterable[Any]) -> Any:
        """Set Wi-Fi and accept both response IDs used by Elvi firmware."""
        return (await self.session([("wifi_set", "evbWifiSet", tuple(values))]))[0]

    async def scan_satellites(self, timeout: int = 40) -> list[dict[str, Any]]:
        """Run RF scan and wait for the asynchronous result list."""
        return (await self.session([("rf_scan", "", timeout)]))[0]

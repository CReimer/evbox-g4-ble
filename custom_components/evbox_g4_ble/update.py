"""Firmware availability entity for EVBox Gen4 BLE."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import Message
import logging
from typing import Any

import aiohttp

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_ADDRESS, DOMAIN, KEY_BOOT_INFO
from .entity import EVBoxEntity
from .firmware import (
    CATALOG_CHECKED_AT,
    FIRMWARE_ARTICLE_URL,
    FIRMWARE_DOCUMENT_URL,
    installed_release,
    latest_release,
    release_from_filename,
)
from .firmware_proxy import (
    async_start_firmware_update,
    firmware_update_in_progress,
)
from .protocol import boot_information, wifi_status

_LOGGER = logging.getLogger(__name__)

_CATALOG_COORDINATOR = "firmware_catalog_coordinator"
_CATALOG_INTERVAL = timedelta(hours=12)
_CATALOG_TIMEOUT = aiohttp.ClientTimeout(total=30)


class EVBoxFirmwareCatalogCoordinator(DataUpdateCoordinator[dict[str, str]]):
    """Check the public EVBox firmware document without downloading it."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name="EVBox firmware catalog",
            update_interval=_CATALOG_INTERVAL,
        )
        self.async_set_updated_data(
            {
                "version": latest_release("G4E-") or "",
                "url": FIRMWARE_DOCUMENT_URL,
                "checked_at": CATALOG_CHECKED_AT,
            }
        )

    async def _async_update_data(self) -> dict[str, str]:
        try:
            async with async_get_clientsession(self.hass).get(
                FIRMWARE_DOCUMENT_URL,
                headers={"Range": "bytes=0-0"},
                timeout=_CATALOG_TIMEOUT,
            ) as response:
                response.raise_for_status()
                disposition = Message()
                disposition["content-disposition"] = response.headers.get(
                    "Content-Disposition", ""
                )
                filename = disposition.get_filename()
                version = release_from_filename(filename)
                if version is None:
                    raise UpdateFailed(
                        "EVBox firmware response has no recognizable .evb filename"
                    )
                await response.content.read(1)
        except UpdateFailed:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(
                f"Could not check the public EVBox firmware document: {err}"
            ) from err
        return {
            "version": version,
            "url": FIRMWARE_DOCUMENT_URL,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }


class EVBoxFirmwareUpdate(EVBoxEntity, UpdateEntity):
    """Report whether EVBox Connect knows a newer compatible release."""

    _attr_translation_key = "firmware"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
    )

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator,
        catalog: EVBoxFirmwareCatalogCoordinator,
        address: str,
    ) -> None:
        super().__init__(coordinator, address, "firmware_update")
        self._firmware_hass = hass
        self.catalog = catalog
        self._attr_release_url = FIRMWARE_ARTICLE_URL

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.catalog.async_add_listener(self.async_write_ha_state)
        )

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.installed_version is not None
            and self.latest_version is not None
        )

    @property
    def installed_version(self) -> str | None:
        details = boot_information(self.coordinator.data.get(KEY_BOOT_INFO))
        return installed_release(details.get("firmware_version"))

    @property
    def latest_version(self) -> str | None:
        details = boot_information(self.coordinator.data.get(KEY_BOOT_INFO))
        if latest_release(details.get("model")) is None:
            return None
        return self.catalog.data.get("version") if self.catalog.data else None

    @property
    def in_progress(self) -> bool:
        """Return whether the temporary bridge is serving this charger."""
        charger_ip = wifi_status(
            self.coordinator.data.get("wifi_status")
        ).get("ip_address")
        return bool(
            charger_ip
            and firmware_update_in_progress(
                self._firmware_hass, str(charger_ip)
            )
        )

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Download the vendor image and hand it to the charger's FTP client."""
        if backup:
            raise ValueError("Firmware backups are not supported by EVBox G4")
        if not self.catalog.data or not self.catalog.data.get("url"):
            raise ValueError("No EVBox firmware download is available")
        await async_start_firmware_update(
            self._firmware_hass, self.coordinator, self.catalog.data["url"]
        )

    @property
    def release_summary(self) -> str:
        return (
            "Mit dem öffentlich bereitgestellten EVBox-Firmwarestand verglichen; "
            "die Installation wird nicht automatisch gestartet."
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        details = boot_information(self.coordinator.data.get(KEY_BOOT_INFO))
        return {
            "catalog_checked_at": self.catalog.data.get(
                "checked_at", CATALOG_CHECKED_AT
            ),
            "model": details.get("model"),
            "full_installed_version": details.get("firmware_version"),
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    domain_data = hass.data.setdefault(DOMAIN, {})
    catalog = domain_data.get(_CATALOG_COORDINATOR)
    if catalog is None:
        catalog = EVBoxFirmwareCatalogCoordinator(hass)
        domain_data[_CATALOG_COORDINATOR] = catalog
        await catalog.async_refresh()
    # Add the entity even when the first BLE refresh did not yet return
    # BootInfo. It becomes available as soon as the coordinator has both the
    # installed version and a catalog entry for the reported model.
    async_add_entities(
        [EVBoxFirmwareUpdate(hass, coordinator, catalog, entry.data[CONF_ADDRESS])]
    )

"""Firmware availability entity for EVBox Gen4 BLE."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ADDRESS, KEY_BOOT_INFO
from .entity import EVBoxEntity
from .firmware import CATALOG_CHECKED_AT, installed_release, latest_release
from .protocol import boot_information


class EVBoxFirmwareUpdate(EVBoxEntity, UpdateEntity):
    """Report whether EVBox Connect knows a newer compatible release."""

    _attr_translation_key = "firmware"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, address: str) -> None:
        super().__init__(coordinator, address, "firmware_update")

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
        return latest_release(details.get("model"))

    @property
    def release_summary(self) -> str:
        return (
            "Mit dem von EVBox Connect bekannten Firmwarestand verglichen; "
            "die Installation wird nicht automatisch gestartet."
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        details = boot_information(self.coordinator.data.get(KEY_BOOT_INFO))
        return {
            "catalog_checked_at": CATALOG_CHECKED_AT,
            "model": details.get("model"),
            "full_installed_version": details.get("firmware_version"),
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    # Add the entity even when the first BLE refresh did not yet return
    # BootInfo. It becomes available as soon as the coordinator has both the
    # installed version and a catalog entry for the reported model.
    async_add_entities(
        [EVBoxFirmwareUpdate(coordinator, entry.data[CONF_ADDRESS])]
    )

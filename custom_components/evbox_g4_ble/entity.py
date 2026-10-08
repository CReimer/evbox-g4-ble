"""Base entity for EVBox Gen4 BLE."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .models import EVBoxConfigEntry
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, KEY_BOOT_INFO
from .coordinator import EVBoxCoordinator
from .protocol import boot_information


class EVBoxEntity(CoordinatorEntity[EVBoxCoordinator]):
    """Common entity identity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: EVBoxCoordinator, address: str, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{address}_{key}"
        boot = boot_information(coordinator.data.get(KEY_BOOT_INFO))
        device_info: DeviceInfo = {
            "identifiers": {(DOMAIN, address)},
            "name": getattr(coordinator, "device_name", None) or "EVBox G4",
            "manufacturer": "EVBox",
            "model": boot.get("model") or "EVBox Gen4",
            "connections": {("bluetooth", address)},
        }
        if boot.get("serial_number"):
            device_info["serial_number"] = boot["serial_number"]
        if boot.get("firmware_version"):
            device_info["sw_version"] = boot["firmware_version"]
        self._attr_device_info = device_info


def async_add_supported_entities[T: EVBoxEntity](
    hass: HomeAssistant,
    entry: EVBoxConfigEntry,
    async_add_entities: AddEntitiesCallback,
    entities: Iterable[T],
    supported: Callable[[T], bool],
) -> None:
    """Discover late capabilities and restore previously registered entities."""
    pending = list(entities)
    registry = er.async_get(hass) if hasattr(hass, "data") else None

    @callback
    def discover() -> None:
        added = []
        for entity in list(pending):
            registered = registry is not None and any(
                item.unique_id == entity.unique_id
                for item in er.async_entries_for_config_entry(registry, entry.entry_id)
            )
            if supported(entity) or registered:
                pending.remove(entity)
                added.append(entity)
        if added:
            async_add_entities(added)

    discover()
    if hasattr(entry, "async_on_unload"):
        entry.async_on_unload(entry.runtime_data.async_add_listener(discover))

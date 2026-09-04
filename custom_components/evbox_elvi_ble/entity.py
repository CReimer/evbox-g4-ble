"""Base entity for EVBox Elvi BLE."""

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
        device_info = {
            "identifiers": {(DOMAIN, address)},
            "name": getattr(coordinator, "device_name", None) or "EVBox Elvi",
            "manufacturer": "EVBox",
            "model": boot.get("model") or "Elvi",
            "connections": {("bluetooth", address)},
        }
        if boot.get("serial_number"):
            device_info["serial_number"] = boot["serial_number"]
        if boot.get("firmware_version"):
            device_info["sw_version"] = boot["firmware_version"]
        self._attr_device_info = DeviceInfo(
            **device_info,
        )

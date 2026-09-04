"""Sensors for EVBox Elvi BLE."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_ADDRESS,
    KEY_BOOT_INFO,
    KEY_RF_MODULES,
)
from .entity import EVBoxEntity
from .protocol import (
    boot_information,
    rf_modules,
    wifi_network,
    wifi_status,
)


@dataclass(frozen=True, kw_only=True)
class EVBoxSensorDescription(SensorEntityDescription):
    value_key: str


DESCRIPTIONS = (
    EVBoxSensorDescription(key="boot_info", translation_key="boot_info", value_key=KEY_BOOT_INFO),
    EVBoxSensorDescription(key="wifi_status", translation_key="wifi_status", value_key="wifi_status"),
    EVBoxSensorDescription(key="wifi_network", translation_key="wifi_network", value_key="wifi_network"),
    EVBoxSensorDescription(key="rf_modules", translation_key="rf_modules", value_key=KEY_RF_MODULES),
    EVBoxSensorDescription(key="rfid_count", translation_key="rfid_count", value_key="cards"),
    EVBoxSensorDescription(
        key="active_connection",
        translation_key="active_connection",
        value_key="connection_info",
    ),
    EVBoxSensorDescription(
        key="wifi_signal",
        translation_key="wifi_signal",
        value_key="connection_info",
        native_unit_of_measurement="dBm",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
    ),
    EVBoxSensorDescription(
        key="cellular_signal",
        translation_key="cellular_signal",
        value_key="connection_info",
        native_unit_of_measurement="dBm",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
    ),
)


class EVBoxSensor(EVBoxEntity, SensorEntity):
    entity_description: EVBoxSensorDescription

    def __init__(self, coordinator, address: str, description: EVBoxSensorDescription) -> None:
        super().__init__(coordinator, address, description.key)
        self.entity_description = description
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if description.key == "wifi_status":
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = ["connected", "connecting", "wrong_password", "disconnected", "unknown"]
        elif description.key == "active_connection":
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = ["wifi", "cellular", "none", "unknown"]

    @property
    def native_value(self) -> Any:
        value = self.coordinator.data.get(self.entity_description.value_key)
        if self.entity_description.key == "rfid_count":
            return len(value) if isinstance(value, list) else 0
        if self.entity_description.key == "active_connection":
            current = str(value.get("current_connection", "")).lower() if isinstance(value, dict) else ""
            return {
                "wi-fi": "wifi",
                "wifi": "wifi",
                "cellular": "cellular",
                "cell": "cellular",
                "none": "none",
            }.get(current, "none" if not current else "unknown")
        if self.entity_description.key in ("wifi_signal", "cellular_signal"):
            section = "wifi" if self.entity_description.key == "wifi_signal" else "cellular"
            details = value.get(section, {}) if isinstance(value, dict) else {}
            return details.get("signal_strength")
        if self.entity_description.key == "boot_info":
            return boot_information(value).get("firmware_version")
        if self.entity_description.key == "wifi_status":
            return wifi_status(value)["status"]
        if self.entity_description.key == "wifi_network":
            return wifi_network(value).get("ssid")
        if self.entity_description.key == "rf_modules":
            return len(rf_modules(value))
        if isinstance(value, (dict, list)):
            return str(value)
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key == "rfid_count":
            value = self.coordinator.data.get("cards")
            return {"cards": value if isinstance(value, list) else []}
        if self.entity_description.key == "active_connection":
            return None
        if self.entity_description.key in ("wifi_signal", "cellular_signal"):
            value = self.coordinator.data.get("connection_info")
            section = "wifi" if self.entity_description.key == "wifi_signal" else "cellular"
            details = dict(value.get(section, {})) if isinstance(value, dict) else {}
            # The numeric signal is already the entity state.
            details.pop("signal_strength", None)
            return details
        if self.entity_description.key == "rf_modules":
            value = self.coordinator.data.get(KEY_RF_MODULES)
            return {"satellites": rf_modules(value)}
        if self.entity_description.key == "boot_info":
            value = self.coordinator.data.get(KEY_BOOT_INFO)
            details = boot_information(value)
            # Keep this entity focused on the app's firmware view. The state is
            # the firmware version; vendor and mobile-subscription identifiers
            # are not useful firmware attributes.
            for key in ("firmware_version", "vendor", "iccid", "imsi"):
                details.pop(key, None)
            return details
        if self.entity_description.key == "wifi_status":
            value = self.coordinator.data.get("wifi_status")
            details = wifi_status(value)
            # Status, SSID and RSSI already have dedicated entity states.
            for key in ("status", "status_code", "ssid", "signal_strength"):
                details.pop(key, None)
            return details
        if self.entity_description.key == "wifi_network":
            value = self.coordinator.data.get("wifi_network")
            details = wifi_network(value)
            details.pop("ssid", None)
            return details
        return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data

    def supported(description: EVBoxSensorDescription) -> bool:
        if description.value_key not in coordinator.data:
            return False
        value = coordinator.data.get(description.value_key)
        if value in (None, {}):
            return False
        if description.key == "boot_info":
            details = boot_information(value)
            return bool(details.get("firmware_version") or details.get("model"))
        if description.key == "wifi_status":
            details = wifi_status(value)
            return details.get("status") != "unknown"
        if description.key == "wifi_network":
            return bool(wifi_network(value))
        if description.key == "wifi_signal":
            wifi = value.get("wifi", {}) if isinstance(value, dict) else {}
            return isinstance(wifi.get("signal_strength"), int)
        if description.key == "cellular_signal":
            cellular = value.get("cellular", {}) if isinstance(value, dict) else {}
            return isinstance(cellular.get("signal_strength"), int)
        return True

    async_add_entities(
        EVBoxSensor(coordinator, entry.data[CONF_ADDRESS], description)
        for description in DESCRIPTIONS
        if supported(description)
    )

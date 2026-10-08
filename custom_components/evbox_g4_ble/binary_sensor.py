"""Connectivity sensor for EVBox Elvi."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
else:
    from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from .models import EVBoxConfigEntry
from .coordinator import EVBoxCoordinator
from homeassistant.core import HomeAssistant
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ADDRESS
from .entity import EVBoxEntity


PARALLEL_UPDATES = 0


class EVBoxReachable(EVBoxEntity, BinarySensorEntity):
    _attr_translation_key = "reachable"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.last_update_success


class EVBoxRestartRequired(EVBoxEntity, BinarySensorEntity):
    """Indicate that accepted configuration is pending a charger restart."""

    _attr_translation_key = "restart_required"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool | None:
        return bool(self.coordinator.data.get("restart_required"))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EVBoxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        [
            EVBoxReachable(entry.runtime_data, entry.data[CONF_ADDRESS], "reachable"),
            EVBoxRestartRequired(
                entry.runtime_data,
                entry.data[CONF_ADDRESS],
                "restart_required",
            ),
        ]
    )

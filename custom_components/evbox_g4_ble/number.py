"""Current limit controls for EVBox Elvi."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from .models import EVBoxConfigEntry
from .coordinator import EVBoxCoordinator
from homeassistant.core import HomeAssistant
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ADDRESS, KEY_MAX_CURRENT, KEY_MIN_CURRENT
from .entity import EVBoxEntity, async_add_supported_entities
from .protocol import amperes_to_current, current_to_amperes


PARALLEL_UPDATES = 0


class EVBoxCurrentNumber(EVBoxEntity, NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 6
    _attr_native_max_value = 32
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = "A"
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: EVBoxCoordinator,
        address: str,
        key: str,
        translation_key: str,
    ) -> None:
        super().__init__(coordinator, address, key)
        self._attr_translation_key = translation_key

    @property
    def native_value(self) -> float | None:
        return current_to_amperes(self.coordinator.data.get(self._key))

    @property
    def native_min_value(self) -> float:
        """Do not offer a maximum below the stored minimum."""
        if self._key == KEY_MAX_CURRENT:
            minimum = current_to_amperes(self.coordinator.data.get(KEY_MIN_CURRENT))
            if minimum is not None:
                return max(6, minimum)
        return 6

    @property
    def native_max_value(self) -> float:
        """Do not offer a minimum above the stored maximum."""
        if self._key == KEY_MIN_CURRENT:
            maximum = current_to_amperes(self.coordinator.data.get(KEY_MAX_CURRENT))
            if maximum is not None:
                return min(32, maximum)
        return 32

    async def async_set_native_value(self, value: float) -> None:
        other_key = KEY_MIN_CURRENT if self._key == KEY_MAX_CURRENT else KEY_MAX_CURRENT
        other = current_to_amperes(self.coordinator.data.get(other_key))
        if self._key == KEY_MAX_CURRENT and other is not None and value < other:
            raise HomeAssistantError(
                "maximum_below_minimum",
                translation_domain="evbox_g4_ble",
                translation_key="maximum_below_minimum",
            )
        if self._key == KEY_MIN_CURRENT and other is not None and value > other:
            raise HomeAssistantError(
                "minimum_above_maximum",
                translation_domain="evbox_g4_ble",
                translation_key="minimum_above_maximum",
            )
        await self.coordinator.async_set_configuration(
            self._key, amperes_to_current(value)
        )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EVBoxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    address = entry.data[CONF_ADDRESS]
    async_add_supported_entities(
        hass,
        entry,
        async_add_entities,
        [
            EVBoxCurrentNumber(
                coordinator, address, KEY_MAX_CURRENT, "maximum_current"
            ),
            EVBoxCurrentNumber(
                coordinator, address, KEY_MIN_CURRENT, "minimum_current"
            ),
        ],
        lambda entity: entity._key in coordinator.data,
    )

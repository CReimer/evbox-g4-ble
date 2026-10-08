"""Free-form configuration controls for EVBox Elvi."""

from __future__ import annotations

from homeassistant.components.text import TextEntity
from .models import EVBoxConfigEntry
from .coordinator import EVBoxCoordinator
from homeassistant.core import HomeAssistant
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    APN_MAX_LENGTH,
    ASCII_NO_WHITESPACE_PATTERN,
    CONF_ADDRESS,
    KEY_APN_NAME,
    KEY_APN_USER,
    KEY_SERVER_URL,
    SERVER_URL_MAX_LENGTH,
    SERVER_URL_PATTERN,
)
from .entity import EVBoxEntity, async_add_supported_entities


PARALLEL_UPDATES = 0


class EVBoxConfigText(EVBoxEntity, TextEntity):
    _attr_native_min = 0
    _attr_native_max = 255
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: EVBoxCoordinator,
        address: str,
        key: str,
        translation_key: str,
    ) -> None:
        super().__init__(coordinator, address, key)
        self._attr_translation_key = translation_key
        if key == KEY_SERVER_URL:
            self._attr_native_max = SERVER_URL_MAX_LENGTH
            self._attr_pattern = SERVER_URL_PATTERN
        elif key in (KEY_APN_NAME, KEY_APN_USER):
            self._attr_native_max = APN_MAX_LENGTH
            self._attr_pattern = ASCII_NO_WHITESPACE_PATTERN

    @property
    def native_value(self) -> str | None:
        value = self.coordinator.data.get(self._key)
        return None if value is None else str(value)

    async def async_set_value(self, value: str) -> None:
        if self._key == KEY_SERVER_URL:
            await self.coordinator.async_set_server(value)
        else:
            await self.coordinator.async_set_configuration(self._key, value)


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
            EVBoxConfigText(coordinator, address, KEY_SERVER_URL, "server_url"),
            EVBoxConfigText(coordinator, address, KEY_APN_NAME, "apn_name"),
            EVBoxConfigText(coordinator, address, KEY_APN_USER, "apn_user"),
        ],
        lambda entity: entity._key in coordinator.data,
    )

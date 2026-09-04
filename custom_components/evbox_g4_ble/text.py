"""Free-form configuration controls for EVBox Elvi."""

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
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
from .entity import EVBoxEntity


class EVBoxConfigText(EVBoxEntity, TextEntity):
    _attr_native_min = 0
    _attr_native_max = 255
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, address: str, key: str, translation_key: str) -> None:
        super().__init__(coordinator, address, key)
        self._attr_translation_key = translation_key
        if key == KEY_SERVER_URL:
            self._attr_native_max = SERVER_URL_MAX_LENGTH
            self._attr_pattern = SERVER_URL_PATTERN
        elif key in (KEY_APN_NAME, KEY_APN_USER):
            self._attr_native_max = APN_MAX_LENGTH
            self._attr_pattern = ASCII_NO_WHITESPACE_PATTERN

    @property
    def native_value(self):
        value = self.coordinator.data.get(self._key)
        return "" if value is None else str(value)

    async def async_set_value(self, value: str) -> None:
        if self._key == KEY_SERVER_URL:
            await self.coordinator.async_set_server(value)
        else:
            await self.coordinator.async_set_configuration(self._key, value)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    address = entry.data[CONF_ADDRESS]
    async_add_entities(
        entity
        for entity in [
            EVBoxConfigText(coordinator, address, KEY_SERVER_URL, "server_url"),
            EVBoxConfigText(coordinator, address, KEY_APN_NAME, "apn_name"),
            EVBoxConfigText(coordinator, address, KEY_APN_USER, "apn_user"),
        ]
        if entity._key in coordinator.data
    )

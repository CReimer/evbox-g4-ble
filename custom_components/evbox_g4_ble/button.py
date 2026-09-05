"""Action buttons for EVBox Elvi."""

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ADDRESS
from .entity import EVBoxEntity


class EVBoxButton(EVBoxEntity, ButtonEntity):
    def __init__(self, coordinator, address: str, key: str, translation_key: str) -> None:
        super().__init__(coordinator, address, key)
        self._attr_translation_key = translation_key
        self._attr_entity_category = EntityCategory.DIAGNOSTIC if key == "refresh" else EntityCategory.CONFIG

    async def async_press(self) -> None:
        if self._key == "identify":
            await self.coordinator.client.evb("evbBTShow")
        elif self._key == "restart":
            await self.coordinator.client.ocpp("Reset", {"type": "Hard"})
            self.coordinator.note_restart_sent()
        elif self._key == "refresh":
            await self.coordinator.async_request_refresh()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    address = entry.data[CONF_ADDRESS]
    async_add_entities(
        [
            EVBoxButton(coordinator, address, "identify", "identify"),
            EVBoxButton(coordinator, address, "restart", "restart"),
            EVBoxButton(coordinator, address, "refresh", "refresh"),
        ]
    )

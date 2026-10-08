"""Action buttons for EVBox Elvi."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from .models import EVBoxConfigEntry
from .coordinator import EVBoxCoordinator
from homeassistant.core import HomeAssistant
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ADDRESS
from .entity import EVBoxEntity
from .errors import async_device_errors, async_refresh_or_raise


PARALLEL_UPDATES = 0


class EVBoxButton(EVBoxEntity, ButtonEntity):
    def __init__(
        self,
        coordinator: EVBoxCoordinator,
        address: str,
        key: str,
        translation_key: str,
    ) -> None:
        super().__init__(coordinator, address, key)
        self._attr_translation_key = translation_key
        self._attr_entity_category = (
            EntityCategory.DIAGNOSTIC if key == "refresh" else EntityCategory.CONFIG
        )

    async def async_press(self) -> None:
        async with async_device_errors(self.coordinator):
            await self._async_press()

    async def _async_press(self) -> None:
        if self._key == "identify":
            await self.coordinator.client.evb("evbBTShow")
        elif self._key == "restart":
            await self.coordinator.client.ocpp("Reset", {"type": "Hard"})
            self.coordinator.note_restart_sent()
        elif self._key == "refresh":
            await async_refresh_or_raise(self.coordinator)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EVBoxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    address = entry.data[CONF_ADDRESS]
    async_add_entities(
        [
            EVBoxButton(coordinator, address, "identify", "identify"),
            EVBoxButton(coordinator, address, "restart", "restart"),
            EVBoxButton(coordinator, address, "refresh", "refresh"),
        ]
    )

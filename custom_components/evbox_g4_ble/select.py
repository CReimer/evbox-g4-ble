"""Select controls exposed by the EVBox Connect app."""

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ADDRESS, KEY_AUTO_START, KEY_PHASE_ROTATION, KEY_USE_BACKEND, LED_LEVEL, LED_MODE
from .entity import EVBoxEntity
from .protocol import auto_start_configuration, auto_start_value, phase_rotation_configuration, phase_rotation_value


class EVBoxChargingModeSelect(EVBoxEntity, SelectEntity):
    """App choice between RFID authorization and automatic start."""

    _attr_options = ["rfid", "automatic_start"]
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, address: str) -> None:
        super().__init__(coordinator, address, "charging_mode")
        self._attr_translation_key = "charging_mode"

    @property
    def current_option(self):
        return auto_start_configuration(self.coordinator.data.get(KEY_AUTO_START))["mode"]

    async def async_select_option(self, option: str) -> None:
        auto_start = auto_start_configuration(
            self.coordinator.data.get(KEY_AUTO_START)
        )
        if (
            option == "automatic_start"
            and _backend_enabled(self.coordinator)
            and not auto_start["legacy"]
        ):
            card_ids = _card_ids(self.coordinator)
            if auto_start.get("card_id") not in card_ids:
                raise HomeAssistantError(
                    "Bei aktivem Lade-Backend zuerst eine Ladekarte unter "
                    "'Zuordnung automatischer Ladesitzungen' auswaehlen"
                )
        value = auto_start_value(self.coordinator.data.get(KEY_AUTO_START), option)
        await self.coordinator.async_set_auto_start(value)


class EVBoxAutoStartCardSelect(EVBoxEntity, SelectEntity):
    """Assign automatic sessions to a stored card, as offered by the app."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, address: str) -> None:
        super().__init__(coordinator, address, "auto_start_card")
        self._attr_translation_key = "auto_start_card"

    @property
    def options(self) -> list[str]:
        return _card_ids(self.coordinator)

    @property
    def current_option(self):
        value = str(self.coordinator.data.get(KEY_AUTO_START, "")).strip()
        if not value or value.lower() == "false":
            return None
        if value.lower() == "true" or value == "999999":
            return None
        return value

    @property
    def available(self) -> bool:
        return (
            super().available
            and _backend_enabled(self.coordinator)
            and _supports_card_assignment(self.coordinator)
            and bool(self.options)
        )

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_auto_start(option)


class EVBoxPhaseRotationSelect(EVBoxEntity, SelectEntity):
    """Phase order for the Elvi's physical connector (OCPP connector 1)."""

    # Only the six physical phase orders are useful settings. OCPP's
    # Unknown/NotApplicable values may be reported by firmware but are not
    # phase rotations a user should be offered for writing.
    _attr_options = ["RST", "RTS", "SRT", "STR", "TRS", "TSR"]
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, address: str) -> None:
        super().__init__(coordinator, address, KEY_PHASE_ROTATION)
        self._attr_translation_key = "phase_rotation"

    @property
    def current_option(self):
        value = phase_rotation_value(self.coordinator.data.get(KEY_PHASE_ROTATION))
        return value if value in self.options else None

    async def async_select_option(self, option: str) -> None:
        value = phase_rotation_configuration(self.coordinator.data.get(KEY_PHASE_ROTATION), option)
        await self.coordinator.async_set_configuration(KEY_PHASE_ROTATION, value)


def _backend_enabled(coordinator) -> bool:
    value = coordinator.data.get(KEY_USE_BACKEND)
    return value is True or str(value).lower() == "true"


def _card_ids(coordinator) -> list[str]:
    result: list[str] = []
    for card in coordinator.data.get("cards", []):
        card_id = card.get("id_tag") or card.get("idTag")
        if card_id and str(card_id) not in result:
            result.append(str(card_id))
    return result


def _supports_card_assignment(coordinator) -> bool:
    """Only current AutoStart firmware stores a card ID; legacy stores bool."""
    return not auto_start_configuration(
        coordinator.data.get(KEY_AUTO_START)
    )["legacy"]


class EVBoxLEDModeSelect(EVBoxEntity, SelectEntity):
    """The idle LED modes exposed by the current EVBox Connect app."""

    _attr_options = ["off", "on"]
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, address: str) -> None:
        super().__init__(coordinator, address, LED_MODE)
        self._attr_translation_key = "led_mode"

    @property
    def current_option(self):
        value = str(self.coordinator.data.get(LED_MODE, "")).lower()
        return value if value in self.options else None

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_led(mode=option.title())


class EVBoxLEDLevelSelect(EVBoxEntity, SelectEntity):
    """The four brightness levels exposed by the EVBox Connect app."""

    _LEVELS = {"subtle": 5, "moderate": 25, "high": 50, "intense": 75}
    _attr_options = list(_LEVELS)
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, address: str) -> None:
        super().__init__(coordinator, address, LED_LEVEL)
        self._attr_translation_key = "led_brightness"

    @property
    def current_option(self):
        value = self.coordinator.data.get(LED_LEVEL)
        return next((name for name, level in self._LEVELS.items() if level == value), None)

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_led(level=self._LEVELS[option])


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        entity
        for entity in [
            EVBoxChargingModeSelect(entry.runtime_data, entry.data[CONF_ADDRESS]),
            EVBoxAutoStartCardSelect(entry.runtime_data, entry.data[CONF_ADDRESS]),
            EVBoxPhaseRotationSelect(entry.runtime_data, entry.data[CONF_ADDRESS]),
            EVBoxLEDModeSelect(entry.runtime_data, entry.data[CONF_ADDRESS]),
            EVBoxLEDLevelSelect(entry.runtime_data, entry.data[CONF_ADDRESS]),
        ]
        if (
            entity._key in entry.runtime_data.data
            or entity._key in ("charging_mode", "auto_start_card")
            and KEY_AUTO_START in entry.runtime_data.data
        )
        and (
            entity._key != "auto_start_card"
            or _supports_card_assignment(entry.runtime_data)
        )
    )

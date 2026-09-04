"""Switch controls for EVBox Elvi."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_ADDRESS,
    KEY_CCID,
    KEY_CCID_AC,
    KEY_METER_ADDRESS,
    KEY_USE_BACKEND,
)
from .entity import EVBoxEntity
from .protocol import ccid_ac_configuration, connector_value, meter_configuration, meter_configuration_value


def _as_bool(value):
    return value is True or str(value).lower() == "true"


def _ccid_ac_modifiable(coordinator) -> bool:
    raw = coordinator.data.get(KEY_CCID)
    ccid = connector_value(raw) or raw
    return "ccidv2tripeu" in str(ccid).replace(" ", "").lower()


class EVBoxConfigSwitch(EVBoxEntity, SwitchEntity):
    def __init__(self, coordinator, address: str, key: str, translation_key: str) -> None:
        super().__init__(coordinator, address, key)
        self._attr_translation_key = translation_key
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self):
        return _as_bool(self.coordinator.data.get(self._key))

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_configuration(self._key, True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_configuration(self._key, False)


class EVBoxCCIDACSwitch(EVBoxEntity, SwitchEntity):
    """App installer control for AC residual-current detection."""

    def __init__(self, coordinator, address: str) -> None:
        super().__init__(coordinator, address, KEY_CCID_AC)
        self._attr_translation_key = "ccid_ac_enabled"
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self):
        return (
            ccid_ac_configuration(self.coordinator.data.get(KEY_CCID_AC))["status"]
            == "enabled"
        )

    @property
    def available(self) -> bool:
        return super().available and _ccid_ac_modifiable(self.coordinator)

    async def _set(self, enabled: bool) -> None:
        current = str(self.coordinator.data.get(KEY_CCID_AC, "1.0"))
        connector_id = current.replace(" ", "").split(",", 1)[0].partition(".")[0] or "1"
        await self.coordinator.async_set_configuration(KEY_CCID_AC, f"{connector_id}.{'100' if enabled else '0'}")

    async def async_turn_on(self, **kwargs) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set(False)


class EVBoxConnectorMeterSwitch(EVBoxEntity, SwitchEntity):
    """Installer-app toggle selecting the connector's kWh meter."""

    def __init__(self, coordinator, address: str) -> None:
        super().__init__(coordinator, address, KEY_METER_ADDRESS)
        self._attr_translation_key = "connector_meter"
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self):
        return bool(meter_configuration(self.coordinator.data.get(KEY_METER_ADDRESS)).get("uses_connector"))

    async def _set(self, enabled: bool) -> None:
        value = meter_configuration_value(self.coordinator.data.get(KEY_METER_ADDRESS), enabled)
        await self.coordinator.async_set_configuration(KEY_METER_ADDRESS, value)

    async def async_turn_on(self, **kwargs) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set(False)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    address = entry.data[CONF_ADDRESS]
    async_add_entities(
        entity
        for entity in [
            EVBoxConfigSwitch(coordinator, address, KEY_USE_BACKEND, "use_backend"),
            EVBoxCCIDACSwitch(coordinator, address),
            EVBoxConnectorMeterSwitch(coordinator, address),
        ]
        if entity._key in coordinator.data
        and (
            not isinstance(entity, EVBoxCCIDACSwitch)
            or _ccid_ac_modifiable(coordinator)
        )
    )

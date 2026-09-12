"""Exercise the real Home Assistant entity classes with a mocked charger."""

from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock
from homeassistant.exceptions import HomeAssistantError
from custom_components.evbox_g4_ble import (
    binary_sensor,
    button,
    number,
    select,
    sensor,
    switch,
    text,
)
from custom_components.evbox_g4_ble.const import (
    CONF_ADDRESS,
    KEY_AUTO_START,
    KEY_BOOT_INFO,
    KEY_CCID,
    KEY_CCID_AC,
    KEY_MAX_CURRENT,
    KEY_MIN_CURRENT,
    KEY_METER_ADDRESS,
    KEY_PHASE_ROTATION,
    KEY_USE_BACKEND,
    KEY_SERVER_URL,
    KEY_APN_NAME,
    KEY_APN_USER,
    KEY_RF_MODULES,
    LED_MODE,
    LED_LEVEL,
)


def coordinator():
    return NS(
        data={},
        config_entry=None,
        last_update_success=True,
        client=NS(evb=AsyncMock(), ocpp=AsyncMock()),
        note_restart_sent=Mock(),
        async_request_refresh=AsyncMock(),
        async_set_configuration=AsyncMock(),
        async_set_server=AsyncMock(),
        async_set_auto_start=AsyncMock(),
        async_set_led=AsyncMock(),
    )


class EntityTests(unittest.IsolatedAsyncioTestCase):
    async def test_binary_sensors_and_buttons(self):
        co = coordinator()
        entry = NS(runtime_data=co, data={CONF_ADDRESS: "AA"})
        entities = []
        await binary_sensor.async_setup_entry(NS(), entry, entities.extend)
        self.assertTrue(entities[0].is_on)
        self.assertFalse(entities[1].is_on)
        co.data["restart_required"] = True
        self.assertTrue(entities[1].is_on)
        entities.clear()
        await button.async_setup_entry(NS(), entry, entities.extend)
        for entity in entities:
            await entity.async_press()
        co.client.evb.assert_awaited_once_with("evbBTShow")
        co.client.ocpp.assert_awaited_once_with("Reset", {"type": "Hard"})
        co.note_restart_sent.assert_called_once()
        co.async_request_refresh.assert_awaited_once()
        await button.EVBoxButton(co, "AA", "unknown", "unknown").async_press()
        self.assertEqual(entities[0].unique_id, "AA_identify")
        self.assertEqual(entities[0].device_info["manufacturer"], "EVBox")

    async def test_current_limits_validate_against_other_bound(self):
        co = coordinator()
        entry = NS(runtime_data=co, data={CONF_ADDRESS: "AA"})
        entities = []
        await number.async_setup_entry(NS(), entry, entities.extend)
        self.assertEqual(entities, [])
        co.data.update({KEY_MIN_CURRENT: "60", KEY_MAX_CURRENT: "320"})
        await number.async_setup_entry(NS(), entry, entities.extend)
        maximum, minimum = entities
        self.assertEqual(maximum.native_value, 32)
        self.assertEqual(minimum.native_value, 6)
        self.assertEqual(maximum.native_min_value, 6)
        self.assertEqual(minimum.native_max_value, 32)
        self.assertEqual(minimum.native_min_value, 6)
        self.assertEqual(maximum.native_max_value, 32)
        for entity, value in [(maximum, 5), (minimum, 33)]:
            with self.assertRaises(HomeAssistantError):
                await entity.async_set_native_value(value)
        await maximum.async_set_native_value(16)
        co.async_set_configuration.assert_awaited_once_with(KEY_MAX_CURRENT, 160)
        co.data.clear()
        self.assertEqual(maximum.native_min_value, 6)
        self.assertEqual(minimum.native_max_value, 32)
        await minimum.async_set_native_value(6)

    async def test_switch_readback_and_commands(self):
        co = coordinator()
        co.data.update(
            {
                KEY_USE_BACKEND: True,
                KEY_CCID: "1.CCIDV2TRIPEU",
                KEY_CCID_AC: "1.100",
                KEY_METER_ADDRESS: "1.0",
            }
        )
        entities = []
        entry = NS(runtime_data=co, data={CONF_ADDRESS: "AA"})
        await switch.async_setup_entry(NS(), entry, entities.extend)
        self.assertEqual(len(entities), 3)
        for entity in entities:
            self.assertIsInstance(entity.is_on, bool)
            await entity.async_turn_on()
            await entity.async_turn_off()
        self.assertTrue(entities[1].available)
        co.data[KEY_CCID] = "other"
        self.assertFalse(entities[1].available)
        co.data[KEY_CCID_AC] = ".0"
        await entities[1].async_turn_on()
        co.async_set_configuration.assert_awaited_with(KEY_CCID_AC, "1.100")
        entities.clear()
        await switch.async_setup_entry(NS(), entry, entities.extend)
        self.assertEqual(len(entities), 2)
        self.assertFalse(switch._as_bool("false"))
        self.assertTrue(switch._as_bool("TRUE"))

    async def test_text_controls_dispatch_and_constraints(self):
        co = coordinator()
        co.data.update(
            {
                KEY_SERVER_URL: "wss://example.org",
                KEY_APN_NAME: None,
                KEY_APN_USER: "user",
            }
        )
        entities = []
        await text.async_setup_entry(
            NS(), NS(runtime_data=co, data={CONF_ADDRESS: "AA"}), entities.extend
        )
        self.assertEqual(
            [entity.native_value for entity in entities],
            ["wss://example.org", "", "user"],
        )
        for entity in entities:
            await entity.async_set_value("value")
        co.async_set_server.assert_awaited_once_with("value")
        self.assertEqual(co.async_set_configuration.await_count, 2)
        generic = text.EVBoxConfigText(co, "AA", "other", "other")
        self.assertEqual(generic.native_value, "")

    async def test_selects_card_assignment_led_and_phase(self):
        co = coordinator()
        co.data.update(
            {
                KEY_AUTO_START: "999999",
                KEY_USE_BACKEND: True,
                KEY_PHASE_ROTATION: "1.RST",
                LED_MODE: "On",
                LED_LEVEL: 25,
                "cards": [
                    {"id_tag": "card"},
                    {"idTag": "card"},
                    {},
                    {"idTag": "second"},
                ],
            }
        )
        entities = []
        entry = NS(runtime_data=co, data={CONF_ADDRESS: "AA"})
        await select.async_setup_entry(NS(), entry, entities.extend)
        mode, card, phase, led, level = entities
        self.assertEqual(mode.current_option, "automatic_start")
        self.assertEqual(card.options, ["card", "second"])
        self.assertIsNone(card.current_option)
        self.assertTrue(card.available)
        with self.assertRaises(HomeAssistantError):
            await mode.async_select_option("automatic_start")
        co.data[KEY_AUTO_START] = "card"
        self.assertEqual(card.current_option, "card")
        await mode.async_select_option("automatic_start")
        await mode.async_select_option("rfid")
        await card.async_select_option("second")
        co.async_set_auto_start.assert_awaited_with("second")
        self.assertEqual(phase.current_option, "RST")
        await phase.async_select_option("STR")
        co.async_set_configuration.assert_awaited_with(KEY_PHASE_ROTATION, "1.STR")
        self.assertEqual(led.current_option, "on")
        await led.async_select_option("off")
        co.async_set_led.assert_awaited_with(mode="Off")
        self.assertEqual(level.current_option, "moderate")
        await level.async_select_option("high")
        co.async_set_led.assert_awaited_with(level=50)
        for value in ("", "false", "true", "999999"):
            co.data[KEY_AUTO_START] = value
            self.assertIsNone(card.current_option)
        co.data.update(
            {KEY_PHASE_ROTATION: "1.Unknown", LED_MODE: "bad", LED_LEVEL: 99}
        )
        self.assertIsNone(phase.current_option)
        self.assertIsNone(led.current_option)
        self.assertIsNone(level.current_option)
        co.data[KEY_AUTO_START] = "true"
        entities.clear()
        await select.async_setup_entry(NS(), entry, entities.extend)
        self.assertFalse(any(entity._key == "auto_start_card" for entity in entities))
        co.data.clear()
        entities.clear()
        await select.async_setup_entry(NS(), entry, entities.extend)
        self.assertEqual(entities, [])

    async def test_sensor_values_attributes_and_supported_filter(self):
        co = coordinator()
        co.data.update(
            {
                "cards": [{}],
                "connection_info": {
                    "current_connection": "Wi-Fi",
                    "wifi": {"signal_strength": -50, "ssid": "wifi"},
                    "cellular": {"signal_strength": -70},
                },
                KEY_BOOT_INFO: {
                    "firmwareVersion": "1.2",
                    "chargePointModel": "Elvi",
                    "chargePointSerialNumber": "serial",
                },
                "wifi_status": {"status": 3},
                "wifi_network": {"ssid": "wifi"},
                KEY_RF_MODULES: [],
            }
        )
        entry = NS(runtime_data=co, data={CONF_ADDRESS: "AA"})
        for desc in sensor.DESCRIPTIONS:
            entity = sensor.EVBoxSensor(co, "AA", desc)
            # Every description has a concrete, stable HA state type.
            self.assertIsInstance(entity.native_value, (str, int, type(None)))
            self.assertIsInstance(entity.extra_state_attributes, (dict, type(None)))
            saved = co.data[desc.value_key]
            co.data[desc.value_key] = None
            self.assertIsInstance(entity.native_value, (str, int, type(None)))
            self.assertIsInstance(entity.extra_state_attributes, (dict, type(None)))
            co.data[desc.value_key] = saved
        active = sensor.EVBoxSensor(co, "AA", sensor.DESCRIPTIONS[5])
        self.assertEqual(active.native_value, "wifi")
        co.data["connection_info"]["current_connection"] = "unexpected"
        self.assertEqual(active.native_value, "unknown")
        for value in (
            co.data.copy(),
            {},
            {desc.value_key: {} for desc in sensor.DESCRIPTIONS},
            {desc.value_key: "bad" for desc in sensor.DESCRIPTIONS},
        ):
            co.data = value
            entities = []
            await sensor.async_setup_entry(NS(), entry, entities.extend)
            self.assertTrue(
                all(entity.unique_id.startswith("AA_") for entity in entities)
            )
        generic = sensor.EVBoxSensor(
            co, "AA", sensor.EVBoxSensorDescription(key="generic", value_key="raw")
        )
        co.data["raw"] = {"hello": "world"}
        self.assertEqual(generic.native_value, "{'hello': 'world'}")
        self.assertIsNone(generic.extra_state_attributes)
        co.data["raw"] = "plain"
        self.assertEqual(generic.native_value, "plain")

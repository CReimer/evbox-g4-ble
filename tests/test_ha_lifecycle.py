"""Real framework lifecycle, service routing and authoritative state tests."""

from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed
from custom_components import evbox_g4_ble as integration
from custom_components.evbox_g4_ble import coordinator as c
from custom_components.evbox_g4_ble.const import (
    CONF_ADDRESS,
    CONF_SECURITY_CODE,
    KEY_USE_BACKEND,
    KEY_APN_NAME,
    KEY_SERVER_URL,
    KEY_RF_MODULES,
    KEY_AUTO_START,
    LED_LEVEL,
)
from custom_components.evbox_g4_ble.diagnostics import (
    async_get_config_entry_diagnostics,
)
from tests.test_ha_platforms import coordinator


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_registers_callable_services_and_cleans_up(self):
        co = coordinator()
        entry = NS(
            entry_id="entry",
            runtime_data=co,
            data={CONF_ADDRESS: "AA", CONF_SECURITY_CODE: "secret"},
            title="Charger",
        )
        hass = NS(
            services=NS(async_register=Mock()),
            bus=NS(async_listen_once=Mock()),
            config_entries=NS(
                async_entries=Mock(return_value=[entry]),
                async_forward_entry_setups=AsyncMock(),
                async_unload_platforms=AsyncMock(return_value=True),
            ),
        )
        co.async_config_entry_first_refresh = AsyncMock()
        with patch.object(
            integration, "async_cleanup_firmware_proxies", AsyncMock()
        ) as cleanup:
            self.assertTrue(await integration.async_setup(hass, {}))
            self.assertEqual(
                hass.services.async_register.call_count,
                len(integration.SERVICE_SCHEMAS),
            )
            call = NS(service="refresh", data={})
            result = await hass.services.async_register.call_args.args[2](call)
            self.assertEqual(result, co.data)
            await hass.bus.async_listen_once.call_args.args[1](None)
            self.assertEqual(cleanup.await_count, 2)
        registry = NS(
            async_get_entity_id=Mock(side_effect=["text.old"] + [None] * 19),
            async_remove=Mock(),
        )
        with (
            patch.object(integration, "EVBoxClient", return_value=co.client),
            patch.object(integration, "EVBoxCoordinator", return_value=co),
            patch.object(integration.er, "async_get", return_value=registry),
        ):
            self.assertTrue(await integration.async_setup_entry(hass, entry))
            registry.async_remove.assert_called_once_with("text.old")
        self.assertTrue(await integration.async_unload_entry(hass, entry))
        diag = await async_get_config_entry_diagnostics(hass, entry)
        self.assertNotIn("secret", str(diag))
        self.assertEqual(diag["data"], {})
        for entries, data in (
            ([], {}),
            ([entry], {"entry_id": "missing"}),
            ([NS(runtime_data=None)], {}),
        ):
            hass.config_entries.async_entries.return_value = entries
            with self.assertRaises(HomeAssistantError):
                integration._coordinator(hass, NS(data=data))

    async def test_services_dispatch_and_validate_capabilities(self):
        co = coordinator()
        co.data.update(
            {
                KEY_USE_BACKEND: True,
                KEY_APN_NAME: "",
                KEY_SERVER_URL: "",
                "led_idle": "",
                "cards": [],
                KEY_RF_MODULES: "ChargeBox.11",
            }
        )
        co.client.scan_satellites = AsyncMock(return_value=["11"])
        co.client.connection_info = AsyncMock(return_value={})
        co.client.set_configuration = AsyncMock(return_value=True)
        co.async_set_rf_modules = AsyncMock()
        for name in (
            "async_set_apn",
            "async_add_card",
            "async_remove_card",
            "async_clear_cards",
        ):
            setattr(co, name, AsyncMock())
        hass = NS(
            config_entries=NS(
                async_entries=Mock(return_value=[NS(entry_id="entry", runtime_data=co)])
            )
        )
        cases = [
            ("scan_wifi", {}),
            ("set_apn", {"apn": "internet"}),
            ("set_server", {"url": "wss://example.org/"}),
            ("set_led_idle", {"mode": "On", "level": 25}),
            ("rfid_add", {"id_tag": "AA"}),
            ("rfid_remove", {"id_tag": "AA"}),
            ("rfid_clear", {}),
            ("scan_satellites", {}),
            ("pair_satellite", {"satellite_id": "12"}),
            ("pair_satellite", {"satellite_id": "11"}),
            ("blink_satellite", {"satellite_id": "11"}),
            ("connection_info", {}),
            ("identify", {}),
        ]
        for service, data in cases:
            with self.subTest(service=service):
                result = await integration._handle_service(
                    hass, NS(service=service, data={"entry_id": "entry", **data})
                )
                self.assertIsInstance(result, dict)
        co.data[KEY_RF_MODULES] = ",".join(f"ChargeBox.{n}" for n in range(10))
        with self.assertRaises(HomeAssistantError):
            await integration._handle_service(
                hass, NS(service="pair_satellite", data={"satellite_id": "99"})
            )
        with self.assertRaises(HomeAssistantError):
            await integration._handle_service(hass, NS(service="unsupported", data={}))
        with (
            patch.object(integration, "valid_internet_connection", return_value=True),
            patch.object(
                integration,
                "async_start_firmware_update",
                AsyncMock(return_value=("accepted", True)),
            ),
        ):
            self.assertEqual(
                await integration._handle_service(
                    hass,
                    NS(service="update_firmware", data={"url": "https://example.org/"}),
                ),
                {"result": "accepted", "proxied_via_ftp": True},
            )


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = NS(
            get_configuration=AsyncMock(return_value={}),
            session=AsyncMock(
                return_value=[
                    "2",
                    "net",
                    "On,00:00:00Z,23:59:59Z,25",
                    "{AA,0}",
                    {"ip": "x"},
                ]
            ),
            evb=AsyncMock(),
            ocpp=AsyncMock(return_value={}),
            set_configuration=AsyncMock(),
            set_server=AsyncMock(),
            set_auto_start=AsyncMock(),
        )
        with patch.object(c.DataUpdateCoordinator, "__init__", return_value=None):
            self.co = c.EVBoxCoordinator(NS(), self.client)
        self.co.data = {}
        self.co.async_set_updated_data = lambda data: setattr(self.co, "data", data)
        self.co.async_request_refresh = AsyncMock()

    async def test_refresh_and_verified_commands(self):
        data = await self.co._async_update_data()
        self.assertEqual(data["cards"], [{"id_tag": "AA"}])
        self.assertEqual(data["led_level"], 25)
        self.client.get_configuration.side_effect = RuntimeError("offline")
        with self.assertRaises(UpdateFailed):
            await self.co._async_update_data()
        self.client.get_configuration.side_effect = None
        await self.co.async_command("identify")
        self.co.async_request_refresh.assert_awaited_once()
        self.client.get_configuration.return_value = {
            KEY_SERVER_URL: "wss://example.org/"
        }
        await self.co.async_set_server("wss://example.org/")
        self.client.get_configuration.return_value = {KEY_AUTO_START: "true"}
        await self.co.async_set_auto_start(True)
        self.assertEqual(self.co.data[KEY_AUTO_START], "true")
        for stored in ({}, {KEY_RF_MODULES: "ChargeBox.99"}):
            self.client.get_configuration.return_value = stored
            with self.assertRaises(HomeAssistantError):
                await self.co.async_set_rf_modules("ChargeBox.11")
        self.client.evb.return_value = "On,00:00:00Z,23:59:59Z,25"
        await self.co.async_set_led()
        self.assertEqual(self.co.data[LED_LEVEL], 25)

    async def test_card_add_clear_and_failed_verification(self):
        self.co.async_card_ids = AsyncMock(side_effect=[["AA"], ["AA", "BB"]])
        await self.co.async_add_card(" bb ")
        self.assertEqual(self.co.data["cards"], [{"id_tag": "AA"}, {"id_tag": "BB"}])
        self.co.async_card_ids = AsyncMock(return_value=["AA"])
        with self.assertRaises(HomeAssistantError):
            await self.co.async_add_card("AA")
        with self.assertRaises(HomeAssistantError):
            await self.co.async_add_card("BB")
        with self.assertRaises(HomeAssistantError):
            await self.co._async_replace_cards([])
        self.client.ocpp.return_value = None
        self.co.async_card_ids.return_value = []
        await self.co.async_clear_cards()
        self.assertEqual(self.co.data["cards"], [])

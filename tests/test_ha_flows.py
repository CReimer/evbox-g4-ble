"""Real HA flows: discovery, installer forms and charger failure handling."""

from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, PropertyMock, patch
from custom_components.evbox_g4_ble import config_flow as f
from custom_components.evbox_g4_ble.const import (
    CONF_SECURITY_CODE,
    SERVICE_UUID,
    KEY_USE_BACKEND,
    KEY_APN_NAME,
    KEY_RF_MODULES,
    KEY_SERVER_URL,
)
from tests.test_ha_platforms import coordinator


class ConfigTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_and_manual_authentication(self):
        flow = f.EVBoxConfigFlow()
        flow.hass = NS()
        flow.context = {}
        self.assertIsInstance(flow.async_get_options_flow(None), f.EVBoxOptionsFlow)
        self.assertEqual(
            (await flow.async_step_bluetooth(NS(service_uuids=[])))["reason"],
            "not_supported",
        )
        with (
            patch.object(flow, "async_set_unique_id", AsyncMock()) as unique,
            patch.object(flow, "_abort_if_unique_id_configured"),
            patch.object(f, "EVBoxClient", return_value=NS(evb=AsyncMock())) as client,
            patch.object(
                f.bluetooth,
                "async_ble_device_from_address",
                return_value=NS(name="Charger"),
            ),
        ):
            self.assertEqual((await flow.async_step_user())["step_id"], "user")
            result = await flow.async_step_user(
                {"address": "AA", CONF_SECURITY_CODE: "code"}
            )
            self.assertEqual(result["title"], "Charger")
            self.assertEqual(result["data"]["address"], "AA")
            unique.assert_awaited_with("AA", raise_on_progress=False)
            result = await flow.async_step_bluetooth(
                NS(service_uuids=[SERVICE_UUID.upper()], address="AA", name=None)
            )
            self.assertEqual(result["step_id"], "user")
            client.return_value.evb.side_effect = RuntimeError("offline")
            self.assertEqual(
                (await flow.async_step_user({CONF_SECURITY_CODE: "code"}))["errors"],
                {"base": "cannot_connect"},
            )
        self.assertFalse(f._valid_text(None, ".*"))
        self.assertFalse(f._valid_text("long", ".*", maximum=2))


class OptionsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.co = coordinator()
        self.co.async_set_updated_data = Mock()
        self.co.async_card_ids = AsyncMock(return_value=[])
        self.co.async_add_card = AsyncMock()
        self.co.async_remove_card = AsyncMock()
        self.co.async_clear_cards = AsyncMock()
        self.co.async_set_apn = AsyncMock()
        self.co.async_set_rf_modules = AsyncMock()
        self.co.client.set_wifi = AsyncMock(return_value={})
        self.co.client.scan_satellites = AsyncMock(return_value=[])
        self.co.client.set_configuration = AsyncMock()
        self.flow = f.EVBoxOptionsFlow()
        self.flow.hass = NS()
        self.flow.context = {}
        entry = NS(runtime_data=self.co, options={"keep": True})
        replacement = patch.object(
            f.EVBoxOptionsFlow, "config_entry", PropertyMock(return_value=entry)
        )
        replacement.start()
        self.addCleanup(replacement.stop)

    async def test_menus_and_empty_forms(self):
        self.assertEqual(
            (await self.flow.async_step_init())["menu_options"], ["firmware"]
        )
        self.co.data.update(
            {"cards": [], KEY_USE_BACKEND: True, KEY_APN_NAME: "", KEY_RF_MODULES: ""}
        )
        self.assertEqual(
            (await self.flow.async_step_init())["menu_options"],
            ["rfid", "wifi", "backend", "apn", "satellites", "firmware"],
        )
        for step in ("wifi", "rfid", "satellites"):
            self.assertEqual(
                (await getattr(self.flow, "async_step_" + step)())["step_id"], step
            )
        for step in (
            "wifi_manual",
            "wifi_clear",
            "rfid_add",
            "rfid_clear",
            "apn",
            "backend",
            "satellite_scan",
            "satellite_pair",
        ):
            self.assertEqual(
                (await getattr(self.flow, "async_step_" + step)())["step_id"], step
            )
        for step, error in [
            ("rfid_remove", "no_rfid_cards"),
            ("satellite_scan_results", "no_satellites_found"),
            ("satellite_unpair", "no_paired_satellites"),
            ("satellite_identify", "no_paired_satellites"),
            ("firmware", "no_internet"),
        ]:
            self.assertEqual(
                (await getattr(self.flow, "async_step_" + step)())["errors"]["base"],
                error,
            )

    async def test_wifi_scan_and_manual_error_mapping(self):
        self.co.client.evb.return_value = []
        self.assertEqual(
            (await self.flow.async_step_wifi_connect())["errors"]["base"],
            "no_wifi_networks",
        )
        self.co.client.evb.side_effect = RuntimeError("scan")
        self.assertEqual(
            (await self.flow.async_step_wifi_connect())["errors"]["base"],
            "wifi_scan_failed",
        )
        self.flow._wifi_networks = [
            {"ssid": "wifi", "authentication": "wpa", "signal_strength": -50}
        ]
        self.assertEqual(
            (await self.flow.async_step_wifi_connect({"network": "0"}))["errors"],
            {"password": "password_required"},
        )
        self.assertEqual(
            (
                await self.flow.async_step_wifi_manual(
                    {"ssid": "wifi", "security": "wpa"}
                )
            )["errors"],
            {"password": "password_required"},
        )
        for status, error in [
            ("wrong_password", "wrong_wifi_password"),
            ("disconnected", "wifi_connection_failed"),
            ("unknown", "wifi_connection_failed"),
            ("connecting", None),
            ("connected", None),
        ]:
            with patch.object(f, "wifi_status", return_value={"status": status}):
                for step, data in [
                    ("wifi_connect", {"network": "0", "password": "p"}),
                    (
                        "wifi_manual",
                        {"ssid": " wifi ", "security": "wpa", "password": "p"},
                    ),
                ]:
                    result = await getattr(self.flow, "async_step_" + step)(data)
                    if error:
                        self.assertIn(error, result["errors"].values())
                    else:
                        self.assertEqual(result["data"], {"keep": True})
        self.co.async_request_refresh.assert_not_awaited()
        self.co.client.set_wifi.side_effect = RuntimeError("write")
        for step, data in [
            ("wifi_connect", {"network": "0", "password": "p"}),
            ("wifi_manual", {"ssid": "wifi", "security": "open"}),
        ]:
            self.assertEqual(
                (await getattr(self.flow, "async_step_" + step)(data))["errors"][
                    "base"
                ],
                "cannot_connect",
            )

    async def test_confirmed_clear_and_rfid_mutations(self):
        for step, method in [
            ("wifi_clear", self.co.client.evb),
            ("rfid_clear", self.co.async_clear_cards),
        ]:
            self.assertEqual(
                (await getattr(self.flow, "async_step_" + step)({"confirm": True}))[
                    "data"
                ],
                {"keep": True},
            )
            method.side_effect = RuntimeError("clear")
            self.assertEqual(
                (await getattr(self.flow, "async_step_" + step)({"confirm": True}))[
                    "errors"
                ]["base"],
                "cannot_connect",
            )
            method.side_effect = None
        self.assertEqual(
            (await self.flow.async_step_rfid_add({"id_tag": "bad space"}))["errors"][
                "id_tag"
            ],
            "invalid_rfid_id",
        )
        self.assertEqual(
            (await self.flow.async_step_rfid_add({"id_tag": "0123456789ABCDEF"}))[
                "data"
            ],
            {"keep": True},
        )
        self.co.async_add_card.assert_awaited_once_with("0123456789ABCDEF")
        self.co.async_card_ids.return_value = ["0123456789ABCDEF"]
        self.assertEqual(
            (await self.flow.async_step_rfid_add({"id_tag": "0123456789ABCDEF"}))[
                "errors"
            ]["id_tag"],
            "rfid_card_exists",
        )
        self.co.async_card_ids.side_effect = RuntimeError("read")
        self.assertEqual(
            (await self.flow.async_step_rfid_add({"id_tag": "0123456789ABCDEF"}))[
                "errors"
            ]["base"],
            "cannot_connect",
        )
        with self.assertRaises(ValueError):
            await self.flow._send_card("card", "Blocked")
        self.co.data["cards"] = [{"id_tag": "card"}, {}]
        self.assertEqual(
            (await self.flow.async_step_rfid_remove())["step_id"], "rfid_remove"
        )
        self.assertEqual(
            (await self.flow.async_step_rfid_remove({"id_tag": "card"}))["data"],
            {"keep": True},
        )
        self.co.async_remove_card.side_effect = RuntimeError("delete")
        self.assertEqual(
            (await self.flow.async_step_rfid_remove({"id_tag": "card"}))["errors"][
                "base"
            ],
            "cannot_connect",
        )

    async def test_apn_and_backend_validation_and_write_failures(self):
        for step, data, method in [
            (
                "apn",
                {"apn": "internet", "username": "u", "password": "p"},
                self.co.async_set_apn,
            ),
            (
                "backend",
                {"online": True, "url": "wss://example.org/"},
                self.co.async_set_server,
            ),
        ]:
            self.assertEqual(
                (await getattr(self.flow, "async_step_" + step)(data))["data"],
                {"keep": True},
            )
            method.assert_awaited_once()
            method.side_effect = RuntimeError("write")
            self.assertEqual(
                (await getattr(self.flow, "async_step_" + step)(data))["errors"][
                    "base"
                ],
                "cannot_connect",
            )
        self.assertEqual(
            (await self.flow.async_step_apn({"apn": "bad space"}))["errors"]["apn"],
            "invalid_apn_value",
        )
        self.assertEqual(
            (await self.flow.async_step_backend({"online": True, "url": "invalid"}))[
                "errors"
            ]["url"],
            "invalid_backend_url",
        )
        self.assertEqual(
            (await self.flow.async_step_backend({"online": False}))["data"],
            {"keep": True},
        )
        self.co.data[KEY_SERVER_URL] = "wss://example.org/"
        self.assertEqual((await self.flow.async_step_backend())["step_id"], "backend")

    async def test_satellite_scan_pair_unpair_and_identify(self):
        item = {"type": "ChargeBox", "id": "12345678", "signal_strength": -50}
        self.co.client.scan_satellites.return_value = [item]
        self.assertEqual(
            (await self.flow.async_step_satellite_scan({"timeout": 10}))["step_id"],
            "satellite_scan_results",
        )
        self.assertEqual(
            (await self.flow.async_step_satellite_scan_results({"satellite": "0"}))[
                "data"
            ],
            {"keep": True},
        )
        self.co.async_set_rf_modules.assert_awaited_with("ChargeBox.12345678")
        self.co.client.scan_satellites.side_effect = RuntimeError("scan")
        self.assertEqual(
            (await self.flow.async_step_satellite_scan({"timeout": 10}))["errors"][
                "base"
            ],
            "cannot_connect",
        )
        self.assertEqual(
            (await self.flow.async_step_satellite_pair({"satellite_id": "bad"}))[
                "errors"
            ]["satellite_id"],
            "invalid_satellite_id",
        )
        self.assertEqual(
            (await self.flow.async_step_satellite_pair({"satellite_id": "12345678"}))[
                "data"
            ],
            {"keep": True},
        )
        self.co.data["rf_modules_parsed"] = [item, {}]
        self.assertEqual(
            self.flow._paired_satellite_payload(add=("ChargeBox", "12345678")),
            "ChargeBox.12345678",
        )
        for step, data in [
            ("satellite_unpair", {"satellite_id": "12345678"}),
            ("satellite_identify", {"satellite": "ChargeBox.12345678"}),
        ]:
            self.assertEqual(
                (await getattr(self.flow, "async_step_" + step)())["step_id"], step
            )
            self.assertEqual(
                (await getattr(self.flow, "async_step_" + step)(data))["data"],
                {"keep": True},
            )
        self.co.async_set_rf_modules.assert_awaited_with("")
        for step, data, method in [
            (
                "satellite_pair",
                {"satellite_id": "12345678"},
                self.co.async_set_rf_modules,
            ),
            (
                "satellite_scan_results",
                {"satellite": "0"},
                self.co.async_set_rf_modules,
            ),
            (
                "satellite_unpair",
                {"satellite_id": "12345678"},
                self.co.async_set_rf_modules,
            ),
            (
                "satellite_identify",
                {"satellite": "ChargeBox.12345678"},
                self.co.client.set_configuration,
            ),
        ]:
            method.side_effect = RuntimeError("write")
            self.assertEqual(
                (await getattr(self.flow, "async_step_" + step)(data))["errors"][
                    "base"
                ],
                "cannot_connect",
            )
            method.side_effect = None
        self.co.data["rf_modules_parsed"] = [
            {"type": "ChargeBox", "id": str(i)} for i in range(10)
        ]
        for step, data in [
            ("satellite_pair", {"satellite_id": "12345678"}),
            ("satellite_scan_results", {"satellite": "0"}),
        ]:
            self.assertEqual(
                (await getattr(self.flow, "async_step_" + step)(data))["errors"][
                    "base"
                ],
                "max_satellites",
            )
            with patch.object(
                self.flow,
                "_paired_satellite_payload",
                side_effect=ValueError("unexpected"),
            ):
                with self.assertRaises(ValueError):
                    await getattr(self.flow, "async_step_" + step)(data)

    async def test_firmware_confirmation_and_failure(self):
        with (
            patch.object(f, "valid_internet_connection", return_value=True),
            patch.object(f, "async_start_firmware_update", AsyncMock()) as start,
        ):
            self.assertEqual(
                (await self.flow.async_step_firmware())["step_id"], "firmware"
            )
            self.assertEqual(
                (
                    await self.flow.async_step_firmware(
                        {"confirm": True, "url": "https://example.org/fw"}
                    )
                )["data"],
                {"keep": True},
            )
            start.assert_awaited_once()
            start.side_effect = RuntimeError("firmware")
            self.assertEqual(
                (
                    await self.flow.async_step_firmware(
                        {"confirm": True, "url": "https://example.org/fw"}
                    )
                )["errors"]["base"],
                "cannot_connect",
            )

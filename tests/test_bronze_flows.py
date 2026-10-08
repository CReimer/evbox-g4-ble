"""Bronze flow requirements: every error path must allow a retry."""

from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from custom_components.evbox_g4_ble import config_flow as f
from custom_components.evbox_g4_ble.const import CONF_SECURITY_CODE
from custom_components.evbox_g4_ble.client import EVBoxConnectionError
from tests import test_ha_flows


class FlowRecoveryTests(unittest.IsolatedAsyncioTestCase):
    setUp = test_ha_flows.OptionsTests.setUp

    async def test_each_options_authentication_failure_recovers_on_same_flow(self):
        self.flow._wifi_networks = [{"ssid": "network", "authentication": None}]
        self.flow._satellite_scan_results = [{"type": "ChargeBox", "id": "123"}]
        self.co.data.update(
            {
                "cards": [{"id_tag": "AA"}],
                "rf_modules_parsed": [{"type": "ChargeBox", "id": "123"}],
            }
        )
        cases = [
            ("wifi_connect", {"network": "0"}, self.co.client.set_wifi),
            (
                "wifi_manual",
                {"ssid": "network", "security": "open"},
                self.co.client.set_wifi,
            ),
            ("wifi_clear", {"confirm": True}, self.co.client.evb),
            ("rfid_add", {"id_tag": "BB"}, self.co.async_add_card),
            ("rfid_remove", {"id_tag": "AA"}, self.co.async_remove_card),
            ("rfid_clear", {"confirm": True}, self.co.async_clear_cards),
            ("apn", {"apn": "internet"}, self.co.async_set_apn),
            ("backend", {"online": False}, self.co.async_set_backend),
            ("satellite_scan", {"timeout": 40}, self.co.client.scan_satellites),
            (
                "satellite_scan_results",
                {"satellite": "0"},
                self.co.async_update_rf_modules,
            ),
            (
                "satellite_pair",
                {"satellite_id": "123"},
                self.co.async_update_rf_modules,
            ),
            (
                "satellite_unpair",
                {"satellite_id": "123"},
                self.co.async_update_rf_modules,
            ),
            (
                "satellite_identify",
                {"satellite": "ChargeBox.123"},
                self.co.client.set_configuration,
            ),
        ]
        with patch.object(f, "wifi_status", return_value={"status": "connected"}):
            for step, data, action in cases:
                for exception in (
                    f.EVBoxAuthError("rejected"),
                    ConfigEntryAuthFailed("rejected"),
                ):
                    with self.subTest(step=step, error=type(exception).__name__):
                        # Scanning may clear previous result choices; restore for pairing.
                        self.flow._satellite_scan_results = [
                            {"type": "ChargeBox", "id": "123"}
                        ]
                        action.side_effect = exception
                        result = await getattr(self.flow, "async_step_" + step)(data)
                        self.assertEqual(result["errors"]["base"], "invalid_auth")
                        action.side_effect = None
                        result = await getattr(self.flow, "async_step_" + step)(data)
                        self.assertNotEqual(
                            result.get("errors", {}).get("base"), "invalid_auth"
                        )
                        if step != "satellite_scan":
                            self.assertEqual(result["type"], "create_entry")
        self.assertEqual(
            self.flow.config_entry.async_start_reauth_if_available.call_count,
            len(cases) * 2,
        )

    async def test_wifi_scan_authentication_failure_recovers_to_selection(self):
        self.co.client.evb.side_effect = ConfigEntryAuthFailed("rejected")
        result = await self.flow.async_step_wifi_connect()
        self.assertEqual(result["errors"]["base"], "invalid_auth")
        self.co.client.evb.side_effect = None
        with patch.object(f, "wifi_scan_networks", return_value=[{"ssid": "network"}]):
            result = await self.flow.async_step_wifi_connect()
        self.assertEqual(result["errors"], {})
        self.assertIn("network", str(result["data_schema"]))

    async def test_unexpected_pairing_error_propagates_without_false_success(self):
        self.flow._satellite_scan_results = [{"type": "ChargeBox", "id": "123"}]
        for step, data in (
            ("satellite_scan_results", {"satellite": "0"}),
            ("satellite_pair", {"satellite_id": "123"}),
        ):
            self.co.async_update_rf_modules.side_effect = HomeAssistantError(
                "readback failed", translation_key="satellites_mismatch"
            )
            with self.assertRaises(HomeAssistantError):
                await getattr(self.flow, "async_step_" + step)(data)
            self.co.async_update_rf_modules.side_effect = None
            self.assertEqual(
                (await getattr(self.flow, "async_step_" + step)(data))["type"],
                "create_entry",
            )

    async def test_firmware_auth_and_untranslated_errors_recover_without_refresh(self):
        data = {"confirm": True, "url": "https://example.org/firmware.evb"}
        with (
            patch.object(f, "valid_internet_connection", return_value=True),
            patch.object(f, "async_start_firmware_update", AsyncMock()) as install,
        ):
            for error, key in (
                (ConfigEntryAuthFailed("wrong code"), "invalid_auth"),
                (HomeAssistantError("untranslated"), "cannot_connect"),
            ):
                install.side_effect = error
                self.assertEqual(
                    (await self.flow.async_step_firmware(data))["errors"]["base"], key
                )
                install.side_effect = None
                self.assertEqual(
                    (await self.flow.async_step_firmware(data))["type"], "create_entry"
                )
        self.co.async_request_refresh.assert_not_awaited()

    async def test_manual_setup_can_finish_without_advertised_device_name(self):
        for device in (None, NS(name=None)):
            flow = f.EVBoxConfigFlow()
            flow.hass = NS(config_entries=NS(async_entries=Mock(return_value=[])))
            flow.context = {}
            with (
                patch.object(flow, "async_set_unique_id", AsyncMock()),
                patch.object(flow, "_abort_if_unique_id_configured"),
                patch.object(f, "EVBoxClient", return_value=NS(evb=AsyncMock())),
                patch.object(
                    f.bluetooth, "async_ble_device_from_address", return_value=device
                ),
            ):
                result = await flow.async_step_user(
                    {"address": "AA", CONF_SECURITY_CODE: "code"}
                )
            self.assertEqual(result["type"], "create_entry")
            self.assertEqual(result["title"], "EVBox G4")


class ManagedFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import tempfile
        from homeassistant.core import HomeAssistant
        from homeassistant.config_entries import ConfigEntries

        self.directory = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.directory.name)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        self.hass.config_entries._initialized.set()

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self.directory.cleanup()

    async def test_framework_flow_retries_then_saves_and_rejects_manual_and_discovered_duplicates(
        self,
    ):
        from homeassistant.config_entries import ConfigEntries
        from custom_components.evbox_g4_ble.const import DOMAIN, SERVICE_UUID

        client = NS(evb=AsyncMock(side_effect=EVBoxConnectionError("offline")))
        with (
            patch.object(
                f.config_entries,
                "_async_get_flow_handler",
                AsyncMock(return_value=f.EVBoxConfigFlow),
            ),
            patch.object(
                f.config_entries,
                "_support_single_config_entry_only",
                AsyncMock(return_value=False),
            ),
            patch.object(ConfigEntries, "async_setup", AsyncMock(return_value=True)),
            patch.object(f, "EVBoxClient", return_value=client),
            patch.object(
                f.bluetooth,
                "async_ble_device_from_address",
                return_value=NS(name="EVBox test"),
            ),
        ):
            result = await self.hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"}
            )
            self.assertEqual(result["step_id"], "user")
            self.assertEqual(result["errors"], {})
            data = {"address": "AA:BB:CC:DD:EE:FF", CONF_SECURITY_CODE: "code"}
            retry = await self.hass.config_entries.flow.async_configure(
                result["flow_id"], user_input=data
            )
            self.assertEqual(retry["errors"]["base"], "cannot_connect")
            self.assertEqual(self.hass.config_entries.async_entries(DOMAIN), [])
            client.evb.side_effect = None
            completed = await self.hass.config_entries.flow.async_configure(
                result["flow_id"], user_input=data
            )
            self.assertEqual(completed["type"], "create_entry")
            entry = completed["result"]
            self.assertEqual(entry.data, data)
            self.assertEqual(entry.unique_id, data["address"])
            client.evb.reset_mock()
            duplicate = await self.hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"}, data=data
            )
            self.assertEqual(duplicate["reason"], "already_configured")
            duplicate = await self.hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "bluetooth"},
                data=NS(
                    service_uuids=[SERVICE_UUID],
                    address=data["address"],
                    name="EVBox test",
                ),
            )
            self.assertEqual(duplicate["reason"], "already_configured")
            client.evb.assert_not_awaited()
            self.assertEqual(len(self.hass.config_entries.async_entries(DOMAIN)), 1)

    async def test_framework_reauthentication_validates_and_preserves_entry_identity(
        self,
    ):
        from homeassistant.config_entries import ConfigEntry, ConfigEntries
        from custom_components.evbox_g4_ble.const import DOMAIN

        entry = ConfigEntry(
            data={"address": "AA", CONF_SECURITY_CODE: "old"},
            discovery_keys={},
            domain=DOMAIN,
            minor_version=1,
            options={"keep": True},
            source="user",
            subentries_data=[],
            title="EVBox test",
            unique_id="AA",
            version=1,
        )
        self.hass.config_entries._entries[entry.entry_id] = entry
        client = NS(evb=AsyncMock(side_effect=f.EVBoxAuthError("rejected")))
        with (
            patch.object(
                f.config_entries,
                "_async_get_flow_handler",
                AsyncMock(return_value=f.EVBoxConfigFlow),
            ),
            patch.object(ConfigEntries, "async_reload", AsyncMock(return_value=True)),
            patch.object(f, "EVBoxClient", return_value=client),
        ):
            for source, step in [
                ("reauth", "reauth_confirm"),
                ("reconfigure", "reconfigure"),
            ]:
                result = await self.hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"source": source, "entry_id": entry.entry_id},
                    data=dict(entry.data),
                )
                self.assertEqual(result["step_id"], step)
                retry = await self.hass.config_entries.flow.async_configure(
                    result["flow_id"], user_input={CONF_SECURITY_CODE: "new"}
                )
                self.assertEqual(retry["errors"]["base"], "invalid_auth")
                self.assertEqual(entry.data[CONF_SECURITY_CODE], "old")
                client.evb.side_effect = None
                result = await self.hass.config_entries.flow.async_configure(
                    result["flow_id"], user_input={CONF_SECURITY_CODE: "new"}
                )
                self.assertEqual(result["type"], "abort")
                self.assertEqual(
                    result["reason"],
                    "reauth_successful"
                    if source == "reauth"
                    else "reconfigure_successful",
                )
                self.assertIs(
                    self.hass.config_entries.async_get_entry(entry.entry_id), entry
                )
                self.assertEqual(entry.options, {"keep": True})
                self.assertEqual(entry.data[CONF_SECURITY_CODE], "new")
                await self.hass.async_block_till_done()
                self.hass.config_entries.async_update_entry(
                    entry, data={"address": "AA", CONF_SECURITY_CODE: "old"}
                )
                client.evb.side_effect = f.EVBoxAuthError("rejected")


class LegacyIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_mixed_case_identity_is_retained(self):
        flow = f.EVBoxConfigFlow()
        original = "aa:bb:cc:dd:ee:ff"
        entry = NS(data={"address": original}, unique_id=original)
        flow.hass = NS(config_entries=NS(async_entries=Mock(return_value=[entry])))
        flow.handler = "evbox_g4_ble"
        flow.context = {"source": "user"}
        self.assertEqual(flow._existing_unique_id(original.upper()), original)
        self.assertEqual(
            flow._existing_unique_id("11:22:33:44:55:66"), "11:22:33:44:55:66"
        )
        entry.unique_id = None
        self.assertEqual(flow._existing_unique_id(original.upper()), original.upper())

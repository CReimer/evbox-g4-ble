"""Regressions for late capabilities, outages, restart proof and diagnostics."""

from types import SimpleNamespace as NS
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.helpers.update_coordinator import UpdateFailed
from custom_components.evbox_g4_ble import (
    coordinator as c,
    sensor,
    binary_sensor,
    select,
)
from custom_components.evbox_g4_ble.client import EVBoxConnectionError, EVBoxAuthError
from custom_components.evbox_g4_ble.const import (
    KEY_BOOT_INFO,
    KEY_MAX_CURRENT,
    KEY_USE_BACKEND,
    KEY_AUTO_START,
    SCALAR_KEYS,
)
from custom_components.evbox_g4_ble.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.evbox_g4_ble import entity as e
from tests.test_ha_platforms import coordinator


class ObservabilityTests(unittest.IsolatedAsyncioTestCase):
    def make_coordinator(self, entry=None):
        client = NS(get_snapshot=AsyncMock(return_value=({}, [None] * 5)))
        with patch.object(c.DataUpdateCoordinator, "__init__", return_value=None):
            co = c.EVBoxCoordinator(NS(), client, config_entry=entry)
        co.hass = NS()
        co.data = {}
        co.async_set_updated_data = lambda data: setattr(co, "data", data)
        return co

    async def test_late_signal_and_current_limit_appear_once_and_listener_unloads(self):
        co = coordinator()
        callbacks = []
        unsubscribe = Mock()
        co.async_add_listener = lambda cb: callbacks.append(cb) or unsubscribe
        entry = NS(runtime_data=co, data={"address": "AA"}, async_on_unload=Mock())
        entities = []
        await sensor.async_setup_entry(NS(), entry, entities.extend)
        self.assertEqual(entities, [])
        co.data = {"connection_info": {"wifi": {"signal_strength": -55}}}
        callbacks[0]()
        keys = [ent._key for ent in entities]
        self.assertIn("wifi_signal", keys)
        callbacks[0]()
        self.assertEqual(len(entities), len(keys))
        signal = next(ent for ent in entities if ent._key == "wifi_signal")
        self.assertEqual(signal.native_value, -55)
        co.data = {}
        self.assertIsNone(signal.native_value)
        entry.async_on_unload.assert_called_once_with(unsubscribe)

    async def test_registered_entity_restored_even_when_first_read_missing(self):
        co = coordinator()
        entry = NS(runtime_data=co, data={"address": "AA"}, entry_id="entry")
        entities = []
        with (
            patch.object(e.er, "async_get", return_value=NS()),
            patch.object(
                e.er,
                "async_entries_for_config_entry",
                return_value=[NS(unique_id="AA_wifi_signal")],
            ),
        ):
            await sensor.async_setup_entry(NS(data={}), entry, entities.extend)
        self.assertEqual([ent._key for ent in entities], ["wifi_signal"])
        self.assertIsNone(entities[0].native_value)

    async def test_missing_counts_are_unknown_and_reachability_reports_off(self):
        co = coordinator()
        for key in ("rfid_count", "rf_modules"):
            desc = next(d for d in sensor.DESCRIPTIONS if d.key == key)
            ent = sensor.EVBoxSensor(co, "AA", desc)
            self.assertIsNone(ent.native_value)
            co.data[desc.value_key] = []
            self.assertEqual(ent.native_value, 0)
        reachable = binary_sensor.EVBoxReachable(co, "AA", "reachable")
        co.last_update_success = False
        self.assertTrue(reachable.available)
        self.assertFalse(reachable.is_on)

    async def test_card_assignment_explains_unavailability(self):
        co = coordinator()
        co.data = {KEY_AUTO_START: "999999"}
        ent = select.EVBoxAutoStartCardSelect(co, "AA")
        self.assertEqual(
            ent.extra_state_attributes["availability_reason"], "backend_disabled"
        )
        co.data[KEY_USE_BACKEND] = True
        self.assertEqual(ent.extra_state_attributes["availability_reason"], "no_cards")
        co.data["cards"] = [{"id_tag": "AA"}]
        self.assertEqual(ent.extra_state_attributes["availability_reason"], "ready")
        co.data[KEY_AUTO_START] = True
        self.assertEqual(
            ent.extra_state_attributes["availability_reason"], "unsupported_firmware"
        )
        co.last_update_success = False
        self.assertEqual(
            ent.extra_state_attributes["availability_reason"], "connection_failed"
        )

    async def test_health_failure_recovery_does_not_leak_exception_content(self):
        co = self.make_coordinator()
        co.client.get_snapshot.side_effect = EVBoxConnectionError("secret credential")
        with self.assertRaises(UpdateFailed) as exc:
            await co._async_update_data()
        self.assertNotIn("secret", str(exc.exception))
        self.assertEqual(co.health["consecutive_failures"], 1)
        self.assertEqual(co.health["last_error"], "EVBoxConnectionError")
        co.client.get_snapshot.side_effect = None
        await co._async_update_data()
        self.assertEqual(co.health["consecutive_failures"], 0)
        self.assertIsNone(co.health["last_error"])
        self.assertIsNotNone(co.health["last_success"])
        self.assertGreaterEqual(co.health["duration_seconds"], 0)

    async def test_slow_configuration_cached_but_explicit_refresh_and_expiry_read_it(
        self,
    ):
        co = self.make_coordinator()
        co.client.get_snapshot.return_value = ({KEY_MAX_CURRENT: "160"}, [None] * 5)
        co.data = await co._async_update_data()
        co.client.get_snapshot.assert_awaited_with(SCALAR_KEYS)
        co.client.get_snapshot.return_value = ({}, [None] * 5)
        self.assertEqual((await co._async_update_data())[KEY_MAX_CURRENT], "160")
        co.client.get_snapshot.assert_awaited_with(())
        with patch.object(
            c.DataUpdateCoordinator, "async_request_refresh", AsyncMock()
        ) as refresh:
            await co.async_request_refresh()
            refresh.assert_awaited_once()
        await co._async_update_data()
        co.client.get_snapshot.assert_awaited_with(SCALAR_KEYS)
        co._last_full_refresh -= 1801
        await co._async_update_data()
        co.client.get_snapshot.assert_awaited_with(SCALAR_KEYS)

    async def test_reset_acceptance_and_auth_failure_do_not_clear_pending_restart(self):
        co = self.make_coordinator()
        co.data = {KEY_BOOT_INFO: "EVBox,Elvi,serial,425v1,iccid,imsi"}
        co.note_response({"status": "RebootRequired"})
        co.note_restart_sent()
        co.client.get_snapshot.return_value = (
            {KEY_BOOT_INFO: co.data[KEY_BOOT_INFO]},
            [None] * 5,
        )
        co.data = await co._async_update_data()
        self.assertTrue(co.data["restart_required"])
        co.client.get_snapshot.side_effect = EVBoxAuthError("wrong code")
        with self.assertRaises(c.ConfigEntryAuthFailed):
            await co._async_update_data()
        self.assertFalse(co._reset_disconnected)
        co.client.get_snapshot.side_effect = EVBoxConnectionError("offline")
        with self.assertRaises(UpdateFailed):
            await co._async_update_data()
        co.client.get_snapshot.side_effect = None
        co.data = await co._async_update_data()
        self.assertFalse(co.data["restart_required"])

    async def test_firmware_change_confirms_restart_and_pending_marker_survives_reload(
        self,
    ):
        entry = NS(data={"restart_required": True, "restart_firmware": "425v1"})
        co = self.make_coordinator(entry)
        co.client.get_snapshot.return_value = (
            {KEY_BOOT_INFO: "EVBox,Elvi,serial,425v2"},
            [None] * 5,
        )
        result = await co._async_update_data()
        self.assertFalse(result["restart_required"])
        self.assertIsNone(co._restart_firmware)

    async def test_restart_repair_created_and_removed_with_persisted_marker(self):
        entry = NS(data={}, entry_id="entry")
        co = self.make_coordinator(entry)
        co.hass = NS(config_entries=NS(async_update_entry=Mock()))
        with (
            patch.object(c.ir, "async_create_issue") as create,
            patch.object(c.ir, "async_delete_issue") as delete,
        ):
            co.note_response([{"status": "RebootRequired"}])
            create.assert_called_once()
            self.assertTrue(
                co.hass.config_entries.async_update_entry.call_args.kwargs["data"][
                    "restart_required"
                ]
            )
            co._restart_required = False
            co._sync_restart_issue()
            delete.assert_called_once_with(
                co.hass, "evbox_g4_ble", "entry_restart_required"
            )

    async def test_diagnostics_redacts_nested_and_compound_identifiers(self):
        co = self.make_coordinator()
        co.data = {
            KEY_BOOT_INFO: "EVBox,Elvi,secretserial,425v1,secreticcid,secretimsi",
            "wifi_network": "secretssid,secretmac,{WPA2,secretpassword}",
            "wifi_status": "7,secretssid,secretmac,1,-55,secretip",
            "cards": [{"id_tag": "secretcard"}, {"idTag": "secretcard2"}],
            "evb_RFModules": "ChargeBox.123456789",
            "connection_info": {"wifi": {"mac_address": "secretmac"}},
        }
        co.last_update_success = True
        entry = NS(
            runtime_data=co,
            data={"address": "secretaddress", "security_code": "secretcode"},
        )
        result = await async_get_config_entry_diagnostics(NS(), entry)
        rendered = json.dumps(result)
        self.assertNotIn("secret", rendered)
        self.assertNotIn("123456789", rendered)
        self.assertIn("425v1", rendered)
        self.assertEqual(co.data["cards"][0]["id_tag"], "secretcard")

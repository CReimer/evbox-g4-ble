"""Regression tests for the reverse-engineered EVBox protocol."""

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

_SPEC = importlib.util.spec_from_file_location("evbox_protocol", Path(__file__).parents[1] / "custom_components/evbox_g4_ble/protocol.py")
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

from evbox_protocol import (  # noqa: E402
    EVBoxCallError,
    EVBoxProtocolError,
    FrameDecoder,
    build_data_transfer,
    build_ocpp_call,
    build_ocpp_call_result,
    backend_companion_values,
    boot_information,
    auto_start_configuration,
    auto_start_value,
    card_list,
    ccid_ac_configuration,
    chunks,
    connector_value,
    configuration_values,
    configuration_boolean,
    current_to_amperes,
    amperes_to_current,
    frame_message,
    firmware_update_payload,
    led_configuration,
    meter_configuration,
    meter_configuration_value,
    parse_response,
    parse_event_payload,
    phase_rotation_configuration,
    phase_rotation_value,
    rf_modules,
    satellite_scan_results,
    connection_information,
    data_transfer_event,
    data_transfer_event_details,
    split_evb_csv,
    wifi_network,
    wifi_scan_networks,
    wifi_status,
    valid_internet_connection,
)


class ProtocolTests(unittest.TestCase):
    def test_ocpp_call_exact(self):
        _, raw = build_ocpp_call("GetConfiguration", {"key": ["evb_BootInfo"]}, "123")
        self.assertEqual(raw, '[2,"123","GetConfiguration",{"key":["evb_BootInfo"]}]')

    def test_ocpp_call_result_acknowledges_charger_event(self):
        self.assertEqual(
            build_ocpp_call_result("event-123", {"status": "Accepted"}),
            '[3,"event-123",{"status":"Accepted"}]',
        )

    def test_backend_companion_values_match_app_everon_rule(self):
        self.assertEqual(
            backend_companion_values("wss://EU.EVERON.IO/"),
            {
                "evb_SerialAsConnectorId": True,
                "evb_ConnectorList": True,
            },
        )

    def test_firmware_update_payload_matches_current_app_defaults(self):
        payload = firmware_update_payload(
            "https://example.test/firmware.evb",
            datetime(2026, 9, 1, 12, 34, 56, 789123, tzinfo=timezone.utc),
        )
        self.assertEqual(
            payload,
            {
                "location": "https://example.test/firmware.evb",
                "retries": 1,
                "retrieveDate": "2000-09-01T12:34:56.789Z",
                "retryInterval": 60,
            },
        )
        self.assertEqual(
            backend_companion_values("wss://backend.example/"),
            {
                "evb_SerialAsConnectorId": False,
                "evb_ConnectorList": False,
            },
        )

    def test_data_transfer_exact(self):
        _, raw = build_data_transfer("evbBTConnect", ("Android", "secret"), "123")
        self.assertEqual(json.loads(raw)[3], {"messageId": "evbBTConnect", "vendorId": "EV-BOX", "data": "Android,secret"})

    def test_nested_csv_matches_app_shape(self):
        _, raw = build_data_transfer("evbWifiSet", ("ssid", None, {"type": "Open", "password": None}), "1")
        self.assertEqual(json.loads(raw)[3]["data"], "ssid,,{Open,}")

    def test_frame_uses_utf8_byte_length(self):
        raw = '[3,"1",{"text":"ä"}]'
        framed = frame_message(raw)
        self.assertTrue(framed.endswith(raw.encode()))
        self.assertEqual(int(framed[: framed.index(b"[")]), len(raw.encode()))

    def test_decoder_accepts_arbitrary_fragments_and_consecutive_frames(self):
        one = '[3,"1",{"status":"Accepted"}]'
        two = '[3,"2",{"value":2}]'
        stream = frame_message(one) + frame_message(two)
        decoder = FrameDecoder()
        result = []
        for pos in range(0, len(stream), 7):
            result.extend(decoder.feed(stream[pos : pos + 7]))
        self.assertEqual(result, [one, two])

    def test_chunks_are_at_most_twenty_bytes(self):
        parts = chunks("x" * 50)
        self.assertTrue(all(len(part) <= 20 for part in parts))
        self.assertEqual(b"".join(parts), frame_message("x" * 50))

    def test_esp32_chunks_are_at_most_128_bytes(self):
        parts = chunks("x" * 300, 128)
        self.assertTrue(all(len(part) <= 128 for part in parts))
        self.assertEqual(b"".join(parts), frame_message("x" * 300))

    def test_parse_call_result_and_data_transfer(self):
        response = parse_response('[3,"7",{"status":"Accepted","data":"{\\"answer\\":42}"}]', "7")
        self.assertEqual(response.payload, {"answer": 42})

    def test_parse_call_error(self):
        with self.assertRaises(EVBoxCallError):
            parse_response('[4,"7","NotSupported","No"]', "7")

    def test_rejected_ocpp_result_without_data_is_not_reported_as_success(self):
        with self.assertRaisesRegex(EVBoxProtocolError, "rejected: Rejected"):
            parse_response('[3,"7",{"status":"Rejected"}]', "7")

    def test_accepted_ocpp_result_without_data_remains_available(self):
        response = parse_response('[3,"7",{"status":"Accepted"}]', "7")
        self.assertEqual(response.payload, {"status": "Accepted"})

    def test_parse_async_data_transfer_event(self):
        raw = '[2,"9","DataTransfer",{"messageId":"evbWifiStatusNotification","vendorId":"EV-BOX","data":"7,Home"}]'
        self.assertEqual(
            parse_event_payload(raw, "evbWifiStatusNotification"), "7,Home"
        )
        self.assertEqual(
            data_transfer_event(raw),
            ("evbWifiStatusNotification", "7,Home"),
        )
        self.assertEqual(
            data_transfer_event_details(raw),
            ("evbWifiStatusNotification", "7,Home", "9"),
        )

    def test_parse_marked_call_result(self):
        raw = '[3,"9",{"status":"Accepted","data":"6,Home"}]'
        self.assertEqual(
            parse_event_payload(raw, "evbWifiStatusNotification"), "6,Home"
        )

    def test_rejects_wrong_id(self):
        with self.assertRaises(EVBoxProtocolError):
            parse_response('[3,"7",{}]', "8")

    def test_configuration_values(self):
        payload = {"configurationKey": [{"key": "a", "readonly": False, "value": "1"}], "unknownKey": []}
        self.assertEqual(configuration_values(payload), {"a": "1"})

    def test_configuration_boolean_does_not_guess_missing_or_invalid_values(self):
        enabled = {"configurationKey": [{"key": "LocalAuthListEnabled", "value": "true"}]}
        disabled = {"configurationKey": [{"key": "LocalAuthListEnabled", "value": False}]}
        invalid = {"configurationKey": [{"key": "LocalAuthListEnabled", "value": "1"}]}
        self.assertIs(configuration_boolean(enabled, "LocalAuthListEnabled"), True)
        self.assertIs(configuration_boolean(disabled, "LocalAuthListEnabled"), False)
        self.assertIsNone(configuration_boolean(invalid, "LocalAuthListEnabled"))
        self.assertIsNone(configuration_boolean({}, "LocalAuthListEnabled"))

    def test_evbox_current_uses_deciamperes(self):
        self.assertEqual(current_to_amperes("160"), 16.0)
        self.assertEqual(current_to_amperes("60"), 6.0)
        self.assertEqual(amperes_to_current(16.5), 165)

    def test_led_configuration(self):
        self.assertEqual(
            led_configuration("On,00:00:00Z,23:59:59Z,25"),
            {
                "led_mode": "On",
                "led_start_time": "00:00:00Z",
                "led_end_time": "23:59:59Z",
                "led_level": 25,
            },
        )

    def test_phase_rotation_reads_physical_connector(self):
        self.assertEqual(phase_rotation_value("0.Unknown,1.RST"), "RST")
        self.assertEqual(phase_rotation_value("RTS"), "RTS")

    def test_phase_rotation_preserves_other_connectors(self):
        self.assertEqual(
            phase_rotation_configuration("0.Unknown,1.RST,2.TSR", "RTS"),
            "0.Unknown,1.RTS,2.TSR",
        )

    def test_boot_information_fields(self):
        value = "EV-BOX,G4E-WBO,20235340,P0425B0425v1.260323_W6.0.0-050,,"
        self.assertEqual(boot_information(value)["firmware_version"], "P0425B0425v1.260323_W6.0.0-050")
        self.assertEqual(boot_information(value)["serial_number"], "20235340")

    def test_wifi_status_fields(self):
        value = "7,Spartan117,46AE30AC15AD,48,-59,192.168.11.150,255.255.255.0,192.168.11.1,192.168.11.1,0.0.0,FE80::6209:C3FF:FE21:B4F9"
        parsed = wifi_status(value)
        self.assertEqual(parsed["status"], "connected")
        self.assertEqual(parsed["signal_strength"], -59)
        self.assertEqual(parsed["ip_address"], "192.168.11.150")
        self.assertEqual(wifi_status("4,Home")["status"], "wrong_password")
        self.assertEqual(wifi_status("6,Home")["status"], "connecting")

    def test_firmware_update_requires_the_app_internet_precondition(self):
        wifi_online = {
            "current_connection": "Wi-Fi",
            "wifi": {"still_online": True},
            "cellular": {"still_online": False},
        }
        cell_offline = {
            "current_connection": "Cellular",
            "wifi": {"still_online": True},
            "cellular": {"still_online": False},
        }
        self.assertTrue(valid_internet_connection(wifi_online, ""))
        self.assertFalse(valid_internet_connection(cell_offline, ""))
        self.assertTrue(valid_internet_connection({}, "7,Home"))
        self.assertFalse(valid_internet_connection({}, "0,"))

    def test_nested_wifi_configuration(self):
        value = "Spartan117,,{WPA/WPA2 PSK,,,},,"
        self.assertEqual(split_evb_csv(value), ["Spartan117", "", "{WPA/WPA2 PSK,,,}", "", ""])
        self.assertEqual(wifi_network(value), {"ssid": "Spartan117", "authorization": "WPA/WPA2 PSK"})

    def test_wifi_scan_networks_normalizes_app_model(self):
        value = [
            {"ssid": "Home", "macAddress": "AABBCCDDEEFF", "rssi": -58, "channel": 6, "authentication": ["WPA2"]},
            {"ssid": "Guest", "macAddress": "112233445566", "rssi": -71, "authentication": []},
        ]
        self.assertEqual(
            wifi_scan_networks(value),
            [
                {"ssid": "Home", "mac_address": "AABBCCDDEEFF", "signal_strength": -58, "channel": 6, "authentication": ["WPA2"]},
                {"ssid": "Guest", "mac_address": "112233445566", "signal_strength": -71, "authentication": []},
            ],
        )

    def test_wifi_scan_networks_parses_legacy_elvi_wire_format(self):
        value = (
            '"{Guest,112233445566,11,Infrastructure,-71,[],[NONE],[NONE]}'
            '{Home,AABBCCDDEEFF,6,Infrastructure,-58,[WPA,WPA2],[CCMP],[CCMP]}"'
        )
        self.assertEqual(
            wifi_scan_networks(value),
            [
                {
                    "ssid": "Home",
                    "mac_address": "AABBCCDDEEFF",
                    "signal_strength": -58,
                    "channel": 6,
                    "authentication": ["WPA", "WPA2"],
                },
                {
                    "ssid": "Guest",
                    "mac_address": "112233445566",
                    "signal_strength": -71,
                    "channel": 11,
                    "authentication": [],
                },
            ],
        )

    def test_wifi_scan_networks_rejects_malformed_legacy_records(self):
        self.assertEqual(wifi_scan_networks("not a scan response"), [])
        self.assertEqual(wifi_scan_networks("{too,few,fields}"), [])

    def test_meter_configuration_matches_app_model(self):
        self.assertEqual(
            meter_configuration("1.1"),
            {"serial_number": "1", "uses_connector": True, "uses_comma_format": False},
        )
        self.assertEqual(meter_configuration_value("1.1", False), "1.0")
        self.assertEqual(meter_configuration_value("1,ABC123", False), "0,ABC123")

    def test_auto_start_supports_current_and_legacy_firmware(self):
        self.assertEqual(auto_start_configuration(""), {"mode": "rfid", "legacy": False})
        self.assertEqual(auto_start_value("", "automatic_start"), "999999")
        self.assertEqual(auto_start_configuration("true"), {"mode": "automatic_start", "legacy": True})
        self.assertEqual(auto_start_value("true", "rfid"), "false")

    def test_connector_prefixed_ccid_values_keep_their_display_text(self):
        self.assertEqual(connector_value("1.CCID V2 Trip EU"), "CCID V2 Trip EU")
        self.assertEqual(ccid_ac_configuration("1.100"), {"connector_id": 1, "status": "enabled"})

    def test_ccid_ac_uses_the_first_entry_like_the_app_sdk(self):
        self.assertEqual(
            ccid_ac_configuration("0.0,1.100"),
            {"connector_id": 0, "status": "disabled"},
        )
        self.assertEqual(
            ccid_ac_configuration("0.0,1.100", "1"),
            {"connector_id": 1, "status": "enabled"},
        )

    def test_satellites_and_cards_follow_app_models(self):
        self.assertEqual(
            rf_modules("ChargeBox.ABC123.-62"),
            [{"type": "ChargeBox", "id": "ABC123", "signal_strength": -62}],
        )

    def test_async_satellite_scan_uses_braced_app_payload(self):
        self.assertEqual(
            satellite_scan_results("{ChargeBox,ABC123,-62},{SmartGrid,XYZ789,-75}"),
            [
                {"type": "ChargeBox", "id": "ABC123", "signal_strength": -62},
                {"type": "SmartGrid", "id": "XYZ789", "signal_strength": -75},
            ],
        )

    def test_connection_information_follows_sdk_field_order(self):
        self.assertEqual(
            connection_information(
                "WiFi,{1,1,1,-54,12},{1,1,1,1,-71,8}"
            ),
            {
                "current_connection": "WiFi",
                "wifi": {
                    "available": True,
                    "configured": True,
                    "network": True,
                    "signal_strength": -54,
                    "last_online_seconds": 12,
                    "still_online": True,
                },
                "cellular": {
                    "available": True,
                    "sim_card": True,
                    "configured": True,
                    "network": True,
                    "signal_strength": -71,
                    "last_online_seconds": 8,
                    "still_online": True,
                },
            },
        )
        self.assertEqual(
            card_list("{04AABBCC,16},{11223344,}"),
            [{"id_tag": "04AABBCC"}, {"id_tag": "11223344"}],
        )


if __name__ == "__main__":
    unittest.main()

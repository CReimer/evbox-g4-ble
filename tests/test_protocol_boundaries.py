"""Malformed wire data, partial frames and legacy representations."""

import json
import unittest
from datetime import time
from custom_components.evbox_g4_ble import protocol as p


class ProtocolBoundaryTests(unittest.TestCase):
    def test_frames_and_json_reject_invalid_input(self):
        for raw in (b"[]", b"x[]", b"3[\xff]"):
            with self.subTest(raw=raw), self.assertRaises(p.EVBoxProtocolError):
                p.FrameDecoder().feed(raw)
        decoder = p.FrameDecoder()
        self.assertEqual(decoder.feed(b"5"), [])
        self.assertEqual(decoder.feed(b"[1"), [])
        self.assertEqual(decoder.feed(b",2]"), ["[1,2]"])
        for raw in ("invalid", "{}", "[]", '[3,"id",true]'):
            with self.subTest(raw=raw), self.assertRaises(p.EVBoxProtocolError):
                p.parse_response(raw, "different")
        for raw in ("invalid", "{}", '[2,"x","DataTransfer",{}]'):
            if raw == "invalid":
                with self.assertRaises(p.EVBoxProtocolError):
                    p.data_transfer_event_details(raw)
            else:
                self.assertIsNone(p.data_transfer_event_details(raw))
        for value in ("[bad", "true", {"a": 1}, None):
            raw = json.dumps(
                [2, "evt", "DataTransfer", {"messageId": "event", "data": value}]
            )
            expected = True if value == "true" else value
            self.assertEqual(
                p.data_transfer_event_details(raw), ("event", expected, "evt")
            )
            with self.assertRaises(p.EVBoxProtocolError):
                p.parse_event_payload(raw, "other")
        self.assertEqual(p.parse_response('[3,"id",{"data":"[bad"}]').payload, "[bad")
        with self.assertRaises(p.EVBoxCallError) as ctx:
            p.parse_response('[4,"id","Rejected"]')
        self.assertEqual(ctx.exception.description, "")
        self.assertEqual(p._csv_value([True, False]), "[true,false]")

    def test_missing_and_legacy_configuration_values(self):
        self.assertEqual(
            p.configuration_values(
                {"configurationKey": [None, {}, {"key": "x", "value": "maybe"}]}
            ),
            {"x": "maybe"},
        )
        self.assertIsNone(
            p.configuration_boolean(
                {"configurationKey": [{"key": "x", "value": "maybe"}]}, "x"
            )
        )
        self.assertIsNone(p.phase_rotation_value(None))
        self.assertIsNone(p.phase_rotation_value("2.RST"))
        self.assertEqual(p.phase_rotation_configuration(None, "RST"), "1.RST")
        self.assertEqual(p.phase_rotation_configuration("2.STR", "RST"), "2.STR,1.RST")
        self.assertEqual(p.phase_rotation_configuration("STR", "RST"), "RST")
        self.assertEqual(p.meter_configuration(None), {})
        self.assertEqual(p.meter_configuration("serial")["serial_number"], "serial")
        self.assertIsNone(p.connector_value(None))
        self.assertEqual(p.ccid_ac_configuration("invalid")["status"], "disabled")
        self.assertEqual(p.ccid_ac_configuration("2.100", "2")["status"], "enabled")
        self.assertEqual(p.rf_modules([None, {"id": "1"}]), [{"id": "1"}])
        self.assertEqual(
            p.rf_modules("invalid,ChargeBox.1.bad"), [{"type": "ChargeBox", "id": "1"}]
        )
        self.assertEqual(p.satellite_scan_results(None), [])
        self.assertEqual(
            p.satellite_scan_results("{invalid},{,1},{ChargeBox,1,bad}"),
            [{"type": "ChargeBox", "id": "1"}],
        )
        self.assertEqual(p.card_list([None, {"id_tag": "AA"}]), [{"id_tag": "AA"}])
        self.assertEqual(p.card_list("{},{AA}"), [{"id_tag": "AA"}])
        self.assertIsNone(p.evbox_time(None))
        self.assertIsNone(p.evbox_time("invalid"))
        self.assertEqual(p.evbox_time("12:34:56Z"), time(12, 34, 56))
        self.assertEqual(p.evbox_time_value(time(12, 34, 56, 1)), "12:34:56Z")
        self.assertEqual(p.led_configuration("invalid"), {})
        self.assertEqual(p.led_configuration("On,a,b,bad"), {})

    def test_scan_and_connection_data_defensively_parse_partial_records(self):
        for value in (None, 42, b"bad", [], [None, {}, {"ssid": ""}, {"ssid": 42}]):
            self.assertEqual(p.wifi_scan_networks(value), [])
        self.assertEqual(
            p.wifi_scan_networks({"ssid": "test", "rssi": None}), [{"ssid": "test"}]
        )
        self.assertEqual(p.connection_information(None), {})
        self.assertEqual(p.connection_information("invalid"), {})
        self.assertEqual(p.connection_information("wifi,{1},{1}"), {})
        result = p.connection_information("wifi,{1,1,1,bad,bad},{1,1,1,1,bad,bad}")
        self.assertIsNone(result["wifi"]["signal_strength"])
        self.assertFalse(result["wifi"]["still_online"])
        self.assertFalse(result["cellular"]["still_online"])

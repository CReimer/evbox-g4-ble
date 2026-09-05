"""Static checks for Home Assistant metadata and translated option flows."""

import ast
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "evbox_g4_ble"


class MetadataTests(unittest.TestCase):
    def test_every_options_flow_step_has_german_and_english_metadata(self):
        tree = ast.parse((COMPONENT / "config_flow.py").read_text())
        steps = {
            node.name.removeprefix("async_step_")
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("async_step_")
            and node.name not in ("async_step_user", "async_step_bluetooth")
        }
        for language in ("de", "en"):
            translated = json.loads((COMPONENT / "translations" / f"{language}.json").read_text())
            translated_steps = translated["options"]["step"]
            self.assertEqual(steps, set(translated_steps), language)

    def test_next_release_is_integration_0510(self):
        manifest = json.loads((COMPONENT / "manifest.json").read_text())
        self.assertEqual(manifest["version"], "0.5.10")

    def test_firmware_url_uses_a_frontend_serializable_selector(self):
        config_flow_source = (COMPONENT / "config_flow.py").read_text()
        self.assertIn("selector.TextSelectorType.URL", config_flow_source)
        self.assertNotIn("cv.url", config_flow_source)
        self.assertIn("async_start_firmware_update(", config_flow_source)

    def test_web_firmware_uses_a_temporary_read_only_ftp_bridge(self):
        manifest = json.loads((COMPONENT / "manifest.json").read_text())
        source = (COMPONENT / "firmware_proxy.py").read_text()
        self.assertIn("aioftp==0.27.2", manifest["requirements"])
        self.assertIn(
            'permissions=[aioftp.Permission("/", readable=True, writable=False)]',
            source,
        )
        self.assertIn('_FTP_LIFETIME = 15 * 60', source)
        self.assertIn('_MAX_FIRMWARE_SIZE = 64 * 1024 * 1024', source)
        self.assertIn("async_cleanup_firmware_proxies", source)

    def test_firmware_availability_has_a_home_assistant_update_platform(self):
        constants = (COMPONENT / "const.py").read_text()
        update_source = (COMPONENT / "update.py").read_text()
        self.assertIn('"update"', constants)
        self.assertIn("class EVBoxFirmwareUpdate", update_source)
        self.assertIn("installed_version", update_source)
        self.assertIn("latest_version", update_source)
        self.assertIn("async_install", update_source)
        self.assertIn("Range\": \"bytes=0-0", update_source)
        self.assertIn("UpdateEntityFeature.INSTALL", update_source)
        self.assertIn("UpdateEntityFeature.PROGRESS", update_source)
        self.assertIn("firmware_update_state", update_source)
        self.assertIn("firmware_update_error", update_source)
        self.assertIn("transferred_bytes", update_source)

    def test_boot_information_enriches_the_home_assistant_device(self):
        entity_source = (COMPONENT / "entity.py").read_text()
        self.assertIn('device_info["serial_number"]', entity_source)
        self.assertIn('device_info["sw_version"]', entity_source)
        self.assertIn('boot.get("model") or "EVBox Gen4"', entity_source)
        self.assertIn('getattr(coordinator, "device_name", None)', entity_source)
        self.assertIn(
            "EVBoxCoordinator(hass, client, entry.title)",
            (COMPONENT / "__init__.py").read_text(),
        )

    def test_manual_setup_uses_the_live_ble_charge_point_name(self):
        config_flow_source = (COMPONENT / "config_flow.py").read_text()
        self.assertIn("bluetooth.async_ble_device_from_address", config_flow_source)
        self.assertIn("self._name = device.name", config_flow_source)

    def test_manifest_does_not_link_to_unrelated_core_documentation(self):
        manifest = json.loads((COMPONENT / "manifest.json").read_text())
        self.assertNotEqual(
            manifest.get("documentation"), "https://github.com/home-assistant/core"
        )

    def test_manifest_discovers_legacy_and_esp32_elvi(self):
        manifest = json.loads((COMPONENT / "manifest.json").read_text())
        service_uuids = {
            item["service_uuid"].lower() for item in manifest["bluetooth"]
        }
        self.assertEqual(
            service_uuids,
            {
                "2456e1b9-26e2-8f83-e744-f34f01e9d701",
                "0000a002-0000-1000-8000-00805f9b34fb",
            },
        )

    def test_led_mode_uses_translation_safe_options(self):
        select_source = (COMPONENT / "select.py").read_text()
        self.assertIn('_attr_options = ["off", "on"]', select_source)
        self.assertIn("mode=option.title()", select_source)
        for path in (
            COMPONENT / "strings.json",
            COMPONENT / "translations" / "de.json",
            COMPONENT / "translations" / "en.json",
        ):
            states = json.loads(path.read_text())["entity"]["select"]["led_mode"]["state"]
            self.assertEqual(set(states), {"off", "on"})

    def test_no_generic_or_internal_configuration_is_offered(self):
        init_source = (COMPONENT / "__init__.py").read_text()
        services = (COMPONENT / "services.yaml").read_text()
        sensor_source = (COMPONENT / "sensor.py").read_text()
        number_source = (COMPONENT / "number.py").read_text()
        select_source = (COMPONENT / "select.py").read_text()
        config_flow_source = (COMPONENT / "config_flow.py").read_text()
        self.assertNotIn('"set_configuration": vol.Schema', init_source)
        self.assertNotIn('"get_configuration": vol.Schema', init_source)
        self.assertNotIn("set_configuration:\n", services)
        self.assertNotIn("get_configuration:\n", services)
        for key in (
            'key="meter_address"',
            'key="ccid"',
            'key="ccid_ac"',
            'key="phase_rotation"',
            'key="auto_start"',
        ):
            self.assertNotIn(key, sensor_source)
        self.assertNotIn("KEY_SERVER_PORT", number_source)
        self.assertNotIn("KEY_SERVER_OCPP", select_source)
        self.assertNotIn('"ocpp_version"', config_flow_source)
        self.assertNotIn('"modem_port"', config_flow_source)
        self.assertNotIn("ocpp_version:", services)
        self.assertNotIn("modem_port:", services)
        self.assertNotIn("start_time:", services)
        self.assertNotIn("end_time:", services)
        for internal_key in ("evb_ConnectorList", "evb_SerialAsConnectorId"):
            self.assertNotIn(internal_key, services)
            self.assertNotIn(internal_key, sensor_source)
            self.assertNotIn(internal_key, number_source)
            self.assertNotIn(internal_key, select_source)

    def test_server_write_keeps_hidden_app_compatibility_behavior(self):
        client_source = (COMPONENT / "client.py").read_text()
        coordinator_source = (COMPONENT / "coordinator.py").read_text()
        init_source = (COMPONENT / "__init__.py").read_text()
        self.assertIn("async def set_server", client_source)
        self.assertIn("KEY_SERIAL_AS_CONNECTOR_ID", client_source)
        self.assertIn("KEY_CONNECTOR_LIST", client_source)
        self.assertIn("await self.client.set_server(url)", coordinator_source)
        self.assertIn('await coordinator.async_set_server(data["url"])', init_source)

    def test_hidden_server_flags_are_removed_from_old_entity_registries(self):
        init_source = (COMPONENT / "__init__.py").read_text()
        self.assertIn('(\"switch\", KEY_CONNECTOR_LIST)', init_source)
        self.assertIn('(\"switch\", KEY_SERIAL_AS_CONNECTOR_ID)', init_source)

    def test_empty_wifi_status_does_not_hide_wifi_configuration(self):
        config_flow_source = (COMPONENT / "config_flow.py").read_text()
        self.assertIn(
            'if _enabled(self.coordinator.data.get(KEY_USE_BACKEND)):',
            config_flow_source,
        )
        self.assertNotIn(
            '"wifi_status" in self.coordinator.data\n            or "wifi_network"',
            config_flow_source,
        )

    def test_auto_start_uses_hidden_local_authorization_precondition(self):
        client_source = (COMPONENT / "client.py").read_text()
        select_source = (COMPONENT / "select.py").read_text()
        self.assertIn("async def set_auto_start", client_source)
        self.assertIn("KEY_LOCAL_AUTH_LIST_ENABLED", client_source)
        self.assertIn("async_set_auto_start(value)", select_source)
        self.assertIn("async_set_auto_start(option)", select_source)
        self.assertNotIn(
            "async_set_configuration(KEY_AUTO_START", select_source
        )

    def test_rfid_input_matches_current_app_constraints(self):
        constants = (COMPONENT / "const.py").read_text()
        init_source = (COMPONENT / "__init__.py").read_text()
        config_flow = (COMPONENT / "config_flow.py").read_text()
        self.assertIn('RFID_ID_PATTERN = r"^[A-Za-z0-9]{1,20}$"', constants)
        self.assertIn("vol.Match(RFID_ID_PATTERN)", init_source)
        self.assertIn("_valid_text(id_tag, RFID_ID_PATTERN)", config_flow)
        self.assertIn('vol.Required("id_tag"): _text()', config_flow)
        self.assertIn("await self._live_card_ids()", config_flow)

    def test_options_forms_use_frontend_serializable_text_selectors(self):
        config_flow = (COMPONENT / "config_flow.py").read_text()
        self.assertNotIn("vol.Match(", config_flow)
        self.assertIn("selector.TextSelectorType.TEXT", config_flow)
        self.assertIn("_valid_text(\n                url,", config_flow)
        self.assertIn("_valid_text(satellite_id, SATELLITE_ID_PATTERN)", config_flow)

    def test_services_hide_internal_satellite_type_and_ocpp_wording(self):
        init_source = (COMPONENT / "__init__.py").read_text()
        services = (COMPONENT / "services.yaml").read_text()
        self.assertNotIn('vol.Required("satellite_type")', init_source)
        self.assertNotIn("satellite_type:", services)
        self.assertIn("Lade-Backend konfigurieren", services)
        self.assertNotIn("OCPP-Backend konfigurieren", services)

    def test_user_facing_names_explain_rf_as_paired_charge_points(self):
        for path in (
            COMPONENT / "strings.json",
            COMPONENT / "translations" / "de.json",
        ):
            text = path.read_text()
            self.assertIn("Gekoppelte Ladepunkte", text)
            self.assertNotIn('"Satelliten"', text)
            self.assertNotIn("Gekoppelte Satelliten", text)
        german = json.loads(
            (COMPONENT / "translations" / "de.json").read_text()
        )
        self.assertEqual(
            german["entity"]["sensor"]["wifi_network"]["name"],
            "Verbundenes WLAN",
        )

    def test_empty_optional_firmware_values_do_not_create_entities(self):
        sensor_source = (COMPONENT / "sensor.py").read_text()
        self.assertIn('description.key == "boot_info"', sensor_source)
        self.assertIn('details.get("status") != "unknown"', sensor_source)
        self.assertIn('bool(wifi_network(value))', sensor_source)
        self.assertIn('isinstance(wifi.get("signal_strength"), int)', sensor_source)
        self.assertIn('isinstance(cellular.get("signal_strength"), int)', sensor_source)

    def test_diagnostic_entities_do_not_repeat_or_misplace_values(self):
        sensor_source = (COMPONENT / "sensor.py").read_text()
        self.assertIn('(\"firmware_version\", \"vendor\", \"iccid\", \"imsi\")', sensor_source)
        self.assertIn('(\"status\", \"status_code\", \"ssid\", \"signal_strength\")', sensor_source)
        self.assertIn('details.pop("signal_strength", None)', sensor_source)
        self.assertIn('details.pop("ssid", None)', sensor_source)

    def test_card_assignment_does_not_offer_the_internal_dummy_id(self):
        select_source = (COMPONENT / "select.py").read_text()
        for path in (
            COMPONENT / "strings.json",
            COMPONENT / "translations" / "de.json",
            COMPONENT / "translations" / "en.json",
        ):
            self.assertNotIn("not_assigned", path.read_text())
        self.assertNotIn('result.insert(0, "not_assigned")', select_source)


if __name__ == "__main__":
    unittest.main()

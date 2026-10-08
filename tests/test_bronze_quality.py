"""Persistent Bronze guards, metadata and runtime identity checks."""

import copy
import json
from pathlib import Path
import unittest

from PIL import Image
import yaml

from custom_components.evbox_g4_ble.coordinator import EVBoxCoordinator
from custom_components.evbox_g4_ble.models import EVBoxConfigEntry
from tools.check_bronze import BRONZE_RULES, validate

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components/evbox_g4_ble"


class BronzeQualityTests(unittest.TestCase):
    def test_bronze_checklist_cannot_skip_or_exempt_runtime_requirements(self):
        rules = yaml.safe_load((COMPONENT / "quality_scale.yaml").read_text())["rules"]
        coverage = {
            "files": {
                "custom_components/evbox_g4_ble/config_flow.py": {
                    "summary": {
                        "num_statements": 20,
                        "covered_lines": 20,
                        "num_branches": 4,
                        "covered_branches": 4,
                        "excluded_lines": 0,
                    },
                    "excluded_lines": [],
                }
            }
        }
        self.assertEqual(len(BRONZE_RULES), 20)
        self.assertEqual(validate(rules, coverage), [])
        for mutate in (
            "missing_rule",
            "invalid_exemption",
            "missing_line",
            "missing_branch",
            "excluded_path",
            "missing_report",
        ):
            modified_rules, report = copy.deepcopy(rules), copy.deepcopy(coverage)
            flow = report["files"]["custom_components/evbox_g4_ble/config_flow.py"]
            if mutate == "missing_rule":
                del modified_rules["runtime-data"]
            elif mutate == "invalid_exemption":
                modified_rules["brands"]["status"] = "exempt"
            elif mutate == "missing_line":
                flow["summary"]["covered_lines"] -= 1
            elif mutate == "missing_branch":
                flow["summary"]["covered_branches"] -= 1
            elif mutate == "excluded_path":
                flow["excluded_lines"] = [1]
            else:
                report["files"].clear()
            self.assertTrue(validate(modified_rules, report), mutate)

    def test_local_brand_images_are_valid_pngs_at_required_resolutions(self):
        for name, size in [("icon.png", 256), ("icon@2x.png", 512)]:
            with Image.open(COMPONENT / "brand" / name) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (size, size))
                image.verify()

    def test_runtime_entry_is_typed_and_all_form_fields_have_descriptions(self):
        self.assertEqual(EVBoxConfigEntry.__value__.__args__, (EVBoxCoordinator,))
        canonical = json.loads((COMPONENT / "strings.json").read_text())
        self.assertEqual(
            canonical, json.loads((COMPONENT / "translations/en.json").read_text())
        )
        for language in ("en", "de"):
            translations = json.loads(
                (COMPONENT / f"translations/{language}.json").read_text()
            )
            for group in ("config", "options"):
                for name, step in translations[group]["step"].items():
                    self.assertEqual(
                        set(step.get("data", {})),
                        set(step.get("data_description", {})),
                        (language, name),
                    )
                    self.assertTrue(
                        all(step["data_description"].values())
                        if step.get("data")
                        else True
                    )

    def test_user_documentation_covers_all_service_actions(self):
        from custom_components.evbox_g4_ble import SERVICE_SCHEMAS

        documentation = (ROOT / "documentation/usage.md").read_text()
        for action in SERVICE_SCHEMAS:
            self.assertIn(f"| `{action}` |", documentation)
        self.assertIn("## Removal", documentation)
        self.assertIn("## Setup and configuration", documentation)


class ActionValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_action_rejects_unloaded_entry_before_ble_access(self):
        from types import SimpleNamespace as NS
        from unittest.mock import AsyncMock, Mock, patch
        from homeassistant.config_entries import ConfigEntryState
        from homeassistant.exceptions import ServiceValidationError
        from custom_components import evbox_g4_ble as integration

        client = NS(evb=AsyncMock())
        entry = NS(
            entry_id="entry",
            state=ConfigEntryState.NOT_LOADED,
            runtime_data=NS(client=client),
        )
        hass = NS(
            services=NS(async_register=Mock()),
            bus=NS(async_listen_once=Mock()),
            config_entries=NS(async_entries=Mock(return_value=[entry])),
        )
        with patch.object(integration, "async_cleanup_firmware_proxies", AsyncMock()):
            self.assertTrue(await integration.async_setup(hass, {}))
        handlers = {
            args.args[1]: args.args[2]
            for args in hass.services.async_register.call_args_list
        }
        self.assertIn("identify", handlers)
        with self.assertRaises(ServiceValidationError) as error:
            await handlers["identify"](
                NS(service="identify", data={"entry_id": "entry"})
            )
        self.assertEqual(error.exception.translation_key, "entry_required")
        client.evb.assert_not_awaited()

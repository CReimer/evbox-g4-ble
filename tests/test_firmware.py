"""Firmware catalog and EVBox version parsing tests."""

import importlib.util
from pathlib import Path
import sys
import unittest

_SPEC = importlib.util.spec_from_file_location(
    "evbox_firmware",
    Path(__file__).parents[1] / "custom_components/evbox_g4_ble/firmware.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

from evbox_firmware import (  # noqa: E402
    installed_release,
    latest_release,
    release_from_filename,
)


class FirmwareTests(unittest.TestCase):
    def test_full_bootinfo_version_matches_app_release(self):
        self.assertEqual(
            installed_release("P0425B0425v1.260323_W6.0.0-050"), "425v1"
        )
        self.assertEqual(installed_release("G4P0424B0424v0.220706"), "424v0")

    def test_app_release_name_is_accepted(self):
        self.assertEqual(installed_release("425v1"), "425v1")
        self.assertIsNone(installed_release("unknown"))

    def test_official_firmware_filename_exposes_release(self):
        self.assertEqual(
            release_from_filename("G4P0425B0425v1.evb"), "425v1"
        )
        self.assertEqual(
            release_from_filename("attachment%20G4P0426B0426v2.evb"), "426v2"
        )
        self.assertIsNone(release_from_filename("firmware.bin"))

    def test_model_specific_catalog_does_not_guess_unknown_hardware(self):
        self.assertEqual(latest_release("G4E-WBO"), "425v1")
        self.assertEqual(latest_release("G4X-EU"), "425v1")
        self.assertIsNone(latest_release("G4Q-EU"))
        self.assertIsNone(latest_release("Elvi"))


if __name__ == "__main__":
    unittest.main()

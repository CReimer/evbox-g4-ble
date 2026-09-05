"""Firmware proxy concurrency guard tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

_SPEC = importlib.util.spec_from_file_location(
    "evbox_firmware_proxy_state",
    Path(__file__).parents[1]
    / "custom_components"
    / "evbox_g4_ble"
    / "firmware_proxy_state.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)


class FirmwareProxyStateTests(unittest.TestCase):
    def test_only_one_update_can_be_active_per_charger(self):
        registry = {}
        charger_ip = "192.0.2.1"

        state = _MODULE.reserve_proxy(registry, charger_ip)
        self.assertIsNotNone(state)
        self.assertTrue(_MODULE.proxy_is_active(registry, charger_ip))
        self.assertIsNone(_MODULE.reserve_proxy(registry, charger_ip))

        proxy = object()
        _MODULE.activate_proxy(registry, charger_ip, state, proxy)
        self.assertEqual(tuple(_MODULE.running_proxies(registry)), (proxy,))

        _MODULE.release_proxy(registry, charger_ip, state, object())
        self.assertTrue(_MODULE.proxy_is_active(registry, charger_ip))
        _MODULE.release_proxy(registry, charger_ip, state, proxy)
        self.assertFalse(_MODULE.proxy_is_active(registry, charger_ip))

    def test_failed_setup_releases_its_reservation(self):
        registry = {}
        charger_ip = "192.0.2.1"

        state = _MODULE.reserve_proxy(registry, charger_ip)
        self.assertIsNotNone(state)
        _MODULE.release_proxy(registry, charger_ip, state)

        self.assertIsNotNone(_MODULE.reserve_proxy(registry, charger_ip))

    def test_progress_and_abort_error_remain_observable(self):
        registry = {}
        charger_ip = "192.0.2.1"
        state = _MODULE.reserve_proxy(registry, charger_ip)
        self.assertIsNotNone(state)

        _MODULE.update_transfer(state, "downloading", 25, 100)
        self.assertEqual(state.percentage, 25)
        self.assertTrue(state.in_progress)

        _MODULE.update_transfer(
            state,
            "error",
            25,
            100,
            "FTP transfer aborted by charger",
        )
        self.assertFalse(state.in_progress)
        self.assertEqual(state.percentage, 25)
        self.assertEqual(state.phase, "error")
        self.assertEqual(
            state.error, "FTP transfer aborted by charger"
        )


if __name__ == "__main__":
    unittest.main()

"""Tests for authoritative configuration readback."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "evbox_elvi_ble"
PACKAGE = "evbox_coordinator_test_component"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_coordinator():
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(COMPONENT)]
    sys.modules[PACKAGE] = package

    homeassistant = types.ModuleType("homeassistant")
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    exceptions = types.ModuleType("homeassistant.exceptions")

    class HomeAssistantError(Exception):
        pass

    exceptions.HomeAssistantError = HomeAssistantError
    helpers = types.ModuleType("homeassistant.helpers")
    update = types.ModuleType("homeassistant.helpers.update_coordinator")

    class DataUpdateCoordinator:
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, *_args, **_kwargs):
            self.data = {}

        def async_set_updated_data(self, data):
            self.data = data

    class UpdateFailed(Exception):
        pass

    update.DataUpdateCoordinator = DataUpdateCoordinator
    update.UpdateFailed = UpdateFailed
    helpers.update_coordinator = update
    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.core": core,
            "homeassistant.exceptions": exceptions,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.update_coordinator": update,
        }
    )

    client = types.ModuleType(f"{PACKAGE}.client")
    client.EVBoxClient = object
    sys.modules[client.__name__] = client
    _load_module(f"{PACKAGE}.const", COMPONENT / "const.py")
    _load_module(f"{PACKAGE}.protocol", COMPONENT / "protocol.py")
    return (
        _load_module(f"{PACKAGE}.coordinator", COMPONENT / "coordinator.py"),
        HomeAssistantError,
    )


COORDINATOR_MODULE, HomeAssistantError = _load_coordinator()


class _Client:
    def __init__(
        self,
        readback: dict[str, object],
        *,
        evb_responses: dict[str, object] | None = None,
        card_reads: list[object] | None = None,
    ) -> None:
        self.readback = readback
        self.evb_responses = evb_responses or {}
        self.card_reads = list(card_reads or [])
        self.writes: list[tuple[str, object]] = []
        self.reads: list[str] = []
        self.ocpp_calls: list[tuple[str, object]] = []

    async def set_configuration(self, key, value):
        self.writes.append((key, value))

    async def get_configuration(self, keys):
        self.reads.extend(keys)
        return {key: self.readback[key] for key in keys if key in self.readback}

    async def session(self, operations):
        self.writes.extend(
            (payload["key"], payload["value"])
            for _kind, _name, payload in operations
        )

    async def evb(self, command, values=()):
        self.writes.append((command, values))
        if command == "evbWhiteListGet" and self.card_reads:
            return self.card_reads.pop(0)
        return self.evb_responses.get(command)

    async def ocpp(self, action, payload):
        self.ocpp_calls.append((action, payload))
        if action == "GetLocalListVersion":
            return {"listVersion": 4}
        return {"status": "Accepted"}


class CoordinatorReadbackTests(unittest.IsolatedAsyncioTestCase):
    def _coordinator(self, client: _Client):
        coordinator = COORDINATOR_MODULE.EVBoxCoordinator(object(), client)
        coordinator.data = {"existing": "kept"}
        return coordinator

    async def test_only_authoritative_readback_becomes_entity_state(self):
        client = _Client({"evb_UseBackend": "true"})
        coordinator = self._coordinator(client)
        await coordinator.async_set_configuration("evb_UseBackend", True)
        self.assertEqual(client.writes, [("evb_UseBackend", True)])
        self.assertEqual(coordinator.data["evb_UseBackend"], "true")
        self.assertEqual(coordinator.data["existing"], "kept")

    async def test_missing_readback_is_not_reported_as_success(self):
        coordinator = self._coordinator(_Client({}))
        with self.assertRaisesRegex(HomeAssistantError, "nicht zurückgegeben"):
            await coordinator.async_set_configuration("evb_APNName", "internet")
        self.assertNotIn("evb_APNName", coordinator.data)

    async def test_different_readback_is_not_reported_as_success(self):
        coordinator = self._coordinator(_Client({"evb_APNName": "other"}))
        with self.assertRaisesRegex(HomeAssistantError, "anderen Wert"):
            await coordinator.async_set_configuration("evb_APNName", "internet")
        self.assertNotIn("evb_APNName", coordinator.data)

    async def test_write_only_apn_password_is_written_but_never_read_or_exposed(self):
        client = _Client(
            {
                "evb_APNName": "internet",
                "evb_APNUser": "user",
                "evb_APNPass": "secret",
            }
        )
        coordinator = self._coordinator(client)
        await coordinator.async_set_apn("internet", "user", "secret")
        self.assertEqual(coordinator.data["evb_APNName"], "internet")
        self.assertEqual(coordinator.data["evb_APNUser"], "user")
        self.assertNotIn("evb_APNPass", coordinator.data)
        self.assertIn(("evb_APNPass", "secret"), client.writes)
        self.assertNotIn("evb_APNPass", client.reads)

    async def test_led_state_is_read_back_before_entities_are_updated(self):
        value = "On,00:00:00Z,23:59:59Z,25"
        client = _Client({}, evb_responses={"evbLEDsIdleGet": value})
        coordinator = self._coordinator(client)
        await coordinator.async_set_led(mode="On", level=25)
        self.assertEqual(coordinator.data["led_idle"], value)
        self.assertEqual(coordinator.data["led_level"], 25)

    async def test_different_led_readback_is_not_reported_as_success(self):
        client = _Client(
            {},
            evb_responses={
                "evbLEDsIdleGet": "Off,00:00:00Z,23:59:59Z,25"
            },
        )
        coordinator = self._coordinator(client)
        with self.assertRaisesRegex(HomeAssistantError, "anderen LED-Ruhezustand"):
            await coordinator.async_set_led(mode="On", level=25)
        self.assertNotIn("led_idle", coordinator.data)

    async def test_rf_readback_ignores_signal_strength_and_order(self):
        client = _Client(
            {"evb_RFModules": "SmartGrid.22.-70,ChargeBox.11.-42"}
        )
        coordinator = self._coordinator(client)
        await coordinator.async_set_rf_modules("ChargeBox.11,SmartGrid.22")
        self.assertEqual(
            [item["id"] for item in coordinator.data["rf_modules_parsed"]],
            ["22", "11"],
        )

    async def test_missing_card_cannot_be_silently_removed(self):
        client = _Client({}, card_reads=["{A1,0}"])
        coordinator = self._coordinator(client)
        with self.assertRaisesRegex(HomeAssistantError, "nicht in der Elvi"):
            await coordinator.async_remove_card("B2")
        self.assertEqual(client.ocpp_calls, [])

    async def test_card_replacement_is_verified_and_updates_state(self):
        client = _Client({}, card_reads=["{A1,0},{B2,0}", "{A1,0}"])
        coordinator = self._coordinator(client)
        await coordinator.async_remove_card("B2")
        self.assertEqual(coordinator.data["cards"], [{"id_tag": "A1"}])
        self.assertEqual(client.ocpp_calls[0][0], "GetLocalListVersion")
        self.assertEqual(client.ocpp_calls[1][0], "SendLocalList")


if __name__ == "__main__":
    unittest.main()

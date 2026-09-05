"""Behavior tests for capability-gated Home Assistant actions."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
import sys
import types
import unittest

import voluptuous as vol


ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "evbox_g4_ble"
PACKAGE = "evbox_services_test_component"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_integration():
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(COMPONENT)]
    sys.modules[PACKAGE] = package

    homeassistant = types.ModuleType("homeassistant")
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    const = types.ModuleType("homeassistant.const")
    const.EVENT_HOMEASSISTANT_STOP = "homeassistant_stop"
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object

    class ServiceCall:
        def __init__(self, service: str, data: dict | None = None) -> None:
            self.service = service
            self.data = data or {}

    class SupportsResponse:
        OPTIONAL = "optional"

    core.ServiceCall = ServiceCall
    core.SupportsResponse = SupportsResponse
    exceptions = types.ModuleType("homeassistant.exceptions")

    class HomeAssistantError(Exception):
        pass

    exceptions.HomeAssistantError = HomeAssistantError
    helpers = types.ModuleType("homeassistant.helpers")
    cv = types.ModuleType("homeassistant.helpers.config_validation")
    cv.config_entry_only_config_schema = lambda _domain: object()
    cv.url = str
    registry = types.ModuleType("homeassistant.helpers.entity_registry")
    registry.async_get = lambda _hass: None
    helpers.config_validation = cv
    helpers.entity_registry = registry

    homeassistant.config_entries = config_entries
    homeassistant.core = core
    homeassistant.exceptions = exceptions
    homeassistant.helpers = helpers
    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.config_entries": config_entries,
            "homeassistant.const": const,
            "homeassistant.core": core,
            "homeassistant.exceptions": exceptions,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.config_validation": cv,
            "homeassistant.helpers.entity_registry": registry,
        }
    )

    client = types.ModuleType(f"{PACKAGE}.client")
    client.EVBoxClient = object
    coordinator = types.ModuleType(f"{PACKAGE}.coordinator")
    coordinator.EVBoxCoordinator = object
    firmware_proxy = types.ModuleType(f"{PACKAGE}.firmware_proxy")

    async def async_start_firmware_update(_hass, coordinator, source_url):
        coordinator.client.calls.append(("firmware_update", source_url))
        return {"status": "Accepted"}, source_url.startswith("http")

    firmware_proxy.async_start_firmware_update = async_start_firmware_update

    async def async_cleanup_firmware_proxies(_hass):
        return None

    firmware_proxy.async_cleanup_firmware_proxies = async_cleanup_firmware_proxies
    sys.modules[client.__name__] = client
    sys.modules[coordinator.__name__] = coordinator
    sys.modules[firmware_proxy.__name__] = firmware_proxy
    _load_module(f"{PACKAGE}.const", COMPONENT / "const.py")
    _load_module(f"{PACKAGE}.protocol", COMPONENT / "protocol.py")
    return _load_module(PACKAGE, COMPONENT / "__init__.py"), ServiceCall, HomeAssistantError


INTEGRATION, ServiceCall, HomeAssistantError = _load_integration()


class _ConfigEntries:
    def __init__(self, coordinator) -> None:
        self.entry = types.SimpleNamespace(entry_id="entry", runtime_data=coordinator)

    def async_entries(self, _domain):
        return [self.entry]


class _Hass:
    def __init__(self, coordinator) -> None:
        self.config_entries = _ConfigEntries(coordinator)
        self.bus = types.SimpleNamespace(async_listen_once=lambda *_args: None)


class _Services:
    def __init__(self) -> None:
        self.handlers = {}

    def async_register(self, domain, name, handler, **_kwargs):
        self.handlers[(domain, name)] = handler


class _Client:
    def __init__(self, *, wifi_response: str | None = None) -> None:
        self.calls: list[tuple] = []
        self.wifi_response = wifi_response

    async def evb(self, command, values=()):
        self.calls.append(("evb", command, values))
        return True

    async def set_configuration(self, key, value):
        self.calls.append(("set_configuration", key, value))
        return {"status": "Accepted"}

    async def set_wifi(self, values):
        self.calls.append(("set_wifi", values))
        return self.wifi_response


class _Coordinator:
    def __init__(self, data: dict, *, wifi_response: str | None = None) -> None:
        self.data = data
        self.client = _Client(wifi_response=wifi_response)
        self.refreshes = 0

    async def async_request_refresh(self):
        self.refreshes += 1


class ServiceBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_service_handler_is_awaitable(self):
        coordinator = _Coordinator({})
        hass = _Hass(coordinator)
        hass.services = _Services()
        await INTEGRATION.async_setup(hass, {})
        handler = hass.services.handlers[(INTEGRATION.DOMAIN, "refresh")]
        self.assertTrue(inspect.iscoroutinefunction(handler))
        self.assertEqual(await handler(ServiceCall("refresh")), coordinator.data)
        self.assertEqual(coordinator.refreshes, 1)

    async def test_https_firmware_service_uses_ftp_bridge(self):
        coordinator = _Coordinator(
            {
                "connection_info": {
                    "current_connection": "Wi-Fi",
                    "wifi": {"still_online": True},
                }
            }
        )
        response = await INTEGRATION._handle_service(
            _Hass(coordinator),
            ServiceCall(
                "update_firmware", {"url": "https://example.test/update.evb"}
            ),
        )
        self.assertEqual(
            response,
            {"result": {"status": "Accepted"}, "proxied_via_ftp": True},
        )
        self.assertIn(
            ("firmware_update", "https://example.test/update.evb"),
            coordinator.client.calls,
        )
        self.assertEqual(coordinator.refreshes, 1)

    async def test_wifi_action_is_rejected_when_online_mode_is_disabled(self):
        coordinator = _Coordinator({"evb_UseBackend": "false"})
        with self.assertRaisesRegex(HomeAssistantError, "Online-/Backend-Betrieb"):
            await INTEGRATION._handle_service(
                _Hass(coordinator), ServiceCall("scan_wifi")
            )
        self.assertEqual(coordinator.client.calls, [])

    async def test_wifi_service_reports_wrong_password_as_failure(self):
        coordinator = _Coordinator(
            {"evb_UseBackend": "true"},
            wifi_response="4,Home,AA:BB:CC:DD:EE:FF,6,-52",
        )
        with self.assertRaisesRegex(HomeAssistantError, "falsches WLAN-Passwort"):
            await INTEGRATION._handle_service(
                _Hass(coordinator),
                ServiceCall(
                    "set_wifi", {"ssid": "Home", "password": "incorrect"}
                ),
            )
        self.assertEqual(coordinator.refreshes, 0)

    async def test_wifi_service_accepts_connected_status_and_refreshes(self):
        coordinator = _Coordinator(
            {"evb_UseBackend": "true"},
            wifi_response="7,Home,AA:BB:CC:DD:EE:FF,6,-52,192.0.2.2",
        )
        await INTEGRATION._handle_service(
            _Hass(coordinator),
            ServiceCall("set_wifi", {"ssid": "Home", "password": "correct"}),
        )
        self.assertEqual(coordinator.refreshes, 1)

    async def test_unsupported_rfid_action_is_not_sent(self):
        coordinator = _Coordinator({})
        with self.assertRaisesRegex(HomeAssistantError, "nicht unterstützt"):
            await INTEGRATION._handle_service(
                _Hass(coordinator),
                ServiceCall("rfid_clear"),
            )
        self.assertEqual(coordinator.client.calls, [])

    async def test_satellite_identification_uses_stored_type(self):
        coordinator = _Coordinator(
            {"evb_RFModules": "SmartGrid.12345,ChargeBox.67890"}
        )
        await INTEGRATION._handle_service(
            _Hass(coordinator),
            ServiceCall("blink_satellite", {"satellite_id": "12345"}),
        )
        self.assertIn(
            ("set_configuration", "evb_Trigger", "RFShow,SmartGrid,12345"),
            coordinator.client.calls,
        )

    async def test_unknown_satellite_is_not_identified(self):
        coordinator = _Coordinator({"evb_RFModules": "ChargeBox.67890"})
        with self.assertRaisesRegex(HomeAssistantError, "nicht eindeutig"):
            await INTEGRATION._handle_service(
                _Hass(coordinator),
                ServiceCall("blink_satellite", {"satellite_id": "12345"}),
            )
        self.assertEqual(coordinator.client.calls, [])

    def test_satellite_type_is_not_a_user_input(self):
        schema = INTEGRATION.SERVICE_SCHEMAS["blink_satellite"]
        self.assertEqual(schema({"satellite_id": "12345"}), {"satellite_id": "12345"})
        self.assertNotIn("satellite_type", schema.schema)

    def test_all_apn_fields_reject_whitespace(self):
        schema = INTEGRATION.SERVICE_SCHEMAS["set_apn"]
        for field in ("apn", "username", "password"):
            values = {"apn": "internet", field: "not valid"}
            with self.assertRaises(vol.Invalid, msg=field):
                schema(values)


if __name__ == "__main__":
    unittest.main()

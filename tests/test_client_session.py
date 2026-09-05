"""End-to-end tests for authenticated BLE command sequencing."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "evbox_g4_ble"
PACKAGE = "evbox_client_test_component"


def _load_client_module():
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(COMPONENT)]
    sys.modules[PACKAGE] = package

    bleak = types.ModuleType("bleak_retry_connector")
    bleak.BleakClientWithServiceCache = object

    async def establish_connection(*_args, **_kwargs):
        raise AssertionError("Tests replace EVBoxClient._connect")

    bleak.establish_connection = establish_connection
    sys.modules["bleak_retry_connector"] = bleak

    homeassistant = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    bluetooth = types.ModuleType("homeassistant.components.bluetooth")
    bluetooth.async_ble_device_from_address = lambda *_args, **_kwargs: None
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    homeassistant.components = components
    components.bluetooth = bluetooth
    sys.modules["homeassistant"] = homeassistant
    sys.modules["homeassistant.components"] = components
    sys.modules["homeassistant.components.bluetooth"] = bluetooth
    sys.modules["homeassistant.core"] = core

    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE}.client", COMPONENT / "client.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CLIENT_MODULE = _load_client_module()
PROTOCOL = sys.modules[f"{PACKAGE}.protocol"]


class _Services:
    def __init__(self, esp32: bool) -> None:
        self.esp32 = esp32

    def get_characteristic(self, uuid):
        if self.esp32 and uuid in (
            CLIENT_MODULE.ESP32_WRITE_UUID,
            CLIENT_MODULE.ESP32_NOTIFY_UUID,
        ):
            return object()
        return None


class FakeBLEClient:
    """Decode writes and immediately return charger-style BLE notifications."""

    def __init__(
        self,
        *,
        local_auth_enabled: bool = True,
        authorization_result: bool | None = True,
        esp32: bool = False,
        reject_key: str | None = None,
        reboot_key: str | None = None,
        reject_get_key: str | None = None,
        malformed_get_key: str | None = None,
        wifi_direct_response: str | None = None,
        wifi_notification: str | None = None,
    ) -> None:
        self.services = _Services(esp32)
        self.is_connected = True
        self.local_auth_enabled = local_auth_enabled
        self.authorization_result = authorization_result
        self.reject_key = reject_key
        self.reboot_key = reboot_key
        self.reject_get_key = reject_get_key
        self.malformed_get_key = malformed_get_key
        self.wifi_direct_response = wifi_direct_response
        self.wifi_notification = wifi_notification
        self.actions: list[tuple[str, dict]] = []
        self.write_uuids: list[str] = []
        self.write_sizes: list[int] = []
        self.notify_uuid: str | None = None
        self._notification = None
        self._decoder = PROTOCOL.FrameDecoder()

    async def start_notify(self, uuid, callback):
        self.notify_uuid = uuid
        self._notification = callback

    async def stop_notify(self, _uuid):
        return None

    async def disconnect(self):
        self.is_connected = False

    async def write_gatt_char(self, uuid, part, response=True):
        assert response is True
        self.write_uuids.append(uuid)
        self.write_sizes.append(len(part))
        for raw in self._decoder.feed(part):
            message = json.loads(raw)
            message_id = str(message[1])
            action = str(message[2])
            payload = message[3]
            self.actions.append((action, payload))
            if action == "DataTransfer" and payload.get("messageId") == "evbWifiSet":
                reply = [
                    3,
                    message_id,
                    {"status": True, "data": self.wifi_direct_response},
                ]
            elif action == "DataTransfer":
                reply = [
                    3,
                    message_id,
                    {
                        "status": True,
                        "data": (
                            None
                            if self.authorization_result is None
                            else str(self.authorization_result).lower()
                        ),
                    },
                ]
            elif action == "GetConfiguration":
                requested_key = payload["key"][0]
                if requested_key == self.reject_get_key:
                    reply = [4, message_id, "NotSupported", "unknown key", {}]
                elif requested_key == self.malformed_get_key:
                    reply = [3, message_id, {"status": "Accepted"}]
                else:
                    value = (
                        str(self.local_auth_enabled).lower()
                        if requested_key == "LocalAuthListEnabled"
                        else f"value-for-{requested_key}"
                    )
                    reply = [
                        3,
                        message_id,
                        {
                            "configurationKey": [
                                {
                                    "key": requested_key,
                                    "readonly": False,
                                    "value": value,
                                }
                            ],
                            "unknownKey": [],
                        },
                    ]
            else:
                if (
                    action == "ChangeConfiguration"
                    and payload.get("key") == "LocalAuthListEnabled"
                ):
                    self.local_auth_enabled = payload.get("value") == "true"
                if (
                    action == "ChangeConfiguration"
                    and payload.get("key") == self.reject_key
                ):
                    status = "Rejected"
                elif (
                    action == "ChangeConfiguration"
                    and payload.get("key") == self.reboot_key
                ):
                    status = "RebootRequired"
                else:
                    status = "Accepted"
                reply = [3, message_id, {"status": status}]
            assert self._notification is not None
            self._notification(None, bytearray(PROTOCOL.frame_message(json.dumps(reply))))
            if (
                action == "DataTransfer"
                and payload.get("messageId") == "evbWifiSet"
                and self.wifi_notification is not None
            ):
                notification = [
                    3,
                    "evbWifiStatusNotification",
                    {"status": True, "data": self.wifi_notification},
                ]
                self._notification(
                    None,
                    bytearray(PROTOCOL.frame_message(json.dumps(notification))),
                )


class ClientSessionTests(unittest.IsolatedAsyncioTestCase):
    def _client(self, fake: FakeBLEClient):
        client = CLIENT_MODULE.EVBoxClient(object(), "AA:BB:CC:DD:EE:FF", "secret")

        async def connect():
            return fake

        client._connect = connect
        return client

    async def test_server_write_uses_one_ble_login_and_app_order(self):
        fake = FakeBLEClient()
        await self._client(fake).set_server("wss://EU.EVERON.IO/")
        self.assertEqual(
            fake.actions,
            [
                (
                    "DataTransfer",
                    {
                        "messageId": "evbBTConnect",
                        "vendorId": "EV-BOX",
                        "data": "Android,secret",
                    },
                ),
                (
                    "ChangeConfiguration",
                    {"key": "evb_ServerURL", "value": "wss://EU.EVERON.IO/"},
                ),
                (
                    "ChangeConfiguration",
                    {"key": "evb_SerialAsConnectorId", "value": "true"},
                ),
                (
                    "ChangeConfiguration",
                    {"key": "evb_ConnectorList", "value": "true"},
                ),
            ],
        )

    async def test_false_authorization_payload_stops_before_any_command(self):
        fake = FakeBLEClient(authorization_result=False)
        with self.assertRaisesRegex(
            CLIENT_MODULE.EVBoxConnectionError, "security code was rejected"
        ):
            await self._client(fake).set_server("wss://backend.example/")
        self.assertEqual(len(fake.actions), 1)
        self.assertEqual(fake.actions[0][0], "DataTransfer")

    async def test_empty_successful_authorization_payload_matches_app_sdk(self):
        fake = FakeBLEClient(authorization_result=None)
        await self._client(fake).set_server("wss://backend.example/")
        self.assertEqual(len(fake.actions), 4)

    async def test_rejected_server_url_stops_before_companion_writes(self):
        fake = FakeBLEClient(reject_key="evb_ServerURL")
        with self.assertRaisesRegex(
            PROTOCOL.EVBoxProtocolError, "rejected: Rejected"
        ):
            await self._client(fake).set_server("wss://backend.example/")
        self.assertEqual(
            [payload.get("key") for action, payload in fake.actions if action == "ChangeConfiguration"],
            ["evb_ServerURL"],
        )

    async def test_reboot_required_server_url_continues_companion_writes(self):
        fake = FakeBLEClient(reboot_key="evb_ServerURL")
        await self._client(fake).set_server("wss://backend.example/")
        self.assertEqual(
            [
                payload.get("key")
                for action, payload in fake.actions
                if action == "ChangeConfiguration"
            ],
            [
                "evb_ServerURL",
                "evb_SerialAsConnectorId",
                "evb_ConnectorList",
            ],
        )

    async def test_rejected_rf_scan_does_not_leave_unhandled_marker_error(self):
        loop = asyncio.get_running_loop()
        unhandled = []
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
        try:
            fake = FakeBLEClient(reject_key="evb_Trigger")
            with self.assertRaisesRegex(
                PROTOCOL.EVBoxProtocolError, "rejected: Rejected"
            ):
                await self._client(fake).scan_satellites(1)
            await asyncio.sleep(0)
        finally:
            loop.set_exception_handler(previous_handler)
        self.assertEqual(unhandled, [])

    async def test_esp32_transport_uses_separate_characteristics_and_128_bytes(self):
        fake = FakeBLEClient(esp32=True)
        long_url = "wss://" + "a" * 200 + "/"
        await self._client(fake).set_server(long_url)
        self.assertEqual(fake.notify_uuid, CLIENT_MODULE.ESP32_NOTIFY_UUID)
        self.assertEqual(set(fake.write_uuids), {CLIENT_MODULE.ESP32_WRITE_UUID})
        self.assertLessEqual(max(fake.write_sizes), CLIENT_MODULE.ESP32_CHUNK_SIZE)
        self.assertGreater(max(fake.write_sizes), CLIENT_MODULE.CHUNK_SIZE)

    async def test_auto_start_enables_local_list_before_writing(self):
        fake = FakeBLEClient(local_auth_enabled=False)
        await self._client(fake).set_auto_start("999999")
        self.assertEqual(
            fake.actions[1:],
            [
                ("GetConfiguration", {"key": ["LocalAuthListEnabled"]}),
                (
                    "ChangeConfiguration",
                    {"key": "LocalAuthListEnabled", "value": "true"},
                ),
                (
                    "ChangeConfiguration",
                    {"key": "evb_AutoStart", "value": "999999"},
                ),
            ],
        )

    async def test_auto_start_does_not_rewrite_enabled_local_list(self):
        fake = FakeBLEClient(local_auth_enabled=True)
        await self._client(fake).set_auto_start("false")
        self.assertEqual(
            fake.actions[1:],
            [
                ("GetConfiguration", {"key": ["LocalAuthListEnabled"]}),
                (
                    "ChangeConfiguration",
                    {"key": "evb_AutoStart", "value": "false"},
                ),
            ],
        )

    async def test_unsupported_optional_configuration_key_does_not_break_setup(self):
        fake = FakeBLEClient(reject_get_key="evb_BootInfo")
        values = await self._client(fake).get_configuration(
            ["evb_MaximumStationCurrent", "evb_BootInfo"]
        )
        self.assertEqual(
            values,
            {"evb_MaximumStationCurrent": "value-for-evb_MaximumStationCurrent"},
        )

    async def test_malformed_optional_configuration_body_is_skipped(self):
        fake = FakeBLEClient(malformed_get_key="evb_BootInfo")
        values = await self._client(fake).get_configuration(
            ["evb_MaximumStationCurrent", "evb_BootInfo"]
        )
        self.assertEqual(
            values,
            {"evb_MaximumStationCurrent": "value-for-evb_MaximumStationCurrent"},
        )

    async def test_wifi_notification_wins_over_generic_direct_acknowledgement(self):
        fake = FakeBLEClient(
            wifi_direct_response=None,
            wifi_notification="7,Home,AA:BB:CC:DD:EE:FF,6,-52,192.0.2.2",
        )
        response = await self._client(fake).set_wifi(
            ("Home", None, {"type": "WPA/WPA2 PSK"}, None, None)
        )
        self.assertEqual(PROTOCOL.wifi_status(response)["status"], "connected")

    async def test_wifi_direct_status_does_not_require_notification(self):
        direct = "4,Home,AA:BB:CC:DD:EE:FF,6,-52"
        fake = FakeBLEClient(wifi_direct_response=direct)
        response = await self._client(fake).set_wifi(
            ("Home", None, {"type": "WPA/WPA2 PSK"}, None, None)
        )
        self.assertEqual(response, direct)


if __name__ == "__main__":
    unittest.main()

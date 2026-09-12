"""BLE boundary failures and notification races without Bluetooth hardware."""

import asyncio
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch
from custom_components.evbox_g4_ble import client as c
from custom_components.evbox_g4_ble.protocol import frame_message


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_reports_missing_adapter_and_connection_failure(self):
        client = c.EVBoxClient(NS(), "AA", "123")
        with patch.object(
            c.bluetooth, "async_ble_device_from_address", return_value=None
        ):
            with self.assertRaisesRegex(c.EVBoxConnectionError, "proxy"):
                await client._connect()
        with (
            patch.object(
                c.bluetooth, "async_ble_device_from_address", return_value=object()
            ),
            patch.object(c, "establish_connection", AsyncMock()) as connect,
        ):
            self.assertIs(await client._connect(), connect.return_value)
            self.assertFalse(connect.call_args.kwargs["use_services_cache"])
            connect.side_effect = OSError("offline")
            with self.assertRaisesRegex(c.EVBoxConnectionError, "connection failed"):
                await client._connect()

    async def test_notification_markers_timeout_and_pending_failure(self):
        router = c._ResponseRouter()
        future = router.expect_marker("evbRFScan")
        router.notification(
            None,
            bytearray(
                frame_message(
                    '[2,"evt","DataTransfer",{"messageId":"evbRFScan","data":"ChargeBox.11"}]'
                )
            ),
        )
        self.assertEqual(
            await router.wait_for_marker("evbRFScan", future, 0.01), "ChargeBox.11"
        )
        self.assertEqual(router.take_event_message_id("evbRFScan"), "evt")
        client = c.EVBoxClient(NS(), "AA", "123")
        ble = NS(write_gatt_char=AsyncMock())
        router._event_message_ids["evbRFScan"] = "evt"
        await client._acknowledge_event(ble, router, "evbRFScan", "uuid", 20)
        self.assertTrue(ble.write_gatt_char.await_count)
        await client._acknowledge_event(ble, router, "missing", "uuid", 20)
        pending = asyncio.get_running_loop().create_future()
        router._pending["request"] = pending
        marker = router.expect_marker("unknown")
        router.notification(None, bytearray(frame_message("[invalid]")))
        with self.assertRaises(c.EVBoxProtocolError):
            await pending
        with self.assertRaises(c.EVBoxProtocolError):
            await marker
        router.discard_marker("unknown", marker)
        marker = router.expect_marker("cancel")
        router.discard_marker("cancel", marker)
        self.assertTrue(marker.cancelled())
        router.discard_marker("cancel", marker)
        marker = router.expect_marker("late")
        with self.assertRaises(TimeoutError):
            await router.wait_for_marker("late", marker, 0.001)
        self.assertNotIn("late", router._markers)

    async def test_request_falls_back_to_ack_when_no_final_wifi_event_arrives(self):
        router = c._ResponseRouter()

        async def write(*args, **kwargs):
            router._pending["id"].set_result("Accepted")

        with patch.object(c, "WIFI_CONNECT_TIMEOUT", 0.001):
            result = await router.request(
                NS(write_gatt_char=write), "id", "request", "uuid", 100, "wifi"
            )
        self.assertEqual(result, "Accepted")
        self.assertEqual(router._pending, {})
        with patch.object(c, "WIFI_CONNECT_TIMEOUT", 0.001):
            with self.assertRaises(TimeoutError):
                await router.request(
                    NS(write_gatt_char=AsyncMock()),
                    "id",
                    "request",
                    "uuid",
                    100,
                    "wifi",
                )
        self.assertEqual(router._markers, {})

    async def test_session_optional_failures_scan_and_cleanup(self):
        client = c.EVBoxClient(NS(), "AA", "123")
        ble = NS(
            services=NS(get_characteristic=Mock(return_value=None)),
            start_notify=AsyncMock(),
            stop_notify=AsyncMock(),
            disconnect=AsyncMock(),
            is_connected=True,
        )
        client._connect = AsyncMock(return_value=ble)
        client._authenticate = AsyncMock()
        client._evb = AsyncMock(side_effect=TimeoutError())
        client._ocpp = AsyncMock(side_effect=TimeoutError())
        self.assertEqual(
            await client.session(
                [("optional_evb", "optional", ()), ("connection_info", "", None)]
            ),
            [None, {}],
        )
        with self.assertRaises(c.EVBoxConnectionError):
            await client.session([("evb", "mandatory", ())])
        client._evb.side_effect = c.EVBoxProtocolError("rejected")
        with self.assertRaises(c.EVBoxProtocolError):
            await client.session([("evb", "mandatory", ())])
        with self.assertRaises(c.EVBoxProtocolError):
            await client.session([("unknown", "", None)])
        client._ocpp.side_effect = None
        client._ocpp.return_value = {}
        with self.assertRaises(c.EVBoxProtocolError):
            await client.session([("auto_start", "", True)])
        with patch.object(
            c._ResponseRouter,
            "wait_for_marker",
            AsyncMock(return_value="{ChargeBox,11,-40}"),
        ):
            result = await client.scan_satellites(1)
            self.assertEqual(
                result, [{"type": "ChargeBox", "id": "11", "signal_strength": -40}]
            )
        ble.is_connected = False
        ble.start_notify.side_effect = RuntimeError("notify")
        with self.assertRaises(c.EVBoxConnectionError):
            await client.session([])
        self.assertGreater(ble.disconnect.await_count, 0)

    async def test_configuration_skips_missing_and_invalid_optional_values(self):
        client = c.EVBoxClient(NS(), "AA", "123")
        client.session = AsyncMock(
            return_value=[
                None,
                "invalid",
                {"configurationKey": [{"key": "x", "value": "v"}]},
            ]
        )
        self.assertEqual(await client.get_configuration([]), {})
        self.assertEqual(
            await client.get_configuration(["missing", "invalid", "x"]), {"x": "v"}
        )
        client.session.return_value = [{"ip": "x"}]
        self.assertEqual(await client.connection_info(), {"ip": "x"})
        client.session.return_value = [True]
        self.assertTrue(await client.ocpp("Reset", {}))
        self.assertTrue(await client.evb("identify"))

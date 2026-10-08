"""Failure boundaries, missing values and firmware ownership regressions."""

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from custom_components import evbox_g4_ble as integration
from custom_components.evbox_g4_ble import button, errors, firmware_proxy_state as state
from custom_components.evbox_g4_ble import client as c, protocol as p
from custom_components.evbox_g4_ble import select, switch
from custom_components.evbox_g4_ble.client import EVBoxAuthError, EVBoxConnectionError
from custom_components.evbox_g4_ble.const import (
    KEY_AUTO_START,
    KEY_CCID,
    KEY_CCID_AC,
    KEY_METER_ADDRESS,
)
from custom_components.evbox_g4_ble.protocol import EVBoxProtocolError
from tests.test_ha_platforms import coordinator


class SilverTests(unittest.IsolatedAsyncioTestCase):
    async def test_action_transport_errors_are_translated_without_private_details(self):
        co = coordinator()
        co.hass = NS()
        entry = NS(async_start_reauth_if_available=Mock())
        co.config_entry = entry
        for failure, key, expected in (
            (
                EVBoxConnectionError("secret address"),
                "cannot_connect",
                HomeAssistantError,
            ),
            (TimeoutError("secret payload"), "cannot_connect", HomeAssistantError),
            (OSError("secret file"), "cannot_connect", HomeAssistantError),
            (
                EVBoxProtocolError("secret credentials"),
                "command_failed",
                HomeAssistantError,
            ),
            (EVBoxAuthError("secret code"), "invalid_auth", ConfigEntryAuthFailed),
        ):
            with self.subTest(key=key, failure=type(failure).__name__):
                with self.assertRaises(expected) as caught:
                    async with errors.async_device_errors(co):
                        raise failure
                self.assertEqual(caught.exception.translation_key, key)
                self.assertNotIn("secret", str(caught.exception))
        entry.async_start_reauth_if_available.assert_called_once_with(co.hass)
        co.config_entry = None
        with self.assertRaises(ConfigEntryAuthFailed):
            async with errors.async_device_errors(co):
                raise EVBoxAuthError("secret")

    async def test_validation_errors_and_cancellation_keep_their_identity(self):
        co = coordinator()
        for failure in (HomeAssistantError("validation"), asyncio.CancelledError()):
            with self.assertRaises(type(failure)) as caught:
                async with errors.async_device_errors(co):
                    raise failure
            self.assertIs(caught.exception, failure)

    async def test_services_and_buttons_use_error_boundary_and_refresh_failure_is_reported(
        self,
    ):
        co = coordinator()
        entry = NS(runtime_data=co, state=ConfigEntryState.LOADED, entry_id="entry")
        hass = NS(config_entries=NS(async_entries=Mock(return_value=[entry])))
        co.client.evb.side_effect = EVBoxConnectionError("secret")
        for operation in (
            integration._handle_service(hass, NS(service="identify", data={})),
            button.EVBoxButton(co, "AA", "identify", "identify").async_press(),
        ):
            with self.assertRaises(HomeAssistantError) as caught:
                await operation
            self.assertEqual(caught.exception.translation_key, "cannot_connect")
        co.last_update_success = False
        for operation in (
            integration._handle_service(hass, NS(service="refresh", data={})),
            button.EVBoxButton(co, "AA", "refresh", "refresh").async_press(),
        ):
            with self.assertRaises(HomeAssistantError) as caught:
                await operation
            self.assertEqual(caught.exception.translation_key, "cannot_connect")

    async def test_missing_values_are_unknown_and_recover_without_entity_recreation(
        self,
    ):
        co = coordinator()
        co.data[KEY_CCID] = "1.CCIDV2TripEU"
        entities = [
            (
                select.EVBoxChargingModeSelect(co, "AA"),
                "current_option",
                KEY_AUTO_START,
                "false",
                "rfid",
            ),
            (switch.EVBoxCCIDACSwitch(co, "AA"), "is_on", KEY_CCID_AC, "1.100", True),
            (
                switch.EVBoxConnectorMeterSwitch(co, "AA"),
                "is_on",
                KEY_METER_ADDRESS,
                "1234.1",
                True,
            ),
        ]
        for entity, prop, key, value, expected in entities:
            self.assertIsNone(getattr(entity, prop))
            self.assertTrue(entity.available)
            co.data[key] = value
            self.assertEqual(getattr(entity, prop), expected)
            co.last_update_success = False
            self.assertFalse(entity.available)
            co.last_update_success = True
            del co.data[key]
            self.assertIsNone(getattr(entity, prop))


class FirmwareOwnershipTests(unittest.TestCase):
    def test_old_worker_cannot_activate_or_release_a_new_update(self):
        registry = {}
        first = state.reserve_proxy(registry, "charger")
        self.assertIsNone(first.percentage)
        state.release_proxy(registry, "charger", first, error="start failed")
        self.assertEqual((first.phase, first.error), ("error", "start failed"))
        second = state.reserve_proxy(registry, "charger")
        with self.assertRaises(RuntimeError):
            state.activate_proxy(registry, "charger", first, object())
        state.release_proxy(registry, "charger", first)
        self.assertIs(state.get_update_state(registry, "charger"), second)
        self.assertTrue(second.in_progress)
        state.update_transfer(second, "installed", 101, 100)
        self.assertEqual(second.percentage, 100)
        state.release_proxy(registry, "charger", second)
        self.assertEqual(second.phase, "installed")


class CoordinatorLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_outage_and_recovery_are_logged_once_by_real_coordinator(self):
        from homeassistant.core import HomeAssistant
        from custom_components.evbox_g4_ble.coordinator import EVBoxCoordinator
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            hass = HomeAssistant(directory)
            client = NS(
                get_snapshot=AsyncMock(
                    side_effect=[
                        EVBoxConnectionError("private detail"),
                        EVBoxConnectionError("private detail"),
                        ({}, [None, None, None, None, {}]),
                        ({}, [None, None, None, None, {}]),
                    ]
                )
            )
            co = EVBoxCoordinator(hass, client, config_entry=None)
            try:
                with self.assertLogs(
                    "custom_components.evbox_g4_ble.coordinator", level="INFO"
                ) as logs:
                    for _ in range(4):
                        await co.async_refresh()
                self.assertEqual(len(logs.records), 2)
                self.assertIn("Error fetching", logs.records[0].getMessage())
                self.assertIn("recovered", logs.records[1].getMessage())
                self.assertNotIn("private detail", " ".join(logs.output))
                self.assertTrue(co.last_update_success)
            finally:
                await co.async_shutdown()


class CompletedNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_malformed_notification_cannot_replace_completed_results(self):
        from custom_components.evbox_g4_ble.client import _ResponseRouter
        from custom_components.evbox_g4_ble.protocol import frame_message

        router = _ResponseRouter()
        pending = asyncio.get_running_loop().create_future()
        pending.set_result("completed")
        router._pending["request"] = pending
        marker = router.expect_marker("late")
        marker.set_result("completed event")
        router.notification(None, bytearray(frame_message("[invalid]")))
        self.assertEqual(await pending, "completed")
        self.assertEqual(await marker, "completed event")


class CurrentValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_bounds_and_missing_peer_value_still_verify_write(self):
        from unittest.mock import patch
        from custom_components.evbox_g4_ble.coordinator import (
            EVBoxCoordinator,
            DataUpdateCoordinator,
        )
        from custom_components.evbox_g4_ble.const import (
            KEY_MIN_CURRENT,
            KEY_MAX_CURRENT,
        )

        client = NS(
            transaction=nullcontext, set_configuration=AsyncMock(return_value={})
        )
        for key, other, requested in (
            (KEY_MIN_CURRENT, {KEY_MAX_CURRENT: "320"}, "60"),
            (KEY_MAX_CURRENT, {KEY_MIN_CURRENT: "60"}, "320"),
            (KEY_MAX_CURRENT, {}, "160"),
        ):
            with patch.object(DataUpdateCoordinator, "__init__", return_value=None):
                co = EVBoxCoordinator(NS(), client)
            co.data = {}
            co.async_set_updated_data = Mock()
            client.get_configuration = AsyncMock(side_effect=[other, {key: requested}])
            await co.async_set_configuration(key, requested)
            self.assertEqual(
                co.async_set_updated_data.call_args.args[0][key], requested
            )


class NotificationCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_failure_retrieves_both_future_exceptions(self) -> None:
        router = c._ResponseRouter()
        loop = asyncio.get_running_loop()
        previous = loop.get_exception_handler()
        unhandled = []
        loop.set_exception_handler(lambda _loop, context: unhandled.append(context))

        async def malformed_reply(*args, **kwargs) -> None:
            router.notification(None, bytearray(p.frame_message("[invalid]")))

        try:
            with self.assertRaises(p.EVBoxProtocolError):
                await router.request(
                    NS(write_gatt_char=malformed_reply),
                    "r",
                    "request",
                    "uuid",
                    100,
                    "wifi",
                )
            await asyncio.sleep(0)
        finally:
            loop.set_exception_handler(previous)
        self.assertEqual(unhandled, [])
        self.assertEqual(router._pending, {})
        self.assertEqual(router._markers, {})

    async def test_cancelled_request_releases_pending_work(self) -> None:
        router = c._ResponseRouter()
        writing = asyncio.Event()
        captured = []

        async def blocked_write(*args, **kwargs) -> None:
            captured.extend([router._pending["r"], router._markers["wifi"]])
            writing.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(
            router.request(
                NS(write_gatt_char=blocked_write),
                "r",
                "request",
                "uuid",
                100,
                "wifi",
            )
        )
        await writing.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(all(future.cancelled() for future in captured))
        self.assertEqual(router._pending, {})
        self.assertEqual(router._markers, {})

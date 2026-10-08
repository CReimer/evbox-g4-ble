"""Regression tests for authentication, operation ordering and firmware feedback."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, PropertyMock, patch

from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.evbox_g4_ble import (
    client as c,
    config_flow as f,
    coordinator as co,
    firmware_proxy as p,
)
from custom_components.evbox_g4_ble.const import (
    CONF_SECURITY_CODE,
    KEY_RF_MODULES,
    KEY_MAX_CURRENT,
    KEY_MIN_CURRENT,
)


class AuthenticationTests(unittest.IsolatedAsyncioTestCase):
    async def test_reauth_and_reconfigure_validate_before_updating_existing_entry(self):
        for step in ("reauth_confirm", "reconfigure"):
            flow = f.EVBoxConfigFlow()
            flow.hass = NS(config_entries=NS(async_entries=Mock(return_value=[])))
            flow.context = {}
            entry = NS(data={"address": "AA", CONF_SECURITY_CODE: "old", "keep": True})
            getter = (
                "_get_reauth_entry"
                if step == "reauth_confirm"
                else "_get_reconfigure_entry"
            )
            with (
                patch.object(flow, getter, return_value=entry),
                patch.object(
                    f, "EVBoxClient", return_value=NS(evb=AsyncMock())
                ) as factory,
                patch.object(
                    flow,
                    "async_update_reload_and_abort",
                    return_value={"type": "abort"},
                ) as update,
            ):
                method = getattr(flow, "async_step_" + step)
                result = await (
                    flow.async_step_reauth(entry.data)
                    if step == "reauth_confirm"
                    else method()
                )
                self.assertEqual(result["step_id"], step)
                self.assertNotIn("old", str(result["data_schema"]))
                for error, expected in (
                    (c.EVBoxAuthError("rejected"), "invalid_auth"),
                    (c.EVBoxConnectionError("offline"), "cannot_connect"),
                ):
                    factory.return_value.evb.side_effect = error
                    result = await method({CONF_SECURITY_CODE: "new"})
                    self.assertEqual(result["errors"], {"base": expected})
                    update.assert_not_called()
                factory.return_value.evb.side_effect = None
                await method({CONF_SECURITY_CODE: "new"})
                self.assertIs(update.call_args.args[0], entry)
                self.assertEqual(
                    update.call_args.kwargs["data_updates"], {CONF_SECURITY_CODE: "new"}
                )
                self.assertEqual(entry.data[CONF_SECURITY_CODE], "old")
                factory.assert_called_with(flow.hass, "AA", "new")

    async def test_rejected_code_during_write_starts_reauth_and_releases_transaction(
        self,
    ):
        client = c.EVBoxClient(NS(), "AA", "bad")
        client.set_configuration = AsyncMock(side_effect=c.EVBoxAuthError("rejected"))
        with patch.object(co.DataUpdateCoordinator, "__init__", return_value=None):
            coordinator = co.EVBoxCoordinator(NS(), client)
        coordinator.hass = NS()
        coordinator.config_entry = NS(async_start_reauth_if_available=Mock())
        with self.assertRaises(ConfigEntryAuthFailed) as error:
            await coordinator.async_set_configuration("example", "value")
        self.assertEqual(error.exception.translation_key, "invalid_auth")
        coordinator.config_entry.async_start_reauth_if_available.assert_called_once_with(
            coordinator.hass
        )
        self.assertIsNone(client._transaction_owner)

    async def test_initial_setup_reports_rejected_code_separately(self):
        flow = f.EVBoxConfigFlow()
        flow.hass = NS(config_entries=NS(async_entries=Mock(return_value=[])))
        flow.context = {}
        with (
            patch.object(flow, "async_set_unique_id", AsyncMock()),
            patch.object(flow, "_abort_if_unique_id_configured"),
            patch.object(
                f,
                "EVBoxClient",
                return_value=NS(
                    evb=AsyncMock(side_effect=c.EVBoxAuthError("rejected"))
                ),
            ),
        ):
            result = await flow.async_step_user(
                {"address": "AA", CONF_SECURITY_CODE: "bad"}
            )
        self.assertEqual(result["errors"], {"base": "invalid_auth"})

    async def test_coordinator_authentication_failure_requests_reauth_but_offline_does_not(
        self,
    ):
        client = NS(get_snapshot=AsyncMock())
        with patch.object(co.DataUpdateCoordinator, "__init__", return_value=None):
            coordinator = co.EVBoxCoordinator(NS(), client)
        for error, expected in (
            (c.EVBoxAuthError("bad code"), ConfigEntryAuthFailed),
            (c.EVBoxConnectionError("offline"), UpdateFailed),
        ):
            client.get_snapshot.side_effect = error
            with self.assertRaises(expected):
                await coordinator._async_update_data()


class TransactionTests(unittest.IsolatedAsyncioTestCase):
    def make_coordinator(self):
        client = c.EVBoxClient(NS(), "AA", "secret")
        with patch.object(co.DataUpdateCoordinator, "__init__", return_value=None):
            coordinator = co.EVBoxCoordinator(NS(), client)
        coordinator.data = {}
        coordinator.async_set_updated_data = lambda data: setattr(
            coordinator, "data", data
        )
        return coordinator, client

    async def test_concurrent_card_additions_preserve_both_cards_and_increment_versions(
        self,
    ):
        coordinator, client = self.make_coordinator()
        cards = ["A1"]
        version = 1
        sent_versions = []

        async def evb(*args):
            snapshot = ",".join("{" + card + ",0}" for card in cards)
            await asyncio.sleep(0)
            return snapshot

        async def ocpp(action, payload):
            nonlocal version
            await asyncio.sleep(0)
            if action == "GetLocalListVersion":
                return {"listVersion": version}
            version = payload["listVersion"]
            sent_versions.append(version)
            cards.extend(item["idTag"] for item in payload["localAuthorizationList"])
            return {"status": "Accepted"}

        client.evb = evb
        client.ocpp = ocpp
        client.set_configuration = AsyncMock()
        await asyncio.wait_for(
            asyncio.gather(
                coordinator.async_add_card("B2"), coordinator.async_add_card("C3")
            ),
            1,
        )
        self.assertEqual(cards, ["A1", "B2", "C3"])
        self.assertEqual(sent_versions, [2, 3])
        self.assertEqual(
            coordinator.data["cards"], [{"id_tag": card} for card in cards]
        )

    async def test_concurrent_satellite_changes_use_authoritative_list(self):
        coordinator, client = self.make_coordinator()
        stored = "ChargeBox.1"

        async def read(keys):
            await asyncio.sleep(0)
            return {KEY_RF_MODULES: stored}

        async def write(key, value):
            nonlocal stored
            await asyncio.sleep(0)
            stored = value

        client.get_configuration = read
        client.set_configuration = write
        coordinator.data[KEY_RF_MODULES] = "ChargeBox.stale"
        await asyncio.wait_for(
            asyncio.gather(
                coordinator.async_update_rf_modules(add=("ChargeBox", "2")),
                coordinator.async_update_rf_modules(add=("ChargeBox", "3")),
            ),
            1,
        )
        self.assertEqual(stored, "ChargeBox.1,ChargeBox.2,ChargeBox.3")
        await coordinator.async_update_rf_modules(remove_id="2")
        self.assertEqual(stored, "ChargeBox.1,ChargeBox.3")
        await coordinator.async_update_rf_modules(add=("ChargeBox", "3"))
        self.assertEqual(stored, "ChargeBox.1,ChargeBox.3")
        stored = ",".join(f"ChargeBox.{n}" for n in range(10))
        with self.assertRaises(HomeAssistantError) as error:
            await coordinator.async_update_rf_modules(add=("ChargeBox", "99"))
        self.assertEqual(error.exception.translation_key, "max_satellites")
        client.get_configuration = AsyncMock(return_value={})
        with self.assertRaises(HomeAssistantError) as error:
            await coordinator.async_update_rf_modules(add=("ChargeBox", "99"))
        self.assertEqual(error.exception.translation_key, "satellites_missing")

    async def test_current_limit_validation_uses_readback_instead_of_stale_ui(self):
        coordinator, client = self.make_coordinator()
        client.set_configuration = AsyncMock()
        for key, requested, other_key, stored, expected in (
            (KEY_MIN_CURRENT, 160, KEY_MAX_CURRENT, "120", "minimum_above_maximum"),
            (KEY_MAX_CURRENT, 100, KEY_MIN_CURRENT, "160", "maximum_below_minimum"),
        ):
            client.get_configuration = AsyncMock(return_value={other_key: stored})
            with self.assertRaises(HomeAssistantError) as error:
                await coordinator.async_set_configuration(key, requested)
            self.assertEqual(error.exception.translation_key, expected)
        client.set_configuration.assert_not_awaited()

    async def test_backend_write_keeps_url_and_online_switch_together(self):
        coordinator, client = self.make_coordinator()
        values = {"evb_ServerURL": "wss://old/", "evb_UseBackend": "false"}

        async def server(url):
            await asyncio.sleep(0)
            values["evb_ServerURL"] = url

        async def setting(key, value):
            values[key] = value

        async def read(keys):
            return {key: values[key] for key in keys}

        client.set_server = server
        client.set_configuration = setting
        client.get_configuration = read
        await asyncio.wait_for(coordinator.async_set_backend(True, "wss://new/"), 1)
        self.assertEqual(
            values, {"evb_ServerURL": "wss://new/", "evb_UseBackend": True}
        )
        await coordinator.async_set_backend(False)
        self.assertFalse(values["evb_UseBackend"])

    async def test_transaction_releases_after_cancellation_and_blocks_other_tasks(self):
        _, client = self.make_coordinator()
        entered = asyncio.Event()
        order = []

        async def first():
            async with client.transaction():
                async with client.transaction():
                    order.append("first")
                    entered.set()
                    await asyncio.Event().wait()

        async def second():
            async with client.transaction():
                order.append("second")

        owner = asyncio.create_task(first())
        await entered.wait()
        waiter = asyncio.create_task(second())
        await asyncio.sleep(0)
        self.assertEqual(order, ["first"])
        owner.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await owner
        await asyncio.wait_for(waiter, 1)
        self.assertEqual(order, ["first", "second"])
        self.assertIsNone(client._transaction_owner)

    async def test_regular_refresh_uses_one_session_for_configuration_and_diagnostics(
        self,
    ):
        _, client = self.make_coordinator()
        payload = {"configurationKey": [{"key": "setting", "value": "value"}]}
        diagnostics = ["status", "network", "led", "cards", {"connection": "wifi"}]
        client.session = AsyncMock(return_value=[payload, *diagnostics])
        config, results = await client.get_snapshot(["setting"])
        self.assertEqual(config, {"setting": "value"})
        self.assertEqual(results, diagnostics)
        client.session.assert_awaited_once()
        self.assertEqual(len(client.session.call_args.args[0]), 6)


class FirmwareFeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_route_prevents_firmware_download(self):
        hass = NS(
            data={}, async_add_executor_job=AsyncMock(side_effect=OSError("no route"))
        )
        with (
            patch.object(p, "async_dispatcher_send"),
            patch.object(p, "_download_firmware", AsyncMock()) as download,
        ):
            with self.assertRaises(HomeAssistantError) as error:
                await p._async_create_proxy(
                    hass, "https://example.org/fw.evb", "192.0.2.2"
                )
            self.assertEqual(error.exception.translation_key, "ftp_start_failed")
            download.assert_not_awaited()
            self.assertFalse(p.firmware_update_in_progress(hass, "192.0.2.2"))

    async def test_expiry_distinguishes_ftp_unreachable_and_installation_unconfirmed(
        self,
    ):
        for phase, error in (
            ("waiting_for_charger", "ftp_unreachable"),
            ("waiting_for_installation", "installation_unconfirmed"),
        ):
            proxy = p._FirmwareProxy(
                NS(),
                Path("unused"),
                NS(),
                "ftp://unused",
                "192.0.2.2",
                p.FirmwareUpdateState(phase=phase),
            )
            proxy.async_close = AsyncMock()
            with patch.object(p.asyncio, "sleep", AsyncMock()):
                await proxy._async_expire()
            proxy.async_close.assert_awaited_once_with(error)

    def test_installation_requires_completed_transfer_and_changed_target_version(self):
        from custom_components.evbox_g4_ble import update as u
        from tests.test_ha_platforms import coordinator

        coordinator = coordinator()
        catalog = NS(data={"version": "426v1"})
        entity = u.EVBoxFirmwareUpdate(NS(data={}), coordinator, catalog, "AA")
        state = p.FirmwareUpdateState(initial_version="424v1", target_version="425v1")
        with (
            patch.object(entity, "_local_update_state", return_value=state),
            patch.object(u.EVBoxEntity, "_handle_coordinator_update"),
            patch.object(u, "mark_firmware_installed") as installed,
            patch.object(
                u.EVBoxFirmwareUpdate, "installed_version", new_callable=PropertyMock
            ) as version,
        ):
            version.return_value = "425v1"
            for phase in ("preparing", "downloading", "error", "installed"):
                state.phase = phase
                entity._handle_coordinator_update()
            installed.assert_not_called()
            state.phase = "waiting_for_installation"
            for unchanged_or_wrong in ("424v1", "426v1", None):
                version.return_value = unchanged_or_wrong
                entity._handle_coordinator_update()
            installed.assert_not_called()
            version.return_value = "425v1"
            entity._handle_coordinator_update()
            installed.assert_called_once()

    def test_non_unicast_charger_addresses_are_rejected(self):
        for address in ("::1", "127.0.0.1", "0.0.0.0", "224.0.0.1"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                p._route_ipv4(address)

    def test_exception_keys_and_placeholders_have_english_and_german_translations(self):
        import ast
        import string

        root = Path(__file__).parents[1] / "custom_components/evbox_g4_ble"
        en = json.loads((root / "translations/en.json").read_text())
        de = json.loads((root / "translations/de.json").read_text())
        self.assertEqual(set(en["exceptions"]), set(de["exceptions"]))
        for file in root.glob("*.py"):
            for node in ast.walk(ast.parse(file.read_text())):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in ("HomeAssistantError", "ConfigEntryAuthFailed")
                ):
                    continue
                for keyword in node.keywords:
                    if keyword.arg == "translation_key" and isinstance(
                        keyword.value, ast.Constant
                    ):
                        key = keyword.value.value
                        self.assertIn(key, en["exceptions"], (file.name, key))
                        placeholders = next(
                            (
                                kw.value
                                for kw in node.keywords
                                if kw.arg == "translation_placeholders"
                            ),
                            None,
                        )
                        supplied = (
                            {key.value for key in placeholders.keys}
                            if placeholders
                            else set()
                        )
                        for translated in (en, de):
                            required = {
                                name
                                for _, name, _, _ in string.Formatter().parse(
                                    translated["exceptions"][key]["message"]
                                )
                                if name
                            }
                            self.assertEqual(required, supplied, key)

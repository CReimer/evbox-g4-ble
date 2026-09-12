"""Firmware bridge and update entity tests without vendor or charger access."""

import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch
import aiohttp
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed
from custom_components.evbox_g4_ble import firmware_proxy as p, update as u
from custom_components.evbox_g4_ble.const import CONF_ADDRESS, KEY_BOOT_INFO
from tests.test_ha_platforms import coordinator


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        async def executor(func, *args):
            return func(*args)

        self.hass = NS(
            data={},
            config=NS(path=lambda *args: self.temp.name),
            async_add_executor_job=executor,
            async_create_task=lambda coro, name: asyncio.create_task(coro),
        )
        self.dispatch = patch.object(p, "async_dispatcher_send")
        self.dispatch.start()
        self.addCleanup(self.dispatch.stop)

    async def test_download_size_empty_and_http_errors(self):
        async def chunks(size):
            yield b"firmware"

        response = NS(
            raise_for_status=Mock(), content_length=8, content=NS(iter_chunked=chunks)
        )
        context = AsyncMock()
        context.__aenter__.return_value = response
        with patch.object(
            p,
            "async_get_clientsession",
            return_value=NS(get=Mock(return_value=context)),
        ):
            self.assertEqual(
                await p._download_firmware(self.hass, "https://example.org"),
                b"firmware",
            )
            response.content_length = p._MAX_FIRMWARE_SIZE + 1
            with self.assertRaisesRegex(HomeAssistantError, "64 MiB"):
                await p._download_firmware(self.hass, "https://example.org")
            response.content_length = None
            with (
                patch.object(p, "_MAX_FIRMWARE_SIZE", 1),
                self.assertRaises(HomeAssistantError),
            ):
                await p._download_firmware(self.hass, "https://example.org")

            async def empty(size):
                for chunk in []:
                    yield chunk

            response.content.iter_chunked = empty
            with self.assertRaisesRegex(HomeAssistantError, "leer"):
                await p._download_firmware(self.hass, "https://example.org")
            for error in (aiohttp.ClientError("http"), TimeoutError()):
                context.__aenter__.side_effect = error
                with self.assertRaisesRegex(HomeAssistantError, "heruntergeladen"):
                    await p._download_firmware(self.hass, "https://example.org")
        sock = Mock()
        sock.getsockname.return_value = ("192.0.2.1", 100)
        with patch.object(p.socket, "socket") as factory:
            factory.return_value.__enter__.return_value = sock
            self.assertEqual(p._route_ipv4("192.0.2.2"), "192.0.2.1")
        with self.assertRaises(ValueError):
            p._route_ipv4("::1")

    async def test_create_transfer_completion_and_expiry_cleanup(self):
        server = NS(start=AsyncMock(), close=AsyncMock(), server_port=1234)
        with (
            patch.object(p, "_download_firmware", AsyncMock(return_value=b"firmware")),
            patch.object(p, "_route_ipv4", return_value="192.0.2.1"),
            patch.object(p, "EVBoxFTPServer", return_value=server) as factory,
        ):
            proxy = await p._async_create_proxy(
                self.hass, "https://example.org", "192.0.2.2"
            )
            self.assertTrue(proxy.location.startswith("ftp://evbox:"))
            self.assertEqual(next(proxy.directory.iterdir()).read_bytes(), b"firmware")
            self.assertTrue(p.firmware_update_in_progress(self.hass, "192.0.2.2"))
            with self.assertRaisesRegex(HomeAssistantError, "bereits"):
                await p._async_create_proxy(
                    self.hass, "https://example.org", "192.0.2.2"
                )
            report = factory.call_args.kwargs["transfer_callback"]
            report("downloading", 1, 10, None)
            report("downloading", 1, 10, None)
            self.assertEqual(proxy.state.percentage, 10)
            report("error", 1, 0, "connection lost")
            self.assertEqual(proxy.state.error, "connection lost")
            p.mark_firmware_installed(self.hass, "missing")
            p.mark_firmware_installed(self.hass, "192.0.2.2")
            p.mark_firmware_installed(self.hass, "192.0.2.2")
            self.assertEqual(proxy.state.phase, "installed")
            with patch.object(p.asyncio, "sleep", AsyncMock()):
                proxy.arm_cleanup()
                await proxy._cleanup_task
            self.assertFalse(proxy.directory.exists())
            await proxy.async_close()
            server.close.assert_awaited_once()
        stale = Path(self.temp.name) / "evbox-firmware-stale"
        stale.mkdir()
        (Path(self.temp.name) / "evbox-firmware-file").write_text("keep")
        await p.async_cleanup_firmware_proxies(self.hass)
        self.assertFalse(stale.exists())
        self.assertTrue((Path(self.temp.name) / "evbox-firmware-file").exists())

    async def test_proxy_failures_release_reservation_and_files(self):
        for where in ("download", "server"):
            for error in (OSError("io"), RuntimeError("unexpected")):
                self.hass.data = {}
                server = NS(
                    start=AsyncMock(side_effect=error if where == "server" else None)
                )
                with (
                    patch.object(
                        p,
                        "_download_firmware",
                        AsyncMock(
                            return_value=b"firmware",
                            side_effect=error if where == "download" else None,
                        ),
                    ),
                    patch.object(p, "_route_ipv4", return_value="192.0.2.1"),
                    patch.object(p, "EVBoxFTPServer", return_value=server),
                ):
                    with self.assertRaises(
                        HomeAssistantError
                        if isinstance(error, OSError)
                        else RuntimeError
                    ):
                        await p._async_create_proxy(
                            self.hass, "https://example.org", "192.0.2.2"
                        )
                self.assertFalse(p.firmware_update_in_progress(self.hass, "192.0.2.2"))
                self.assertEqual(list(Path(self.temp.name).iterdir()), [])
        fake = NS(async_close=AsyncMock())
        state = p.reserve_proxy(p._proxy_registry(self.hass), "other")
        p.activate_proxy(p._proxy_registry(self.hass), "other", state, fake)
        await p.async_cleanup_firmware_proxies(self.hass)
        fake.async_close.assert_awaited_once()

    async def test_update_url_dispatch_and_command_failure(self):
        co = coordinator()
        for url in ("invalid", "https://example.org"):
            with self.assertRaises(HomeAssistantError):
                await p.async_start_firmware_update(self.hass, co, url)
        result, proxied = await p.async_start_firmware_update(
            self.hass, co, "ftp://example.org/fw.evb"
        )
        self.assertFalse(proxied)
        proxy = NS(
            location="ftp://example.org/fw.evb",
            async_close=AsyncMock(),
            arm_cleanup=Mock(),
        )
        with (
            patch.object(p, "wifi_status", return_value={"ip_address": "192.0.2.2"}),
            patch.object(p, "_async_create_proxy", AsyncMock(return_value=proxy)),
        ):
            self.assertTrue(
                (
                    await p.async_start_firmware_update(
                        self.hass, co, "https://example.org"
                    )
                )[1]
            )
            proxy.arm_cleanup.assert_called_once()
            co.client.ocpp.side_effect = RuntimeError("command")
            with self.assertRaises(RuntimeError):
                await p.async_start_firmware_update(
                    self.hass, co, "https://example.org"
                )
            proxy.async_close.assert_awaited_once_with("Firmware update command failed")
        with self.assertRaises(RuntimeError):
            await p.async_start_firmware_update(
                self.hass, co, "ftp://example.org/fw.evb"
            )


class UpdateTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_initial_state_and_response_validation(self):
        with (
            patch.object(u.DataUpdateCoordinator, "__init__", return_value=None),
            patch.object(
                u.EVBoxFirmwareCatalogCoordinator, "async_set_updated_data"
            ) as initial,
        ):
            catalog = u.EVBoxFirmwareCatalogCoordinator(NS())
            self.assertTrue(initial.call_args.args[0]["version"])
        catalog.hass = NS()
        response = NS(
            raise_for_status=Mock(),
            headers={"Content-Disposition": 'attachment; filename="P0425v1.evb"'},
            content=NS(read=AsyncMock()),
        )
        context = AsyncMock()
        context.__aenter__.return_value = response
        with patch.object(
            u,
            "async_get_clientsession",
            return_value=NS(get=Mock(return_value=context)),
        ):
            self.assertEqual((await catalog._async_update_data())["version"], "425v1")
            response.content.read.assert_awaited_once_with(1)
            response.headers = {}
            with self.assertRaises(UpdateFailed):
                await catalog._async_update_data()
            for error in (aiohttp.ClientError("http"), TimeoutError()):
                context.__aenter__.side_effect = error
                with self.assertRaises(UpdateFailed):
                    await catalog._async_update_data()

    async def test_entity_reports_only_verified_installation(self):
        co = coordinator()
        co.data[KEY_BOOT_INFO] = "EVBox,G4E-test,serial,P0425v1,iccid,imsi"
        catalog = NS(
            data={"version": "425v1", "url": "https://example.org"},
            async_add_listener=Mock(),
            async_refresh=AsyncMock(),
        )
        hass = NS(data={})
        entities = []
        entry = NS(runtime_data=co, data={CONF_ADDRESS: "AA"})
        with patch.object(u, "EVBoxFirmwareCatalogCoordinator", return_value=catalog):
            await u.async_setup_entry(hass, entry, entities.extend)
            await u.async_setup_entry(hass, entry, entities.extend)
            catalog.async_refresh.assert_awaited_once()
        entity = entities[0]
        self.assertEqual(entity.installed_version, "425v1")
        self.assertEqual(entity.latest_version, "425v1")
        self.assertTrue(entity.available)
        self.assertFalse(entity.in_progress)
        self.assertIsNone(entity.update_percentage)
        self.assertIn("öffentlich", entity.release_summary)
        self.assertIn("model", entity.extra_state_attributes)
        state = p.FirmwareUpdateState(total_bytes=100, transferred_bytes=50)
        with (
            patch.object(u, "wifi_status", return_value={"ip_address": "192.0.2.2"}),
            patch.object(u, "firmware_update_state", return_value=state),
            patch.object(entity, "async_write_ha_state"),
            patch.object(u, "mark_firmware_installed") as installed,
        ):
            self.assertTrue(entity.in_progress)
            self.assertEqual(entity.update_percentage, 50)
            self.assertEqual(entity.extra_state_attributes["total_bytes"], 100)
            entity._handle_coordinator_update()
            installed.assert_called_once_with(hass, "192.0.2.2")
            state.phase = "installed"
            entity._handle_coordinator_update()
            installed.assert_called_once()
            co.last_update_success = False
            self.assertTrue(entity.available)
        with patch.object(u, "async_start_firmware_update", AsyncMock()) as install:
            await entity.async_install(None, False)
            install.assert_awaited_once()
        with self.assertRaises(ValueError):
            await entity.async_install(None, True)
        catalog.data = {}
        with self.assertRaises(ValueError):
            await entity.async_install(None, False)
        self.assertIsNone(entity.latest_version)
        co.data.clear()
        self.assertIsNone(entity.latest_version)
        self.assertIsNone(entity.installed_version)
        with (
            patch.object(u.EVBoxEntity, "async_added_to_hass", AsyncMock()),
            patch.object(u, "async_dispatcher_connect", return_value=Mock()),
            patch.object(entity, "async_on_remove") as remove,
        ):
            await entity.async_added_to_hass()
            self.assertEqual(remove.call_count, 2)

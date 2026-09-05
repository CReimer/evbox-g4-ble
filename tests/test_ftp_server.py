"""FTP compatibility tests for the EVBox firmware downloader."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import aioftp

_SPEC = importlib.util.spec_from_file_location(
    "evbox_ftp_server",
    Path(__file__).parents[1]
    / "custom_components"
    / "evbox_g4_ble"
    / "ftp_server.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

EVBoxFTPServer = _MODULE.EVBoxFTPServer


class EVBoxFTPServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_size_precedes_successful_firmware_download(self):
        firmware = b"firmware-test\r\n"
        transfer_events = []
        event_loop_thread = threading.get_ident()
        open_threads: list[int] = []
        original_open = Path.open

        def checked_open(path, *args, **kwargs):
            thread = threading.get_ident()
            open_threads.append(thread)
            if thread == event_loop_thread:
                raise RuntimeError("blocking open in the event loop")
            return original_open(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "update.evb").write_bytes(firmware)
            user = aioftp.User(
                "evbox",
                "secret",
                base_path=directory,
                permissions=[
                    aioftp.Permission("/", readable=True, writable=False)
                ],
            )
            server = EVBoxFTPServer(
                [user],
                transfer_callback=lambda *event: transfer_events.append(
                    event
                ),
            )
            await server.start("127.0.0.1", 0)
            try:
                with patch.object(Path, "open", checked_open):
                    async with aioftp.Client.context(
                        "127.0.0.1", server.server_port, "evbox", "secret"
                    ) as client:
                        code, lines = await client.command(
                            "SIZE update.evb", expected_codes="213"
                        )
                        self.assertEqual(str(code), "213")
                        self.assertEqual(lines[-1].strip(), str(len(firmware)))
                        stream = await client.download_stream("update.evb")
                        downloaded = await stream.read()
                        await stream.finish()
            finally:
                await server.close()
        self.assertEqual(downloaded, firmware)
        self.assertTrue(open_threads)
        self.assertNotIn(event_loop_thread, open_threads)
        self.assertEqual(transfer_events[0], ("downloading", 0, 15, None))
        self.assertEqual(
            transfer_events[-1],
            ("waiting_for_installation", 15, 15, None),
        )

    async def test_size_rejects_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            user = aioftp.User("evbox", "secret", base_path=directory)
            server = EVBoxFTPServer([user])
            await server.start("127.0.0.1", 0)
            try:
                async with aioftp.Client.context(
                    "127.0.0.1", server.server_port, "evbox", "secret"
                ) as client:
                    code, _lines = await client.command(
                        "SIZE missing.evb", expected_codes="550"
                    )
            finally:
                await server.close()
        self.assertEqual(str(code), "550")

    async def test_abort_reports_incomplete_transfer_as_error(self):
        firmware = b"x" * (1024 * 1024)
        transfer_events = []
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "update.evb").write_bytes(firmware)
            user = aioftp.User(
                "evbox",
                "secret",
                base_path=directory,
                permissions=[
                    aioftp.Permission("/", readable=True, writable=False)
                ],
                read_speed_limit_per_connection=1024,
            )
            server = EVBoxFTPServer(
                [user],
                block_size=1024,
                transfer_callback=lambda *event: transfer_events.append(
                    event
                ),
            )
            await server.start("127.0.0.1", 0)
            try:
                async with aioftp.Client.context(
                    "127.0.0.1", server.server_port, "evbox", "secret"
                ) as client:
                    stream = await client.download_stream("update.evb")
                    async with asyncio.timeout(1):
                        while not any(
                            event[1] > 0 for event in transfer_events
                        ):
                            await asyncio.sleep(0)
                    await client.command("ABOR", expected_codes="426")
                    await client.command(expected_codes="226")
                    stream.close()
                    await asyncio.sleep(0)
            finally:
                await server.close()

        phase, transferred, total, error = transfer_events[-1]
        self.assertEqual(phase, "error")
        self.assertLess(transferred, total)
        self.assertEqual(error, "FTP transfer aborted by charger")


if __name__ == "__main__":
    unittest.main()

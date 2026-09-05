"""FTP compatibility tests for the EVBox firmware downloader."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

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
            server = EVBoxFTPServer([user])
            await server.start("127.0.0.1", 0)
            try:
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


if __name__ == "__main__":
    unittest.main()

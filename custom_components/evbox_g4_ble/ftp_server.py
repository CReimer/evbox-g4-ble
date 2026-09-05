"""FTP compatibility required by EVBox Gen4 firmware clients."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import PurePosixPath

import aioftp


class EVBoxFTPServer(aioftp.Server):
    """An aioftp server with the SIZE command expected by EVBox firmware."""

    def __init__(
        self,
        *args,
        transfer_callback: Callable[
            [str, int, int, str | None], None
        ]
        | None = None,
        **kwargs,
    ) -> None:
        # Home Assistant raises when synchronous filesystem access happens in
        # its event loop. aioftp's default PathIO performs all operations there,
        # including opening and reading a RETR payload.
        kwargs.setdefault("path_io_factory", aioftp.AsyncPathIO)
        super().__init__(*args, **kwargs)
        self._transfer_callback = transfer_callback
        self.commands_mapping["size"] = self.size

    def _report_transfer(
        self,
        phase: str,
        transferred_bytes: int,
        total_bytes: int,
        error: str | None = None,
    ) -> None:
        if self._transfer_callback is not None:
            self._transfer_callback(
                phase, transferred_bytes, total_bytes, error
            )

    @aioftp.ConnectionConditions(aioftp.ConnectionConditions.login_required)
    @aioftp.PathConditions(
        aioftp.PathConditions.path_must_exists,
        aioftp.PathConditions.path_must_be_file,
    )
    @aioftp.PathPermissions(aioftp.PathPermissions.readable)
    async def size(self, connection, rest: str | PurePosixPath) -> bool:
        """Return a file size as specified by RFC 3659 section 4."""
        real_path, _virtual_path = self.get_paths(connection, rest)
        details = await connection.path_io.stat(real_path)
        connection.response("213", str(details.st_size))
        return True

    @aioftp.ConnectionConditions(
        aioftp.ConnectionConditions.login_required,
        aioftp.ConnectionConditions.passive_server_started,
    )
    @aioftp.PathConditions(
        aioftp.PathConditions.path_must_exists,
        aioftp.PathConditions.path_must_be_file,
    )
    @aioftp.PathPermissions(aioftp.PathPermissions.readable)
    async def retr(self, connection, rest: str | PurePosixPath) -> bool:
        """Send firmware while reporting progress and terminal failures."""
        real_path, _virtual_path = self.get_paths(connection, rest)
        details = await connection.path_io.stat(real_path)
        total_bytes = details.st_size
        error_reported = False

        @aioftp.ConnectionConditions(
            aioftp.ConnectionConditions.data_connection_made,
            wait=True,
            fail_code="425",
            fail_info="Can't open data connection",
        )
        async def transfer(server, current, path) -> bool:
            nonlocal error_reported
            transferred_bytes = 0
            try:
                stream = current.data_connection
                del current.data_connection
                file_in = current.path_io.open(real_path, mode="rb")
                async with file_in, stream:
                    if current.restart_offset:
                        await file_in.seek(current.restart_offset)
                        transferred_bytes = current.restart_offset
                    async for data in file_in.iter_by_block(
                        current.block_size
                    ):
                        await stream.write(data)
                        transferred_bytes += len(data)
                        server._report_transfer(
                            "downloading",
                            transferred_bytes,
                            total_bytes,
                        )
                current.response("226", "data transfer done")
                server._report_transfer(
                    "waiting_for_installation", total_bytes, total_bytes
                )
                return True
            except asyncio.CancelledError:
                error_reported = True
                server._report_transfer(
                    "error",
                    transferred_bytes,
                    total_bytes,
                    "FTP transfer aborted by charger",
                )
                current.response("426", "transfer aborted")
                current.response("226", "abort successful")
                return False
            except Exception as err:
                error_reported = True
                server._report_transfer(
                    "error",
                    transferred_bytes,
                    total_bytes,
                    f"FTP transfer failed: {err}",
                )
                raise

        async def tracked_transfer() -> None:
            result = await transfer(self, connection, rest)
            if result is False and not error_reported:
                self._report_transfer(
                    "error",
                    0,
                    total_bytes,
                    "FTP data connection could not be opened",
                )

        self._report_transfer("downloading", 0, total_bytes)
        task = asyncio.create_task(tracked_transfer())
        connection.extra_workers.add(task)
        connection.response("150", "data transfer started")
        return True

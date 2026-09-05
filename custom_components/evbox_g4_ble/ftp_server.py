"""FTP compatibility required by EVBox Gen4 firmware clients."""

from __future__ import annotations

from pathlib import PurePosixPath

import aioftp


class EVBoxFTPServer(aioftp.Server):
    """An aioftp server with the SIZE command expected by EVBox firmware."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.commands_mapping["size"] = self.size

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

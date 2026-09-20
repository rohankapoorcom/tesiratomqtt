"""Represents the telnet connections between this program and Biamp Tesira device."""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import TYPE_CHECKING

import telnetlib3

from errors import ClientConnectionError, ClientTimeoutError

if TYPE_CHECKING:
    from telnetlib3.stream_reader import TelnetReader
    from telnetlib3.stream_writer import TelnetWriter

_LOGGER = logging.getLogger(__name__)

BANNER = "Welcome to the Tesira Text Protocol Server"

# Wide enough that the Tesira never wraps echoed commands.
_TERMINAL_COLS = 1310
_TERMINAL_ROWS = 125


class BiampTesiraTelnetConnection:
    """Line-oriented telnet session to a Tesira."""

    def __init__(
        self, reader: TelnetReader, writer: TelnetWriter, identifier: str
    ) -> None:
        """Wrap a telnetlib3 reader/writer pair."""
        self.reader: TelnetReader | None = reader
        self.writer: TelnetWriter | None = writer
        self.identifier = identifier

    @classmethod
    async def connect(
        cls, host: str, port: int, identifier: str, timeout_seconds: float
    ) -> BiampTesiraTelnetConnection:
        """Connect and wait for the welcome banner."""
        try:
            reader, writer = await asyncio.wait_for(
                telnetlib3.open_connection(
                    host,
                    port,
                    encoding="utf8",
                    cols=_TERMINAL_COLS,
                    rows=_TERMINAL_ROWS,
                ),
                timeout=timeout_seconds,
            )
        except TimeoutError as error:
            msg = f"Timeout connecting to {host}:{port}"
            raise ClientTimeoutError(msg) from error
        except OSError as error:
            msg = f"Failed to connect to {host}:{port}"
            raise ClientConnectionError(msg) from error

        connection = cls(reader, writer, identifier)
        connection._enable_tcp_keepalive()

        try:
            await asyncio.wait_for(
                connection._wait_for_banner(), timeout=timeout_seconds
            )
        except TimeoutError as error:
            connection.close()
            msg = f"Timeout waiting for the Tesira banner from {host}:{port}"
            raise ClientTimeoutError(msg) from error
        except ClientConnectionError:
            connection.close()
            raise

        _LOGGER.debug("%s - Connected to %s:%s", identifier, host, port)
        return connection

    def _enable_tcp_keepalive(self) -> None:
        """Enable TCP keepalive so dead peers are noticed."""
        if self.writer is None:
            return
        sock = self.writer.transport.get_extra_info("socket")
        if sock is None:
            return
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            for name, value in (
                ("TCP_KEEPIDLE", 30),
                ("TCP_KEEPINTVL", 10),
                ("TCP_KEEPCNT", 3),
            ):
                option = getattr(socket, name, None)
                if option is not None:
                    sock.setsockopt(socket.IPPROTO_TCP, option, value)
        except OSError:
            _LOGGER.debug("%s - Unable to enable TCP keepalive", self.identifier)

    async def _wait_for_banner(self) -> None:
        """Skip the terminal preamble up to and including the banner."""
        while True:
            line = await self.readline()
            if BANNER in line:
                return

    def close(self) -> None:
        """Close the connection."""
        if self.writer is not None and not self.writer.is_closing():
            self.writer.close()
        self.writer = None
        self.reader = None

    @property
    def closed(self) -> bool:
        """Return whether the connection is closed."""
        return self.writer is None or self.writer.is_closing()

    async def write(self, command: str) -> None:
        """Send a command."""
        if self.writer is None or self.writer.is_closing():
            msg = "Client not connected."
            raise ClientConnectionError(msg)

        try:
            self.writer.write(command + "\r\n")
            await self.writer.drain()
        except OSError as err:
            raise ClientConnectionError from err

    async def readline(self) -> str:
        """Read one line, stripped of its CR LF or CR NUL terminator."""
        reader = self.reader
        if reader is None or self.closed:
            msg = "Client not connected."
            raise ClientConnectionError(msg)

        try:
            data = await reader.readline()
        except (OSError, asyncio.IncompleteReadError) as err:
            raise ClientConnectionError from err

        if not data and reader.at_eof():
            msg = f"{self.identifier} - Connection closed by the Tesira"
            raise ClientConnectionError(msg)

        cleaned_data = data.replace("\x00", "").strip("\r\n")  # type: ignore  # noqa: PGH003
        if cleaned_data:
            _LOGGER.debug("%s - Received %s", self.identifier, cleaned_data)
        return cleaned_data

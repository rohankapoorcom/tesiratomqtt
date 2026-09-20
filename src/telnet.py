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

# The Tesira sends this once telnet negotiation is complete. Anything before it
# (for example "No entry for terminal type ...") is terminal noise to be ignored.
BANNER = "Welcome to the Tesira Text Protocol Server"

# The Tesira wraps echoed input at the negotiated terminal width; a very wide
# terminal keeps long subscribe commands on a single line.
_TERMINAL_COLS = 1310
_TERMINAL_ROWS = 125


class BiampTesiraTelnetConnection:
    """
    Telnet client used for communications.

    This is a thin line-oriented wrapper over telnetlib3. It knows nothing about
    the Tesira Text Protocol itself other than how to wait for the welcome
    banner; classifying lines is the caller's job.
    """

    def __init__(
        self, reader: TelnetReader, writer: TelnetWriter, identifier: str
    ) -> None:
        """Wrap an established telnetlib3 reader/writer pair."""
        self.reader: TelnetReader | None = reader
        self.writer: TelnetWriter | None = writer
        self.identifier = identifier

    @classmethod
    async def connect(
        cls, host: str, port: int, identifier: str, timeout_seconds: float
    ) -> BiampTesiraTelnetConnection:
        """
        Open a telnet session and wait for the Tesira welcome banner.

        Args:
            host: Tesira host name or IP address.
            port: Telnet port (normally 23).
            identifier: Name used in log messages to tell sessions apart.
            timeout_seconds: Maximum seconds to wait for the connection and the banner.

        Raises:
            ClientTimeoutError: The connection or the banner took too long.
            ClientConnectionError: The connection could not be established.

        """
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
        """Ask the kernel to probe idle connections so dead peers are noticed."""
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
        """Consume lines until the welcome banner has been seen."""
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
        """
        Send a command terminated with CR LF.

        Args:
            command: The command to send.

        """
        if self.writer is None or self.writer.is_closing():
            msg = "Client not connected."
            raise ClientConnectionError(msg)

        try:
            self.writer.write(command + "\r\n")
            await self.writer.drain()
        except OSError as err:
            raise ClientConnectionError from err

    async def readline(self) -> str:
        """
        Read one line from the Tesira.

        The Tesira terminates lines with either CR LF or CR NUL. telnetlib3
        yields at any of those (and at a lone CR when the LF has not arrived
        yet, in which case the following LF shows up as an empty line). All
        terminators and NUL padding are stripped, so callers only ever see the
        textual content of a line, possibly empty.

        Raises:
            ClientConnectionError: The connection was closed by the peer.

        """
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

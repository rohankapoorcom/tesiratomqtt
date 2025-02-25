"""Represents the telnet connections between this program and Biamp Tesira device."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from errors import ClientConnectionError, ClientTimeoutError

if TYPE_CHECKING:
    from telnetlib3.stream_reader import TelnetReader
    from telnetlib3.stream_writer import TelnetWriter

_LOGGER = logging.getLogger(__name__)


@dataclass
class BiampTesiraTelnetConnection:
    """Telnet client used for communications."""

    reader: TelnetReader | None
    writer: TelnetWriter | None
    identifier: str

    def close(self) -> None:
        """Close the connection."""
        if self.writer is not None and not self.writer.is_closing():
            self.writer.close()
            self.writer = None

    @property
    def closed(self) -> bool:
        """Return whether the connection is closed."""
        return self.writer is None or self.writer.is_closing()

    async def write(self, command: str) -> None:
        """
        Send a command.

        Args:
            command: The command to send.

        Returns:
            None

        """
        # Make sure we're connected
        if self.writer is None or self.writer.is_closing():
            msg = "Client not connected."
            raise ClientConnectionError(msg)

        # Send the request
        try:
            self.writer.write(command + "\r\n")
            await self.writer.drain()
        except OSError as err:
            raise ClientConnectionError from err

    async def readuntil(self, separator: str, timeout_time: float | None = None) -> str:
        """
        Read data until the separator is found or the optional timeout is reached.

        Args:
            separator: The separator to read until.
            timeout_time: The optional timeout in seconds.

        Returns:
            The data read, as a string.

        """
        # Make sure we're connected
        if self.reader is None or self.closed:
            msg = "Client not connected."
            raise ClientConnectionError(msg)

        # Read the response, with optional timeout
        try:
            data = await asyncio.wait_for(
                self.reader.readuntil(separator.encode()), timeout_time
            )
        except TimeoutError as err:
            raise ClientTimeoutError from err
        except (OSError, asyncio.IncompleteReadError) as err:
            raise ClientConnectionError from err

        return data.replace(b"\x00", b"").decode()

    async def readline(self, timeout_time: float | None = None) -> str:
        """
        Read one line or the optional timeout is reached.

        Args:
            timeout_time: The optional timeout in seconds.

        Returns:
            The data read, as a string.

        """
        # Make sure we're connected
        if self.reader is None or self.closed:
            msg = "Client not connected."
            raise ClientConnectionError(msg)

        # Read the response, with optional timeout
        try:
            data = await asyncio.wait_for(self.reader.readline(), timeout_time)
            cleaned_data = data.replace("\x00", "").strip()  # type: ignore  # noqa: PGH003
            _LOGGER.debug("%s - Received %s", self.identifier, cleaned_data)
            return cleaned_data
        except TimeoutError as err:
            raise ClientTimeoutError from err
        except (OSError, asyncio.IncompleteReadError) as err:
            raise ClientConnectionError from err

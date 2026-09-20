"""
A fake Biamp Tesira Text Protocol server for tests.

It reproduces the behaviours of a real Tesira telnet session that matter to the
client: option negotiation, the delayed welcome banner with terminal preamble,
echoing every command back character by character, CR LF or CR NUL line
endings, subscription updates arriving before the ``+OK`` of the subscribe
command, and ``-ERR ALREADY_SUBSCRIBED`` on repeated subscriptions.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import Any

IAC = 0xFF
DONT, DO, WONT, WILL = 0xFE, 0xFD, 0xFC, 0xFB
SB, SE = 0xFA, 0xF0

BANNER = "Welcome to the Tesira Text Protocol Server..."
PREAMBLE = (
    'No entry for terminal type "unknown";\r\nusing dumb terminal settings.\r\n\r\n\r\n'
)


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.6f}"
    if isinstance(value, str):
        return f'"{value}"'
    return str(value)


@dataclass
class Block:
    """State of one DSP block (index is ignored; only index 1 is modelled)."""

    mute: bool = False
    level: float = -10.0
    min_level: float = -100.0
    max_level: float = 12.0

    def get(self, attribute: str) -> Any:
        return {
            "mute": self.mute,
            "level": self.level,
            "minLevel": self.min_level,
            "maxLevel": self.max_level,
        }[attribute]

    def set(self, attribute: str, raw: str) -> Any:
        if attribute == "mute":
            self.mute = raw.lower() == "true"
            return self.mute
        if attribute == "level":
            self.level = float(raw)
            return self.level
        raise KeyError(attribute)


@dataclass
class Session:
    """One accepted telnet connection."""

    server: FakeTesiraServer
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    subscriptions: dict[str, tuple[str, str]] = field(default_factory=dict)
    _raw: bytearray = field(default_factory=bytearray)
    _text: bytearray = field(default_factory=bytearray)

    async def run(self) -> None:
        try:
            await self._negotiate_and_welcome()
            while True:
                data = await self.reader.read(1024)
                if not data:
                    return
                self._raw.extend(data)
                self._strip_iac()
                for line in self._pop_lines():
                    await self._handle_line(line)
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.CancelledError):
            return
        finally:
            self.close()

    def close(self) -> None:
        if not self.writer.is_closing():
            self.writer.close()

    async def _negotiate_and_welcome(self) -> None:
        options = bytes(
            [IAC, DO, 0x18, IAC, WILL, 0x01, IAC, WILL, 0x03, IAC, DO, 0x1F]
        )
        self.writer.write(options)
        await self.writer.drain()
        await asyncio.sleep(self.server.banner_delay)
        if self.server.preamble:
            self.writer.write(PREAMBLE.encode())
        await self.send_line("")
        await self.send_line(BANNER)

    def _strip_iac(self) -> None:
        """Move plain text out of ``_raw`` into ``_text``, dropping telnet commands."""
        raw = self._raw
        while raw:
            if raw[0] != IAC:
                self._text.append(raw.pop(0))
                continue
            if len(raw) < 2:
                return
            if raw[1] == SB:
                end = raw.find(bytes([IAC, SE]), 2)
                if end == -1:
                    return
                del raw[: end + 2]
            elif raw[1] in (WILL, WONT, DO, DONT):
                if len(raw) < 3:
                    return
                del raw[:3]
            else:
                del raw[:2]

    def _pop_lines(self) -> list[str]:
        lines: list[str] = []
        while True:
            text = bytes(self._text)
            cr = text.find(b"\r")
            lf = text.find(b"\n")
            candidates = [pos for pos in (cr, lf) if pos != -1]
            if not candidates:
                return lines
            pos = min(candidates)
            if text[pos : pos + 1] == b"\r":
                if len(text) == pos + 1:
                    return lines  # wait to see whether LF follows
                follows_cr = text[pos + 1 : pos + 2] in (b"\n", b"\x00")
                end = pos + 2 if follows_cr else pos + 1
            else:
                end = pos + 1
            lines.append(text[:pos].decode())
            del self._text[:end]

    async def send_line(self, text: str) -> None:
        self.writer.write(text.encode() + self.server.line_ending)
        await self.writer.drain()

    async def _echo(self, line: str) -> None:
        if not self.server.echo:
            return
        repeats = 2 if self.server.duplicate_echo else 1
        for _ in range(repeats):
            if self.server.char_by_char_echo:
                for char in line:
                    self.writer.write(char.encode())
                    await self.writer.drain()
                    await asyncio.sleep(0)
                self.writer.write(self.server.line_ending)
                await self.writer.drain()
            else:
                await self.send_line(line)

    async def publish(self, label: str, value: Any, *, folded_ok: bool = False) -> None:
        suffix = " +OK" if folded_ok else ""
        await self.send_line(
            f'! "publishToken":"{label}" "value":{_format_value(value)}{suffix}'
        )

    async def _handle_line(self, line: str) -> None:  # noqa: PLR0912
        server = self.server
        server.commands_received.append(line)
        await self._echo(line)
        if server.response_delay:
            await asyncio.sleep(server.response_delay)
        if server.silent:
            return

        parts = line.split(" ")
        if line == "DEVICE get serialNumber":
            if server.serial_error:
                await self.send_line("-ERR ATTRIBUTE_NOT_FOUND")
            else:
                await self.send_line(f'+OK "value":"{server.serial}"')
            return

        if len(parts) < 3:
            await self.send_line("-ERR Parse error: not enough parameters supplied")
            return

        tag, verb, attribute = parts[0], parts[1], parts[2]
        block = server.blocks.get(tag)
        if block is None:
            await self.send_line(
                '-ERR address not found: {"deviceId":0 "classCode":0 "instanceNum":0}'
            )
            return

        try:
            if verb == "get":
                await self.send_line(
                    f'+OK "value":{_format_value(block.get(attribute))}'
                )
            elif verb == "set":
                value = block.set(attribute, parts[4])
                await self.send_line("+OK")
                await server.notify(tag, attribute, value)
            elif verb == "subscribe":
                label = parts[4]
                if label in self.subscriptions:
                    await self.send_line("-ERR ALREADY_SUBSCRIBED")
                    return
                self.subscriptions[label] = (tag, attribute)
                if server.interleave_before_ok is not None:
                    other_label, other_value = server.interleave_before_ok
                    await self.publish(other_label, other_value)
                await self.publish(
                    label, block.get(attribute), folded_ok=server.folded_ok
                )
                if not server.folded_ok:
                    await self.send_line("+OK")
                await self.send_line("")
            elif verb == "unsubscribe":
                self.subscriptions.pop(parts[4], None)
                await self.send_line("+OK")
            else:
                await self.send_line(f"-ERR '{verb}' is not supported")
        except (KeyError, IndexError, ValueError):
            await self.send_line(f"-ERR '{attribute}' is not supported by {tag}")


class FakeTesiraServer:
    """Configurable fake Tesira listening on an ephemeral localhost port."""

    def __init__(self, blocks: dict[str, Block] | None = None) -> None:
        self.blocks: dict[str, Block] = blocks if blocks is not None else {}
        self.serial = "03787145"
        self.serial_error = False
        self.banner_delay = 0.02
        self.preamble = True
        self.line_ending = b"\r\n"
        self.echo = True
        self.char_by_char_echo = True
        self.duplicate_echo = False
        self.folded_ok = False
        self.silent = False
        self.response_delay = 0.0
        self.interleave_before_ok: tuple[str, Any] | None = None
        self.commands_received: list[str] = []
        self.sessions: list[Session] = []
        self._server: asyncio.AbstractServer | None = None
        self._tasks: set[asyncio.Task] = set()

    @property
    def port(self) -> int:
        assert self._server is not None
        return self._server.sockets[0].getsockname()[1]

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._on_connect, "127.0.0.1", 0)

    async def stop(self) -> None:
        self.drop_all_sessions()
        for task in list(self._tasks):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    def _on_connect(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        session = Session(self, reader, writer)
        self.sessions.append(session)
        task = asyncio.create_task(session.run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @property
    def open_sessions(self) -> list[Session]:
        return [s for s in self.sessions if not s.writer.is_closing()]

    def drop_all_sessions(self) -> None:
        for session in self.sessions:
            session.close()

    async def notify(self, tag: str, attribute: str, value: Any) -> None:
        """Send an update to every session subscribed to (tag, attribute)."""
        for session in self.open_sessions:
            for label, target in list(session.subscriptions.items()):
                if target == (tag, attribute):
                    await session.publish(label, value)

    async def push_update(self, tag: str, attribute: str, raw: str) -> None:
        """Change a block value as if done from the Tesira UI and notify subscribers."""
        value = self.blocks[tag].set(attribute, raw)
        await self.notify(tag, attribute, value)

    async def push_raw(self, label: str, value: Any) -> None:
        """Send a publishToken for an arbitrary label on every session."""
        for session in self.open_sessions:
            await session.publish(label, value)

    def subscribe_commands(self, label: str | None = None) -> list[str]:
        return [
            c
            for c in self.commands_received
            if " subscribe " in c and (label is None or c.endswith(f" {label}"))
        ]

"""Tests for the line-oriented telnet wrapper."""

from __future__ import annotations

import asyncio
import socket

import pytest

from errors import ClientConnectionError, ClientTimeoutError
from fake_tesira import FakeTesiraServer
from telnet import BiampTesiraTelnetConnection


async def test_connect_waits_for_banner_and_skips_preamble(
    server: FakeTesiraServer,
) -> None:
    telnet = await BiampTesiraTelnetConnection.connect(
        "127.0.0.1", server.port, "test", timeout_seconds=2.0
    )
    try:
        # Nothing from the preamble/banner must leak into the line stream.
        await telnet.write("DEVICE get serialNumber")
        lines = []
        while len(lines) < 2:
            line = await telnet.readline()
            if line:
                lines.append(line)
        assert lines == ["DEVICE get serialNumber", '+OK "value":"03787145"']
    finally:
        telnet.close()


@pytest.mark.parametrize("line_ending", [b"\r\n", b"\r\x00"])
async def test_readline_normalises_line_endings(
    server: FakeTesiraServer, line_ending: bytes
) -> None:
    server.line_ending = line_ending
    server.echo = False
    telnet = await BiampTesiraTelnetConnection.connect(
        "127.0.0.1", server.port, "test", timeout_seconds=2.0
    )
    try:
        await telnet.write("Mic1 get mute 1")
        line = await telnet.readline()
        while not line:
            line = await telnet.readline()
        assert line == '+OK "value":false'
        assert "\x00" not in line
        assert "\r" not in line
    finally:
        telnet.close()


async def test_connect_times_out_without_banner(server: FakeTesiraServer) -> None:
    server.banner_delay = 5.0
    with pytest.raises(ClientTimeoutError):
        await BiampTesiraTelnetConnection.connect(
            "127.0.0.1", server.port, "test", timeout_seconds=0.2
        )


async def test_connect_refused() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        free_port = sock.getsockname()[1]
    with pytest.raises(ClientConnectionError):
        await BiampTesiraTelnetConnection.connect(
            "127.0.0.1", free_port, "test", timeout_seconds=1.0
        )


async def test_readline_raises_when_peer_closes(server: FakeTesiraServer) -> None:
    telnet = await BiampTesiraTelnetConnection.connect(
        "127.0.0.1", server.port, "test", timeout_seconds=2.0
    )
    try:
        server.drop_all_sessions()
        with pytest.raises(ClientConnectionError):
            await asyncio.wait_for(_read_forever(telnet), 2)
    finally:
        telnet.close()


async def _read_forever(telnet: BiampTesiraTelnetConnection) -> None:
    while True:
        await telnet.readline()


async def test_operations_after_close_raise(server: FakeTesiraServer) -> None:
    telnet = await BiampTesiraTelnetConnection.connect(
        "127.0.0.1", server.port, "test", timeout_seconds=2.0
    )
    telnet.close()
    assert telnet.closed
    with pytest.raises(ClientConnectionError):
        await telnet.write("DEVICE get serialNumber")
    with pytest.raises(ClientConnectionError):
        await telnet.readline()

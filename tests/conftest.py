"""Shared fixtures."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest

from fake_tesira import Block, FakeTesiraServer
from models import Subscription, TesiraConfig
from tesira import BiampTesiraConnection


class FakeMqtt:
    """Records published states."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any], str | None]] = []
        self.fail = False

    async def publish_state(self, name: str, data: dict, serial: str | None) -> None:
        if self.fail:
            msg = "MQTT is down"
            raise RuntimeError(msg)
        self.published.append((name, copy.deepcopy(data), serial))

    def states_for(self, identifier: str) -> list[Any]:
        return [
            d["state"] for _, d, _ in self.published if d["identifier"] == identifier
        ]

    def last_state(self, identifier: str) -> Any:
        return self.states_for(identifier)[-1]


async def wait_until(
    predicate: Callable[[], bool], deadline_seconds: float = 2.0, interval: float = 0.01
) -> None:
    """Poll until ``predicate`` is true."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + deadline_seconds
    while not predicate():
        if loop.time() > deadline:
            msg = "Condition not met in time"
            raise AssertionError(msg)
        await asyncio.sleep(interval)


@pytest.fixture
async def server() -> AsyncIterator[FakeTesiraServer]:
    fake = FakeTesiraServer(
        blocks={
            "Mic1": Block(mute=False),
            "Lvl1": Block(mute=True, level=-12.5, min_level=-100.0, max_level=12.0),
        }
    )
    await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture
def mqtt() -> FakeMqtt:
    return FakeMqtt()


@pytest.fixture
def make_config(server: FakeTesiraServer) -> Callable[..., TesiraConfig]:
    def _make(**overrides: Any) -> TesiraConfig:
        values: dict[str, Any] = {
            "host": "127.0.0.1",
            "port": server.port,
            "resubscription_time": 300,
            "command_timeout": 2.0,
            "heartbeat_interval": 0,
        }
        values.update(overrides)
        return TesiraConfig(**values)

    return _make


@pytest.fixture
async def connection(
    make_config: Callable[..., TesiraConfig], mqtt: FakeMqtt
) -> AsyncIterator[BiampTesiraConnection]:
    conn = BiampTesiraConnection(make_config(), mqtt)
    yield conn
    await conn.close()


@pytest.fixture
async def make_connection(
    make_config: Callable[..., TesiraConfig], mqtt: FakeMqtt
) -> AsyncIterator[Callable[..., Awaitable[BiampTesiraConnection]]]:
    created: list[BiampTesiraConnection] = []

    async def _make(**overrides: Any) -> BiampTesiraConnection:
        conn = BiampTesiraConnection(make_config(**overrides), mqtt)
        created.append(conn)
        return conn

    yield _make
    for conn in created:
        await conn.close()


MUTE_SUB = Subscription(
    instance_tag="Mic1", attribute="mute", index=1, name="Mute", device_name="Mic 1"
)
LEVEL_SUB = Subscription(
    instance_tag="Lvl1", attribute="level", index=1, name="Level", device_name="Lvl 1"
)
LEVEL_MUTE_SUB = Subscription(
    instance_tag="Lvl1", attribute="mute", index=1, name="Mute", device_name="Lvl 1"
)
ALL_SUBS = {MUTE_SUB, LEVEL_SUB, LEVEL_MUTE_SUB}

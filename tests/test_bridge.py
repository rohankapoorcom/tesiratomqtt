"""End-to-end: both supervisors against the fake Tesira and the fake broker."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Callable

import pytest

from conftest import ALL_SUBS, running, wait_until
from errors import ClientError
from fake_mqtt import FakeBroker
from fake_tesira import FakeTesiraServer
from models import TesiraConfig
from mqtt_connection import MqttConnection
from tesira import BiampTesiraConnection

STATE = "t2m/Mic1_mute_1/state"
CONFIG = "homeassistant/switch/03787145_Mic1_mute_1/config"


@pytest.fixture
async def bridge(
    make_config: Callable[..., TesiraConfig],
    mqtt_conn: MqttConnection,
    server: FakeTesiraServer,
) -> AsyncIterator[BiampTesiraConnection]:
    tesira = BiampTesiraConnection(make_config(), mqtt_conn)

    async def on_command(key: str, value: str) -> None:
        with contextlib.suppress(ClientError):
            await tesira.update_state_and_command(key, value)

    async with (
        running(tesira.run(ALL_SUBS)),
        running(mqtt_conn.run(on_command)),
    ):
        await wait_until(
            lambda: (
                tesira.connected
                and mqtt_conn.connected
                and len(server.subscribe_commands()) == len(ALL_SUBS)
            )
        )
        yield tesira
    await tesira.close()


async def test_mqtt_outage_does_not_touch_the_tesira(
    bridge: BiampTesiraConnection,
    server: FakeTesiraServer,
    broker: FakeBroker,
    mqtt_conn: MqttConnection,
) -> None:
    await wait_until(lambda: broker.on(STATE) == [False])
    assert len(broker.on(CONFIG)) == 1
    sessions = server.open_sessions
    subscribe_count = len(server.subscribe_commands())
    broker.clear()

    broker.drop()
    await wait_until(lambda: not mqtt_conn.connected)
    await server.push_update("Mic1", "mute", "true")
    assert bridge.connected

    await wait_until(lambda: broker.connects == 2)
    await wait_until(lambda: broker.on(STATE) == [True])
    assert broker.on("t2m/availability") == [{"state": "online"}]
    assert len(broker.on(CONFIG)) == 1
    assert server.open_sessions == sessions
    assert len(server.subscribe_commands()) == subscribe_count


async def test_command_from_mqtt_reaches_the_tesira(
    bridge: BiampTesiraConnection, server: FakeTesiraServer, broker: FakeBroker
) -> None:
    await wait_until(lambda: broker.on(STATE) == [False])
    await broker.inject("t2m/Mic1_mute_1/set", "true")
    await wait_until(lambda: broker.on(STATE) == [False, True])
    assert server.blocks["Mic1"].mute is True
    assert bridge.connected


async def test_tesira_loss_republishes_state_but_keeps_mqtt_session(
    bridge: BiampTesiraConnection,
    server: FakeTesiraServer,
    broker: FakeBroker,
    mqtt_conn: MqttConnection,
) -> None:
    await wait_until(lambda: broker.on(STATE) == [False])
    server.drop_all_sessions()
    await wait_until(lambda: not bridge.connected)
    await wait_until(lambda: bridge.connected and broker.on(STATE) == [False, False])
    assert broker.connects == 1
    assert mqtt_conn.connected

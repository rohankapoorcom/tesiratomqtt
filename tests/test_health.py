"""HTTP probe endpoints."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import pytest
from aiohttp.test_utils import TestClient, TestServer

from conftest import ALL_SUBS, running, wait_until
from errors import ClientError
from fake_mqtt import FakeBroker
from fake_tesira import FakeTesiraServer
from health import HealthServer, create_app
from models import Config, HealthConfig, MqttConfig, Subscription, TesiraConfig
from mqtt_connection import MqttConnection
from tesira import BiampTesiraConnection


@dataclass
class FakeLink:
    connected: bool = True


@pytest.fixture
async def client() -> AsyncIterator[tuple[TestClient, FakeLink, FakeLink]]:
    mqtt = FakeLink()
    tesira = FakeLink()
    async with TestClient(TestServer(create_app(mqtt, tesira))) as test_client:
        yield test_client, mqtt, tesira


async def test_livez_is_ok_when_links_are_down(
    client: tuple[TestClient, FakeLink, FakeLink],
) -> None:
    http, mqtt, tesira = client
    mqtt.connected = False
    tesira.connected = False
    resp = await http.get("/livez")
    assert resp.status == 200
    body = await resp.json()
    assert body == {"status": "unavailable", "mqtt": False, "tesira": False}


async def test_readyz_and_health_require_both_links(
    client: tuple[TestClient, FakeLink, FakeLink],
) -> None:
    http, mqtt, tesira = client
    for path in ("/readyz", "/health"):
        resp = await http.get(path)
        assert resp.status == 200
        assert await resp.json() == {"status": "ok", "mqtt": True, "tesira": True}

        mqtt.connected = False
        resp = await http.get(path)
        assert resp.status == 503
        assert await resp.json() == {
            "status": "unavailable",
            "mqtt": False,
            "tesira": True,
        }

        mqtt.connected = True
        tesira.connected = False
        resp = await http.get(path)
        assert resp.status == 503
        assert await resp.json() == {
            "status": "unavailable",
            "mqtt": True,
            "tesira": False,
        }

        tesira.connected = True


async def test_unknown_path_is_404(
    client: tuple[TestClient, FakeLink, FakeLink],
) -> None:
    http, _, _ = client
    resp = await http.get("/nope")
    assert resp.status == 404


def test_health_config_is_optional() -> None:
    config = Config(
        mqtt=MqttConfig(
            base_topic="t2m",
            server="broker",
            port=1883,
            user="u",
            password="p",  # noqa: S106
            keepalive=60,
        ),
        tesira=TesiraConfig(host="tesira", port=23, resubscription_time=300),
        subscriptions={
            Subscription(
                instance_tag="Mic1",
                attribute="mute",
                index=1,
                name="Mute",
                device_name="Mic 1",
            )
        },
    )
    assert config.health.enabled
    assert config.health.host == "0.0.0.0"  # noqa: S104
    assert config.health.port == 8080


@pytest.fixture
async def bridge(
    make_config: Callable[..., TesiraConfig], mqtt_conn: MqttConnection
) -> AsyncIterator[BiampTesiraConnection]:
    tesira = BiampTesiraConnection(make_config(), mqtt_conn)

    async def on_command(key: str, value: str) -> None:
        with contextlib.suppress(ClientError):
            await tesira.update_state_and_command(key, value)

    async with (
        running(tesira.run(ALL_SUBS)),
        running(mqtt_conn.run(on_command)),
    ):
        yield tesira
    await tesira.close()


async def test_readyz_follows_real_outages(
    bridge: BiampTesiraConnection,
    broker: FakeBroker,
    mqtt_conn: MqttConnection,
    server: FakeTesiraServer,
) -> None:
    await wait_until(lambda: mqtt_conn.connected and bridge.connected)
    async with TestClient(TestServer(create_app(mqtt_conn, bridge))) as http:
        assert (await http.get("/readyz")).status == 200
        assert (await http.get("/livez")).status == 200

        broker.drop()
        await wait_until(lambda: not mqtt_conn.connected)
        readyz = await http.get("/readyz")
        assert readyz.status == 503
        assert (await readyz.json())["mqtt"] is False
        assert (await http.get("/livez")).status == 200

        await wait_until(lambda: mqtt_conn.connected)
        assert (await http.get("/readyz")).status == 200

        server.drop_all_sessions()
        await wait_until(lambda: not bridge.connected)
        readyz = await http.get("/readyz")
        assert readyz.status == 503
        assert (await readyz.json())["tesira"] is False
        assert (await http.get("/livez")).status == 200


async def test_health_server_listens_and_stops() -> None:
    config = HealthConfig(host="127.0.0.1", port=0)
    server = HealthServer(FakeLink(), FakeLink(), config)
    async with running(server.run()):
        await asyncio.sleep(0.05)
    await server.close()

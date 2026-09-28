"""Tests for MqttConnection against the fake broker."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest

import mqtt_connection
from conftest import MQTT_CONFIG, running, wait_until
from errors import ClientError
from fake_mqtt import FakeBroker, Published
from models import subscription_identifier
from mqtt_connection import MqttConnection

SERIAL = "03787145"


def entry(identifier: str = "Mic1_mute_1", state: Any = False) -> dict[str, Any]:
    tag, attribute, index = identifier.split("_")
    data: dict[str, Any] = {
        "instance_tag": tag,
        "attribute": attribute,
        "index": int(index),
        "state": state,
        "variable_type": "bool" if attribute == "mute" else "float",
        "device_id": f"{SERIAL}_{tag}",
        "unique_id": f"{SERIAL}_{identifier}",
        "name": attribute.title(),
        "device_name": tag,
        "identifier": identifier,
    }
    if attribute == "level":
        data.update(min_level=-100.0, max_level=12.0)
    return data


class Handler:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.raise_error = False

    async def __call__(self, key: str, value: str) -> None:
        self.calls.append((key, value))
        if self.raise_error:
            msg = "rejected"
            raise ClientError(msg)


@pytest.fixture
def handler() -> Handler:
    return Handler()


async def test_connect_publishes_online_with_stable_client_id(
    mqtt_conn: MqttConnection, broker: FakeBroker, handler: Handler
) -> None:
    async with running(mqtt_conn.run(handler)):
        await mqtt_conn.wait_connected()
        assert broker.on("t2m/availability") == [{"state": "online"}]
        client = broker.current
        assert client is not None
        assert client.kwargs["identifier"] == "tesira2mqtt-test"
        assert client.kwargs["will"].topic == "t2m/availability"


async def test_publish_state_announces_once_and_subscribes_to_set(
    mqtt_conn: MqttConnection, broker: FakeBroker, handler: Handler
) -> None:
    async with running(mqtt_conn.run(handler)):
        await mqtt_conn.wait_connected()
        await mqtt_conn.publish_state("Mute", entry(state=False), SERIAL)
        await mqtt_conn.publish_state("Mute", entry(state=True), SERIAL)

        config = f"homeassistant/switch/{SERIAL}_Mic1_mute_1/config"
        assert len(broker.on(config)) == 1
        assert broker.on(config)[0]["command_topic"] == "t2m/Mic1_mute_1/set"
        assert broker.on("t2m/Mic1_mute_1/state") == [False, True]
        assert {item.qos for item in broker.published} == {1}
        assert broker.on("t2m/Mic1_mute_1/attributes")[-1]["state"] is True
        assert broker.current is not None
        assert broker.current.subscriptions == ["t2m/Mic1_mute_1/set"]


async def test_encoded_tag_keeps_discovery_and_clears_old_state(
    mqtt_conn: MqttConnection, broker: FakeBroker, handler: Handler
) -> None:
    identifier = subscription_identifier("Room A", "mute", 1)
    data = entry(state=False)
    data.update(
        instance_tag="Room A",
        identifier=identifier,
        device_id=f"{SERIAL}_Room A",
        unique_id=f"{SERIAL}_Room A_mute_1",
    )
    async with running(mqtt_conn.run(handler)):
        await mqtt_conn.wait_connected()
        assert await mqtt_conn.publish_state("Mute", data, SERIAL) is True

        config_topic = f"homeassistant/switch/{SERIAL}_{identifier}/config"
        config = broker.on(config_topic)[0]
        assert " " not in config_topic
        assert config["unique_id"] == f"{SERIAL}_Room A_mute_1"
        assert config["dev"]["ids"] == f"tesira2mqtt_{SERIAL}_Room A"
        assert config["state_topic"] == f"t2m/{identifier}/state"
        assert broker.on(f"t2m/{identifier}/state") == [False]
        assert broker.on("t2m/Room A_mute_1/state") == [""]
        assert broker.on("t2m/Room A_mute_1/attributes") == [""]
        legacy_config = f"homeassistant/switch/{SERIAL}_Room A_mute_1/config"
        assert broker.on(legacy_config) == [""]
        topics = [item.topic for item in broker.published]
        assert topics.index(legacy_config) < topics.index(config_topic)

        data["state"] = True
        await mqtt_conn.publish_state("Mute", data, SERIAL)
        assert broker.on(f"t2m/{identifier}/state") == [False, True]
        assert broker.on("t2m/Room A_mute_1/state") == [""]
        assert broker.on(legacy_config) == [""]


@pytest.mark.parametrize(
    "tags",
    [
        # The second tag is exactly the hex of the first.
        ("Room A", "526f6f6d2041"),
        ("Room A", "__526f6f6d2041"),
        ("Room A", "mute_1__526f6f6d2041"),
        ("Room A", "Room-A"),
        (".", "__2e"),
    ],
)
async def test_upgrade_from_raw_identifiers_keeps_every_entity(
    mqtt_conn: MqttConnection,
    broker: FakeBroker,
    handler: Handler,
    tags: tuple[str, ...],
) -> None:
    def data_for(tag: str, state: bool) -> dict[str, Any]:
        data = entry(state=state)
        data.update(
            instance_tag=tag,
            identifier=subscription_identifier(tag, "mute", 1),
            device_id=f"{SERIAL}_{tag}",
            unique_id=f"{SERIAL}_{tag}_mute_1",
        )
        return data

    # Retained topics as the previous release left them: raw-tag identifiers,
    # discovery on the unique_id.
    for tag in tags:
        old = f"{tag}_mute_1"
        for topic, payload in (
            (f"t2m/{old}/state", False),
            (f"t2m/{old}/attributes", {"instance_tag": tag}),
            (
                f"homeassistant/switch/{SERIAL}_{old}/config",
                {"unique_id": f"{SERIAL}_{old}"},
            ),
        ):
            broker.published.append(Published(topic, payload, retain=True))

    async with running(mqtt_conn.run(handler)):
        await mqtt_conn.wait_connected()
        for tag in tags:
            assert await mqtt_conn.publish_state("Mute", data_for(tag, True), SERIAL)

    # Home Assistant treats a config on an existing discovery topic as an update
    # to that entity, so a topic must never switch to another unique_id.
    owner: dict[str, str] = {}
    for item in broker.published:
        if item.topic.startswith("homeassistant/") and item.payload:
            assert (
                owner.setdefault(item.topic, item.payload["unique_id"])
                == (item.payload["unique_id"])
            )
    # Tags Home Assistant could already discover keep their discovery topic.
    for tag in tags:
        if re.fullmatch(r"[A-Za-z0-9_-]+", tag):
            assert subscription_identifier(tag, "mute", 1) == f"{tag}_mute_1"

    retained = broker.retained()
    configs = {
        topic: payload
        for topic, payload in retained.items()
        if topic.startswith("homeassistant/")
    }
    states = {
        topic: payload
        for topic, payload in retained.items()
        if topic.endswith("/state")
    }
    # One config and one state per tag, each at that tag's own identifier.
    assert len(configs) == len(tags)
    assert len(states) == len(tags)
    for tag in tags:
        identifier = subscription_identifier(tag, "mute", 1)
        config = configs[f"homeassistant/switch/{SERIAL}_{identifier}/config"]
        assert config["unique_id"] == f"{SERIAL}_{tag}_mute_1"
        assert config["state_topic"] == f"t2m/{identifier}/state"
        assert states[f"t2m/{identifier}/state"] is True
        assert retained[f"t2m/{identifier}/attributes"]["instance_tag"] == tag


async def test_publish_in_flight_during_reconnect_is_announced_again(
    mqtt_conn: MqttConnection, broker: FakeBroker, handler: Handler
) -> None:
    config = f"homeassistant/switch/{SERIAL}_Mic1_mute_1/config"
    broker.hold_topic = config
    async with running(mqtt_conn.run(handler)):
        await mqtt_conn.wait_connected()
        first = broker.current
        publish = asyncio.create_task(
            mqtt_conn.publish_state("Mute", entry(state=True), SERIAL)
        )
        await broker.held.wait()
        broker.hold_topic = None
        broker.drop()
        await wait_until(lambda: broker.connects == 2)
        await asyncio.sleep(0.01)
        # The discovery publish on the old client now completes.
        broker.release.set()
        assert await publish is False

        await mqtt_conn.wait_connected()
        # The caller retries the undelivered state on the new client.
        assert await mqtt_conn.publish_state("Mute", entry(state=True), SERIAL)
        second = broker.current
        assert second is not None
        assert second is not first
        assert second.subscriptions == ["t2m/Mic1_mute_1/set"]
        assert len(broker.on(config)) == 2
        assert broker.on("t2m/Mic1_mute_1/state") == [True]


async def test_level_discovery_is_a_number(
    mqtt_conn: MqttConnection, broker: FakeBroker, handler: Handler
) -> None:
    async with running(mqtt_conn.run(handler)):
        await mqtt_conn.wait_connected()
        await mqtt_conn.publish_state("Level", entry("Lvl1_level_1", -3.5), SERIAL)
        config = broker.on(f"homeassistant/number/{SERIAL}_Lvl1_level_1/config")[0]
        assert config["min"] == -100.0
        assert config["max"] == 12.0
        assert config["unit_of_measurement"] == "dB"


async def test_publish_before_connect_is_not_stored_for_replay(
    mqtt_conn: MqttConnection, broker: FakeBroker, handler: Handler
) -> None:
    assert await mqtt_conn.publish_state("Mute", entry(state=True), SERIAL) is False
    assert broker.published == []

    async with running(mqtt_conn.run(handler)):
        await mqtt_conn.wait_connected()
        assert broker.on("t2m/Mic1_mute_1/state") == []

        assert await mqtt_conn.publish_state("Mute", entry(state=False), SERIAL)
        assert broker.on("t2m/Mic1_mute_1/state") == [False]
        assert len(broker.on(f"homeassistant/switch/{SERIAL}_Mic1_mute_1/config")) == 1
        assert broker.current is not None
        assert broker.current.subscriptions == ["t2m/Mic1_mute_1/set"]


async def test_reconnect_republishes_availability_discovery_and_state(
    mqtt_conn: MqttConnection, broker: FakeBroker, handler: Handler
) -> None:
    async with running(mqtt_conn.run(handler)) as task:
        await mqtt_conn.wait_connected()
        await mqtt_conn.publish_state("Mute", entry(state=False), SERIAL)
        broker.clear()

        broker.drop()
        await wait_until(lambda: not mqtt_conn.connected)
        # Updates during the outage are refused, not raised; the caller retries.
        published = await mqtt_conn.publish_state("Mute", entry(state=True), SERIAL)
        assert published is False
        assert broker.published == []

        await wait_until(lambda: broker.connects == 2)
        await mqtt_conn.wait_connected()
        assert not task.done()
        assert broker.on("t2m/availability") == [{"state": "online"}]
        assert len(broker.on(f"homeassistant/switch/{SERIAL}_Mic1_mute_1/config")) == 1
        # Only the last delivered state is replayed.
        assert broker.on("t2m/Mic1_mute_1/state") == [False]
        assert broker.current is not None
        assert broker.current.subscriptions == ["t2m/Mic1_mute_1/set"]


async def test_failed_attributes_publish_does_not_replay_an_older_state(
    mqtt_conn: MqttConnection, broker: FakeBroker, handler: Handler
) -> None:
    async with running(mqtt_conn.run(handler)):
        await mqtt_conn.wait_connected()
        assert await mqtt_conn.publish_state("Mute", entry(state=False), SERIAL)

        broker.fail_topic = "t2m/Mic1_mute_1/attributes"
        published = await mqtt_conn.publish_state("Mute", entry(state=True), SERIAL)
        assert published is False
        assert broker.on("t2m/Mic1_mute_1/state") == [False, True]
        broker.fail_topic = None

        broker.drop()
        await wait_until(lambda: broker.connects == 2)
        await mqtt_conn.wait_connected()
        assert broker.on("t2m/Mic1_mute_1/state") == [False, True, True]
        assert broker.retained()["t2m/Mic1_mute_1/attributes"]["state"] is True


async def test_publish_failure_on_live_client_is_not_fatal(
    mqtt_conn: MqttConnection, broker: FakeBroker, handler: Handler
) -> None:
    async with running(mqtt_conn.run(handler)):
        await mqtt_conn.wait_connected()
        client = broker.current
        assert client is not None
        client.connected = False  # publish now raises MqttError
        published = await mqtt_conn.publish_state("Mute", entry(state=True), SERIAL)
        assert published is False
        client.connected = True


async def test_broker_unreachable_at_start_keeps_retrying(
    mqtt_conn: MqttConnection, broker: FakeBroker, handler: Handler
) -> None:
    broker.refuse = True
    async with running(mqtt_conn.run(handler)) as task:
        await asyncio.sleep(0.3)
        assert not task.done()
        assert not mqtt_conn.connected

        broker.refuse = False
        await asyncio.wait_for(mqtt_conn.wait_connected(), 2)
        assert broker.on("t2m/availability") == [{"state": "online"}]


async def test_set_message_is_passed_to_handler(
    mqtt_conn: MqttConnection, broker: FakeBroker, handler: Handler
) -> None:
    async with running(mqtt_conn.run(handler)) as task:
        await mqtt_conn.wait_connected()
        await broker.inject("t2m/Mic1_mute_1/set", "true")
        await wait_until(lambda: handler.calls == [("Mic1_mute_1", "true")])

        handler.raise_error = True
        await broker.inject("t2m/Mic1_mute_1/set", "false")
        await wait_until(lambda: len(handler.calls) == 2)
        assert not task.done()

        await broker.inject("t2m/availability", "junk")
        await broker.inject("t2m/Mic1_mute_1/state", "junk")
        await asyncio.sleep(0.05)
        assert len(handler.calls) == 2


async def test_nested_base_topic_commands_are_parsed(
    broker: FakeBroker, handler: Handler
) -> None:
    conn = MqttConnection(MQTT_CONFIG.model_copy(update={"base_topic": "bldg/t2m"}))
    async with running(conn.run(handler)):
        await conn.wait_connected()
        await conn.publish_state("Mute", entry(), SERIAL)
        assert broker.current is not None
        assert broker.current.subscriptions == ["bldg/t2m/Mic1_mute_1/set"]

        await broker.inject("bldg/t2m/Mic1_mute_1/set", "true")
        await wait_until(lambda: handler.calls == [("Mic1_mute_1", "true")])

        await broker.inject("bldg/t2m/set", "x")
        await broker.inject("bldg/t2m/a/b/set", "x")
        await asyncio.sleep(0.05)
        assert len(handler.calls) == 1
    await conn.close()


async def test_undecodable_payload_is_ignored(
    mqtt_conn: MqttConnection, broker: FakeBroker, handler: Handler
) -> None:
    async with running(mqtt_conn.run(handler)) as task:
        await mqtt_conn.wait_connected()
        assert broker.current is not None
        await broker.current.deliver_raw("t2m/Mic1_mute_1/set", b"\xff\xfe")
        await broker.inject("t2m/Mic1_mute_1/set", "true")
        await wait_until(lambda: handler.calls == [("Mic1_mute_1", "true")])
        assert not task.done()


async def test_close_during_backoff_returns_promptly(
    broker: FakeBroker, handler: Handler, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mqtt_connection, "_RECONNECT_BACKOFF_INITIAL", 30.0)
    broker.refuse = True
    conn = MqttConnection(MQTT_CONFIG)
    task = asyncio.create_task(conn.run(handler))
    await asyncio.sleep(0.05)
    await conn.close()
    await asyncio.wait_for(task, 1)


async def test_close_publishes_offline_and_stops(
    mqtt_conn: MqttConnection, broker: FakeBroker, handler: Handler
) -> None:
    task = asyncio.create_task(mqtt_conn.run(handler))
    await mqtt_conn.wait_connected()
    client = broker.current

    await mqtt_conn.close()
    await asyncio.wait_for(task, 1)
    assert broker.on("t2m/availability") == [{"state": "online"}, {"state": "offline"}]
    assert client is not None
    assert not client.connected
    assert not mqtt_conn.connected

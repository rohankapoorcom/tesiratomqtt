"""Tests for the Tesira Text Protocol connection against the fake Tesira."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import pytest

from conftest import ALL_SUBS, LEVEL_SUB, MUTE_SUB, FakeMqtt, wait_until
from errors import (
    ClientConnectionError,
    ClientError,
    ClientResponseError,
    ClientTimeoutError,
)
from fake_tesira import Block, FakeTesiraServer
from models import Subscription
from tesira import BiampTesiraConnection

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

pytestmark = pytest.mark.usefixtures("server")

# ------------------------------------------------------------------ connecting


async def test_open_reads_serial_quickly(
    connection: BiampTesiraConnection, server: FakeTesiraServer
) -> None:
    started = time.monotonic()
    await connection.open()
    elapsed = time.monotonic() - started

    assert connection.serial_number == "03787145"
    assert connection.connected
    assert len(server.open_sessions) == 2
    # Both sessions are opened concurrently and nothing sleeps: well under the
    # ~7 seconds the previous implementation needed.
    assert elapsed < 1.0


async def test_duplicate_echo_does_not_corrupt_serial(
    connection: BiampTesiraConnection, server: FakeTesiraServer
) -> None:
    server.duplicate_echo = True
    await connection.open()
    assert connection.serial_number == "03787145"


async def test_echo_disabled_still_works(
    connection: BiampTesiraConnection, server: FakeTesiraServer
) -> None:
    server.echo = False
    await connection.open()
    assert connection.serial_number == "03787145"


async def test_cr_nul_line_endings(
    connection: BiampTesiraConnection, server: FakeTesiraServer, mqtt: FakeMqtt
) -> None:
    server.line_ending = b"\r\x00"
    await connection.open()
    await connection.subscribe(MUTE_SUB)
    assert connection.serial_number == "03787145"
    assert mqtt.last_state("Mic1_mute_1") is False


async def test_serial_that_is_not_topic_safe_is_rejected(
    connection: BiampTesiraConnection, server: FakeTesiraServer
) -> None:
    server.serial = "DEVICE get serialNumber"
    with pytest.raises(ClientResponseError, match="Unexpected serial number"):
        await connection.open()
    assert connection.serial_number is None
    assert not connection.connected
    await wait_until(lambda: not server.open_sessions)


async def test_error_response_for_serial_is_rejected(
    connection: BiampTesiraConnection, server: FakeTesiraServer
) -> None:
    server.serial_error = True
    with pytest.raises(ClientResponseError, match="-ERR"):
        await connection.open()
    assert not connection.connected


async def test_open_fails_cleanly_when_banner_never_arrives(
    make_connection: Callable[..., Awaitable[BiampTesiraConnection]],
    server: FakeTesiraServer,
) -> None:
    server.banner_delay = 5.0
    connection = await make_connection(command_timeout=0.2)
    with pytest.raises(ClientTimeoutError):
        await connection.open()
    assert not connection.connected


# ---------------------------------------------------------------- subscribing


async def test_subscribe_all_publishes_state_and_discovery_data(
    connection: BiampTesiraConnection, server: FakeTesiraServer, mqtt: FakeMqtt
) -> None:
    await connection.open()
    started = time.monotonic()
    await connection.subscribe_all(ALL_SUBS)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0  # previously ~2 s per mute and ~4 s per level

    by_id = {d["identifier"]: d for _, d, _ in mqtt.published}
    assert set(by_id) == {"Mic1_mute_1", "Lvl1_level_1", "Lvl1_mute_1"}

    mute = by_id["Mic1_mute_1"]
    assert mute["state"] is False
    assert mute["variable_type"] == "bool"
    assert mute["unique_id"] == "03787145_Mic1_mute_1"
    assert mute["device_id"] == "03787145_Mic1"
    assert mute["name"] == "Mute"
    assert mute["device_name"] == "Mic 1"

    level = by_id["Lvl1_level_1"]
    assert level["state"] == -12.5
    assert level["variable_type"] == "float"
    assert level["min_level"] == -100.0
    assert level["max_level"] == 12.0

    assert all(serial == "03787145" for _, _, serial in mqtt.published)
    assert sorted(server.subscribe_commands()) == [
        "Lvl1 subscribe level 1 Lvl1_level_1",
        "Lvl1 subscribe mute 1 Lvl1_mute_1",
        "Mic1 subscribe mute 1 Mic1_mute_1",
    ]


async def test_subscribe_with_ok_folded_onto_publish_line(
    connection: BiampTesiraConnection, server: FakeTesiraServer, mqtt: FakeMqtt
) -> None:
    server.folded_ok = True
    await connection.open()
    await connection.subscribe(LEVEL_SUB)
    assert mqtt.last_state("Lvl1_level_1") == -12.5


async def test_publish_token_updates_state(
    connection: BiampTesiraConnection, server: FakeTesiraServer, mqtt: FakeMqtt
) -> None:
    await connection.open()
    await connection.subscribe(MUTE_SUB)

    await server.push_update("Mic1", "mute", "true")
    await wait_until(lambda: mqtt.last_state("Mic1_mute_1") is True)
    assert connection._subscriptions["Mic1_mute_1"]["state"] is True


async def test_publish_token_interleaved_with_pending_command(
    connection: BiampTesiraConnection, server: FakeTesiraServer, mqtt: FakeMqtt
) -> None:
    await connection.open()
    await connection.subscribe(MUTE_SUB)

    # While the subscribe for Lvl1 is in flight the Tesira emits an update for
    # Mic1 before answering +OK. Both must be handled correctly.
    server.interleave_before_ok = ("Mic1_mute_1", True)
    await connection.subscribe(LEVEL_SUB)

    assert mqtt.last_state("Mic1_mute_1") is True
    assert mqtt.last_state("Lvl1_level_1") == -12.5


async def test_resubscribing_is_idempotent(
    connection: BiampTesiraConnection, server: FakeTesiraServer, mqtt: FakeMqtt
) -> None:
    await connection.open()
    await connection.subscribe_all(ALL_SUBS)
    published_before = len(mqtt.published)

    await connection.subscribe_all(ALL_SUBS)

    assert len(server.subscribe_commands("Mic1_mute_1")) == 2
    assert len(mqtt.published) == published_before
    assert connection._subscriptions["Lvl1_level_1"]["state"] == -12.5


async def test_rejected_subscription_is_skipped_not_fatal(
    connection: BiampTesiraConnection, mqtt: FakeMqtt
) -> None:
    bad = Subscription(
        instance_tag="DoesNotExist",
        attribute="mute",
        index=1,
        name="Mute",
        device_name="Nope",
    )
    await connection.open()
    await connection.subscribe_all({MUTE_SUB, bad})

    assert "DoesNotExist_mute_1" not in connection._subscriptions
    assert mqtt.last_state("Mic1_mute_1") is False
    assert connection.connected


async def test_unknown_publish_token_is_ignored(
    connection: BiampTesiraConnection, server: FakeTesiraServer, mqtt: FakeMqtt
) -> None:
    await connection.open()
    await connection.subscribe(MUTE_SUB)
    count = len(mqtt.published)

    await server.push_raw("SomethingElse_mute_1", True)
    # The reader must stay alive and keep serving commands.
    assert await connection.command("Mic1 get mute 1") == "false"
    assert len(mqtt.published) == count


async def test_mqtt_failure_does_not_kill_reader(
    connection: BiampTesiraConnection, server: FakeTesiraServer, mqtt: FakeMqtt
) -> None:
    await connection.open()
    await connection.subscribe(MUTE_SUB)

    mqtt.fail = True
    await server.push_update("Mic1", "mute", "true")
    await wait_until(lambda: connection._subscriptions["Mic1_mute_1"]["state"] is True)
    assert connection.connected
    assert await connection.command("DEVICE get serialNumber") == "03787145"


# ------------------------------------------------------------------- commands


async def test_command_parsing(connection: BiampTesiraConnection) -> None:
    await connection.open()
    assert await connection.command("Lvl1 get level 1") == "-12.500000"
    assert await connection.command("Lvl1 get mute 1") == "true"
    assert await connection.command("Lvl1 set level 1 -3") is None
    with pytest.raises(ClientResponseError, match="address not found"):
        await connection.command("Nope get level 1")


async def test_concurrent_commands_get_matching_responses(
    connection: BiampTesiraConnection, server: FakeTesiraServer
) -> None:
    for i in range(5):
        server.blocks[f"Blk{i}"] = Block(level=float(i))
    server.response_delay = 0.01
    await connection.open()

    results = await asyncio.gather(
        *(connection.command(f"Blk{i} get level 1") for i in range(5))
    )
    assert results == [f"{float(i):.6f}" for i in range(5)]


async def test_update_state_and_command(
    connection: BiampTesiraConnection, server: FakeTesiraServer, mqtt: FakeMqtt
) -> None:
    await connection.open()
    await connection.subscribe(MUTE_SUB)

    await connection.update_state_and_command("Mic1_mute_1", "true")

    assert server.blocks["Mic1"].mute is True
    assert "Mic1 set mute 1 true" in server.commands_received
    await wait_until(lambda: mqtt.last_state("Mic1_mute_1") is True)

    with pytest.raises(ClientError, match="does not match"):
        await connection.update_state_and_command("Unknown_mute_1", "true")


async def test_command_timeout_marks_connection_lost(
    make_connection: Callable[..., Awaitable[BiampTesiraConnection]],
    server: FakeTesiraServer,
) -> None:
    connection = await make_connection(command_timeout=0.3)
    await connection.open()
    server.silent = True

    started = time.monotonic()
    with pytest.raises(ClientTimeoutError):
        await connection.command("Mic1 get mute 1")
    assert time.monotonic() - started < 1.0
    assert not connection.connected


# ------------------------------------------------------- connection lifecycle


async def test_peer_disconnect_is_detected(
    connection: BiampTesiraConnection, server: FakeTesiraServer
) -> None:
    await connection.open()
    server.drop_all_sessions()

    async with asyncio.timeout(2):
        await connection.wait_closed()
    assert not connection.connected
    with pytest.raises(ClientConnectionError):
        await connection.command("DEVICE get serialNumber")


async def test_heartbeat_detects_unresponsive_session(
    make_connection: Callable[..., Awaitable[BiampTesiraConnection]],
    server: FakeTesiraServer,
) -> None:
    connection = await make_connection(command_timeout=0.2, heartbeat_interval=0.1)
    await connection.open()
    server.silent = True

    async with asyncio.timeout(2):
        await connection.wait_closed()
    assert not connection.connected


async def test_close_is_clean(
    connection: BiampTesiraConnection, server: FakeTesiraServer
) -> None:
    await connection.open()
    await connection.subscribe(MUTE_SUB)
    await connection.close()

    assert not connection.connected
    await wait_until(lambda: not server.open_sessions)
    assert not [t for t in asyncio.all_tasks() if t.get_name().startswith("tesira-")]


async def test_reopen_after_close(
    connection: BiampTesiraConnection, mqtt: FakeMqtt
) -> None:
    await connection.open()
    await connection.subscribe(MUTE_SUB)
    await connection.close()

    await connection.open()
    await connection.subscribe(MUTE_SUB)
    assert connection.connected
    assert mqtt.states_for("Mic1_mute_1") == [False, False]


# ----------------------------------------------------------------- supervisor


async def test_run_reconnects_and_resubscribes_after_loss(
    connection: BiampTesiraConnection, server: FakeTesiraServer, mqtt: FakeMqtt
) -> None:
    await connection.open()
    await connection.subscribe_all(ALL_SUBS)
    barrier = asyncio.Barrier(1)
    task = asyncio.create_task(connection.run(barrier, ALL_SUBS))
    try:
        await asyncio.sleep(0.05)
        server.drop_all_sessions()

        await wait_until(
            lambda: (
                len(server.subscribe_commands("Mic1_mute_1")) >= 2
                and connection.connected
            )
        )
        assert len(server.open_sessions) == 2

        # Updates flow again over the new session.
        await server.push_update("Mic1", "mute", "true")
        await wait_until(lambda: mqtt.last_state("Mic1_mute_1") is True)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_run_retries_with_backoff_while_tesira_is_down(
    make_connection: Callable[..., Awaitable[BiampTesiraConnection]],
    server: FakeTesiraServer,
) -> None:
    connection = await make_connection(command_timeout=0.2)
    await connection.open()
    barrier = asyncio.Barrier(1)
    task = asyncio.create_task(connection.run(barrier, {MUTE_SUB}))
    try:
        await asyncio.sleep(0.05)
        server.silent = True  # new sessions never get a banner
        server.banner_delay = 10
        server.drop_all_sessions()

        # The supervisor keeps trying instead of raising out.
        await asyncio.sleep(0.6)
        assert not task.done()
        assert not connection.connected

        server.silent = False
        server.banner_delay = 0.02
        await wait_until(lambda: connection.connected, deadline_seconds=4)
        assert server.subscribe_commands("Mic1_mute_1")
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_run_resubscribes_on_schedule(
    make_connection: Callable[..., Awaitable[BiampTesiraConnection]],
    server: FakeTesiraServer,
) -> None:
    connection = await make_connection(resubscription_time=0.1)
    await connection.open()
    await connection.subscribe(MUTE_SUB)
    barrier = asyncio.Barrier(1)
    task = asyncio.create_task(connection.run(barrier, {MUTE_SUB}))
    try:
        await wait_until(lambda: len(server.subscribe_commands("Mic1_mute_1")) >= 3)
        assert connection.connected
        assert len(server.open_sessions) == 2  # no reconnect happened
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

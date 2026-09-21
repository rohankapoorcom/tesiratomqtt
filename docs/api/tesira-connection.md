# Tesira Connection API Documentation

## Overview

`BiampTesiraConnection` (`src/tesira.py`) talks to a Biamp Tesira DSP over telnet using the [Tesira Text Protocol](https://support.biamp.com/Tesira/Control/Tesira_Text_Protocol) (TTP). It owns the telnet sessions, manages subscriptions, executes commands, tracks the state of every subscribed control and hands changes to the MQTT layer.

See also: [Telnet session negotiation in Tesira](https://support.biamp.com/Tesira/Control/Telnet_session_negotiation_in_Tesira).

## How it works

Two telnet sessions are opened concurrently:

- **subscription session** – `subscribe` commands and the resulting `! "publishToken"` updates.
- **command session** – `get`/`set` commands and the heartbeat.

Subscriptions are session-scoped, so they are re-created after a reconnect and refreshed every `resubscription_time` seconds as a safety net.

The Tesira takes about three seconds per session before sending its welcome banner, and sets sessions up one at a time, so `open()` takes roughly six seconds.

Each session has a reader task that classifies every line:

| Line | Handling |
|------|----------|
| `! "publishToken":"<label>" "value":<value>` | Store and publish the value. A trailing ` +OK` also resolves the pending command. |
| `+OK` / `+OK "value":<value>` / `-ERR ...` | Response to the command in flight on that session. |
| anything else | Ignored (command echo, blank lines, terminal preamble). |

Commands are serialised per session with a lock and each awaits its own `+OK`/`-ERR`. A command that gets no response within `command_timeout` marks the connection lost, since a late reply would otherwise be matched to the next command.

Both `CR LF` and `CR NUL` line endings are handled by `src/telnet.py`.

## Class: BiampTesiraConnection

```python
def __init__(self, tesira: TesiraConfig, mqtt: MqttConnection) -> None
```

Constructing the object does not open a connection.

### Properties

- `serial_number: str | None` – from `DEVICE get serialNumber`; `None` until `open()` succeeds. Must match `^[A-Za-z0-9_-]+$` (it is used in MQTT topics and `unique_id`s), otherwise `open()` raises `ClientResponseError`.
- `connected: bool` – both sessions open and no loss detected.

### Lifecycle

#### open()

Opens both sessions, starts the readers, reads and validates the serial number and starts the heartbeat (if `heartbeat_interval > 0`). Existing sessions are closed first, so it can be used to reconnect.

Raises `ClientConnectionError`, `ClientTimeoutError` or `ClientResponseError`.

#### close()

Stops background tasks, closes both sessions and fails in-flight commands with `ClientConnectionError`. Also stops `run()`.

#### wait_closed()

Returns once the connection has been lost or closed.

#### run(subscriptions)

Supervises the connection until cancelled: opens and subscribes (with exponential backoff, 1 s to 60 s, while the device is unavailable), reconnects on loss, and calls `subscribe_all()` every `resubscription_time` seconds. The entry point runs this as a long-lived task.

### Subscriptions

#### subscribe(subscription)

Sends `<instance_tag> subscribe <attribute> <index> <label>` with label `<instance_tag>_<attribute>_<index>` (also the MQTT identifier). For `level`, `minLevel`/`maxLevel` are fetched first. The initial `publishToken` reply is stored and published; `-ERR ALREADY_SUBSCRIBED` is treated as success and the value is fetched with `get` instead.

Raises `ClientResponseError` if the device rejects the subscription, or a connection error.

#### subscribe_all(subscriptions)

Calls `subscribe()` for each entry. Rejected subscriptions are logged and skipped; connection errors propagate.

### Commands

#### command(command) -> str | None

Sends a TTP command on the command session. Returns the value from `+OK "value":<value>` (unquoted) or `None` for a bare `+OK`.

```python
await tesira_conn.command("DEVICE get serialNumber")               # '03787145'
await tesira_conn.command("OfficeSpeakersPCLevel get level 1")     # '-4.000000'
await tesira_conn.command("OfficeSpeakersPCLevel set mute 1 true") # None
```

Raises `ClientResponseError` on `-ERR` or an unparseable reply, `ClientTimeoutError` on no response, `ClientConnectionError` when not connected.

#### update_state_and_command(key, value)

Sends `<instance_tag> set <attribute> <index> <value>` for the subscription identified by `key`. State is updated by the resulting `publishToken`, so it always reflects what the device did. Raises `ClientError` for an unknown key.

#### get_min_max_levels(subscription) -> dict[str, float]

Returns `{"min_level": ..., "max_level": ...}` for a level block.

## State store

Each subscription entry, as passed to `MqttConnection.publish_state()`:

```python
{
    "instance_tag": "OfficeSpeakersPCLevel",
    "attribute": "level",
    "index": 1,
    "state": -4.0,                  # bool for mute, float for level
    "variable_type": "float",
    "device_id": "03787145_OfficeSpeakersPCLevel",
    "unique_id": "03787145_OfficeSpeakersPCLevel_level_1",
    "name": "Level",
    "device_name": "Office Speakers PC",
    "identifier": "OfficeSpeakersPCLevel_level_1",
    "min_level": -12.0,             # level only
    "max_level": 12.0,              # level only
}
```

## Errors

| Exception | Meaning |
|-----------|---------|
| `ClientError` | Base class; also unknown MQTT identifier. |
| `ClientConnectionError` | Not connected, refused, or closed by the device. |
| `ClientTimeoutError` | Connection, banner or command timed out. |
| `ClientResponseError` | `-ERR`, or a reply that could not be parsed. |

MQTT publish failures inside the reader loop are logged and do not affect the connection.

## Example

```python
from models import Subscription, TesiraConfig
from tesira import BiampTesiraConnection


async def main(mqtt_conn):
    tesira_conn = BiampTesiraConnection(
        TesiraConfig(host="tesira.device.com", port=23, resubscription_time=300),
        mqtt_conn,
    )
    subscriptions = {
        Subscription(
            instance_tag="OfficeSpeakersPCLevel",
            attribute="level",
            index=1,
            name="Level",
            device_name="Office Speakers PC",
        )
    }

    await tesira_conn.open()
    await tesira_conn.subscribe_all(subscriptions)
    await tesira_conn.update_state_and_command("OfficeSpeakersPCLevel_level_1", "-6")

    try:
        await tesira_conn.run(subscriptions)
    finally:
        await tesira_conn.close()
```

## Testing

`tests/fake_tesira.py` is a fake Tesira server reproducing the real device's quirks (delayed banner, echo, `CR NUL` endings, `publishToken` before `+OK`, `ALREADY_SUBSCRIBED`, dropped sessions). `tests/test_tesira.py` runs this class against it; use `scripts/test`.

---

**Last Updated**: September 2026
**API Version**: 1.1.29

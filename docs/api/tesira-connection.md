# Tesira Connection API Documentation

## Overview

The `BiampTesiraConnection` class (`src/tesira.py`) manages communication with a Biamp Tesira DSP over telnet using the **Tesira Text Protocol (TTP)**. It owns the telnet sessions, creates and refreshes subscriptions, executes commands, keeps the current state of every subscribed control, and hands state changes to the MQTT layer.

For the protocol itself, refer to the official Biamp documentation:

- [Tesira Text Protocol](https://support.biamp.com/Tesira/Control/Tesira_Text_Protocol)
- [Telnet session negotiation in Tesira](https://support.biamp.com/Tesira/Control/Telnet_session_negotiation_in_Tesira)

## How the connection works

### Two sessions

Two telnet sessions are opened to the device, concurrently:

- **subscription session** – carries every `subscribe` command and therefore receives the asynchronous `! "publishToken"` updates the Tesira sends whenever a subscribed value changes.
- **command session** – carries `get` and `set` commands (including the ones triggered from MQTT) and the periodic heartbeat.

TTP subscriptions are session-scoped: if a session drops, every subscription made on it is gone and has to be re-created. This is why the connection re-subscribes after a reconnect and, as a safety net, on a fixed schedule (`resubscription_time`).

The Tesira accepts new sessions one at a time and takes roughly three seconds per session before it sends the `Welcome to the Tesira Text Protocol Server` banner, so opening the connection takes about six seconds regardless of how fast the network is.

### Reader loop and line classification

Each session has a background reader task that reads one line at a time and classifies it:

| Line looks like | Handled as |
|-----------------|------------|
| `! "publishToken":"<label>" "value":<value>` | Subscription update: the value is stored and published to MQTT. A trailing ` +OK` (the Tesira sometimes folds the subscribe acknowledgement onto this line) also resolves the pending command. |
| `+OK` or `+OK "value":<value>` | Success response for the command currently in flight on that session. |
| `-ERR ...` | Error response for the command currently in flight on that session. |
| anything else | Ignored. The Tesira echoes every command back through a terminal layer, sometimes wrapped or repeated; blank lines and terminal preamble also fall in here. |

Because every command awaits its own `+OK`/`-ERR` line, nothing depends on timing, sleeps or on the echo arriving in a particular shape. Commands on a session are serialised with a lock so responses can never be attributed to the wrong request. If a command receives no response within `command_timeout` seconds the session is considered dead (a late reply would otherwise be mistaken for the next command's response) and the connection is torn down for the supervisor to rebuild.

### Line endings

The Tesira terminates lines with either `CR LF` or `CR NUL`; both are handled transparently by `src/telnet.py`.

## Class: BiampTesiraConnection

```python
class BiampTesiraConnection:
    """BiampTesiraConnection used for communication with Biamp Tesira DSPs."""
```

### Constructor

```python
def __init__(self, tesira: TesiraConfig, mqtt: MqttConnection) -> None
```

**Parameters:**
- `tesira` (`TesiraConfig`): host, port, `resubscription_time`, `command_timeout`, `heartbeat_interval`.
- `mqtt` (`MqttConnection`): used to publish state (and, on first sight of a control, its Home Assistant discovery message) via `publish_state()`.

Constructing the object does not open any network connection.

### Properties

#### serial_number

```python
@property
def serial_number(self) -> str | None
```

Serial number reported by the device via `DEVICE get serialNumber`, or `None` until `open()` has succeeded. The serial is validated against `^[A-Za-z0-9_-]+$` because it becomes part of every MQTT `unique_id` and Home Assistant discovery topic; `open()` raises `ClientResponseError` if the device returns anything else.

#### connected

```python
@property
def connected(self) -> bool
```

`True` while both sessions are open and no connection loss has been detected.

### Lifecycle methods

#### open()

```python
async def open(self) -> None
```

Opens both sessions concurrently, waits for the welcome banner on each, starts the reader tasks, reads and validates the serial number and starts the heartbeat (if `heartbeat_interval > 0`). Any previously open sessions are closed first, so `open()` can be used to reconnect.

**Raises:**
- `ClientConnectionError` – connection refused / reset, or the device closed the session.
- `ClientTimeoutError` – connection, banner or serial number took longer than `command_timeout`.
- `ClientResponseError` – the device answered with `-ERR` or an invalid serial number.

#### close()

```python
async def close(self) -> None
```

Stops the heartbeat and reader tasks, closes both sessions and fails any command still in flight with `ClientConnectionError`. Also stops `run()` if it is active.

#### wait_closed()

```python
async def wait_closed(self) -> None
```

Returns once the connection has been lost (peer disconnect, read error, heartbeat or command timeout) or closed. Useful for supervisors that want to react to connection loss.

#### run()

```python
async def run(self, barrier: asyncio.Barrier, subscriptions: set[Subscription]) -> None
```

Supervises the connection until cancelled. After `barrier.wait()`:

- If not connected, calls `open()` and `subscribe_all()`, retrying with exponential backoff (1 s doubling up to 60 s) while the device is unavailable.
- While connected, waits for either a connection loss (then tears down and reconnects) or `resubscription_time` seconds elapsing (then calls `subscribe_all()` again to refresh the subscriptions).

The application entry point runs this as a long-lived task next to the MQTT message loop.

### Subscription methods

#### subscribe()

```python
async def subscribe(self, subscription: Subscription) -> None
```

Sends `<instance_tag> subscribe <attribute> <index> <label>` on the subscription session, where the label is `<instance_tag>_<attribute>_<index>` and doubles as the MQTT identifier. For `level` subscriptions the block's `minLevel`/`maxLevel` are fetched first (needed for the Home Assistant `number` entity).

The Tesira replies with the current value as a `publishToken` line followed by `+OK`; the value is stored and published. `-ERR ALREADY_SUBSCRIBED` is treated as success (the subscription is still active from an earlier call). If no initial value arrives, the current value is fetched with a `get`.

**Raises:**
- `ClientResponseError` – the device rejected the subscription (typically a wrong instance tag or index).
- `ClientConnectionError` / `ClientTimeoutError` – session problems.

#### subscribe_all()

```python
async def subscribe_all(self, subscriptions: set[Subscription]) -> None
```

Calls `subscribe()` for every entry. Rejected subscriptions are logged and skipped so one mistyped instance tag does not prevent the others from working; connection errors propagate.

### Command methods

#### command()

```python
async def command(self, command: str) -> str | None
```

Sends an arbitrary TTP command on the command session and returns the value from `+OK "value":<value>` (surrounding quotes removed), or `None` for a bare `+OK`.

**Example:**
```python
serial = await tesira_conn.command("DEVICE get serialNumber")   # '03787145'
level = await tesira_conn.command("OfficeSpeakersPCLevel get level 1")  # '-4.000000'
await tesira_conn.command("OfficeSpeakersPCLevel set mute 1 true")  # None
```

**Raises:**
- `ClientResponseError` – the device answered `-ERR ...` or with an unparseable line.
- `ClientTimeoutError` – no response within `command_timeout` (the connection is marked lost).
- `ClientConnectionError` – not connected.

#### update_state_and_command()

```python
async def update_state_and_command(self, key: str, value: str) -> None
```

Applies a value received on an MQTT `set` topic: `key` is the identifier (`<instance_tag>_<attribute>_<index>`) and the call sends `<instance_tag> set <attribute> <index> <value>`. The resulting `publishToken` update from the Tesira is what updates the stored state and MQTT, so the state always reflects what the device actually did.

**Raises:**
- `ClientError` – `key` does not match any subscription.
- Any error raised by `command()`.

#### get_min_max_levels()

```python
async def get_min_max_levels(self, subscription: Subscription) -> dict[str, float]
```

Returns `{"min_level": float, "max_level": float}` for a level block using `get minLevel` / `get maxLevel`.

#### process_tesira_response()

```python
async def process_tesira_response(self, response: str) -> None
```

Parses a `! "publishToken"` line, updates the stored state and publishes it. Normally invoked by the reader loop; exposed for tests and tooling.

## State store

Every subscription is kept in an internal dictionary keyed by identifier. The entry is what `MqttConnection.publish_state()` receives:

```python
{
    "instance_tag": "OfficeSpeakersPCLevel",
    "attribute": "level",
    "index": 1,
    "state": -4.0,                  # bool for mute, float for level
    "variable_type": "float",       # "bool" | "float"
    "device_id": "03787145_OfficeSpeakersPCLevel",
    "unique_id": "03787145_OfficeSpeakersPCLevel_level_1",
    "name": "Level",
    "device_name": "Office Speakers PC",
    "identifier": "OfficeSpeakersPCLevel_level_1",
    "min_level": -12.0,             # level only
    "max_level": 12.0,              # level only
}
```

## Error handling

| Exception | Meaning |
|-----------|---------|
| `ClientError` | Base class; also raised for unknown MQTT identifiers. |
| `ClientConnectionError` | Not connected, connection refused, or the device closed the session. |
| `ClientTimeoutError` | Subclass of `ClientConnectionError`; connection, banner or command timed out. |
| `ClientResponseError` | The device answered `-ERR`, or the reply could not be parsed/validated. |

Failures to publish to MQTT from the reader loop are logged and do not affect the Tesira connection.

## Usage example

```python
import asyncio

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
    print(f"Connected to Tesira {tesira_conn.serial_number}")
    await tesira_conn.subscribe_all(subscriptions)

    # Change the level; the Tesira's publishToken update will flow to MQTT.
    await tesira_conn.update_state_and_command("OfficeSpeakersPCLevel_level_1", "-6")

    # Keep the connection alive, reconnecting and resubscribing as needed.
    barrier = asyncio.Barrier(1)
    try:
        await tesira_conn.run(barrier, subscriptions)
    finally:
        await tesira_conn.close()
```

## Testing

`tests/fake_tesira.py` contains a configurable fake Tesira TTP server that reproduces the real device's behaviour (option negotiation, delayed banner with terminal preamble, character-by-character echo, `CR LF`/`CR NUL` endings, `publishToken` before `+OK`, `ALREADY_SUBSCRIBED`, silent and dropped sessions). `tests/test_tesira.py` exercises this class against it; run the suite with `scripts/test`.

---

**Last Updated**: September 2026
**API Version**: 1.1.28

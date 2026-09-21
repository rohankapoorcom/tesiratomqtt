# System Architecture Overview

Tesira2MQTT is a bidirectional MQTT bridge for Biamp Tesira DSPs. It subscribes to the level and mute controls listed in the configuration, publishes their state (and Home Assistant discovery messages) to MQTT, and applies commands received on MQTT back to the Tesira.

## High-Level Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌──────────────────────────────┐
│  Home Assistant │◄──►│   MQTT Broker   │◄──►│  Tesira2MQTT                 │
│                 │    │                 │    │                              │
│  entities via   │    │  state /        │    │  MqttConnection              │
│  MQTT discovery │    │  attributes /   │    │    publish state, discovery, │
│                 │    │  set /          │    │    availability              │
│                 │    │  availability   │    │                              │
└─────────────────┘    └─────────────────┘    │  BiampTesiraConnection       │
                                              │    state store, supervisor,  │
                                              │    two reader loops          │
                                              │                              │
                                              │  BiampTesiraTelnetConnection │
                                              │    line-oriented telnet      │
                                              └──────────────┬───────────────┘
                                                             │ telnet (TTP), 2 sessions
                                                             ▼
                                              ┌──────────────────────────────┐
                                              │  Biamp Tesira DSP            │
                                              │    DSP blocks (instance tags)│
                                              └──────────────────────────────┘
```

## Components

### `src/__init__.py` – entry point

Loads and validates `config.yaml`, connects to MQTT and the Tesira, creates the subscriptions, then runs two tasks in a `TaskGroup`:

- `BiampTesiraConnection.run()` – Tesira supervisor.
- `listen_to_incoming_mqtt_messages()` – applies `<base_topic>/<identifier>/set` messages to the Tesira. Rejected commands are logged, not fatal.

`SIGINT`/`SIGTERM` publish `offline`, cancel the tasks and close the sessions.

### `src/mqtt_connection.py` – MqttConnection

- Publishes retained availability (`online`/`offline`) with a last-will `offline`.
- `publish_state()` publishes `<base_topic>/<identifier>/state` and `/attributes` (retained, QoS 2). On first sight of an identifier it also publishes the Home Assistant discovery message to `homeassistant/<switch|number>/<unique_id>/config` and subscribes to the `set` topic.

aiomqtt does not reconnect on its own: if the broker connection drops the process exits and the orchestrator restarts it.

### `src/tesira.py` – BiampTesiraConnection

- Opens a **subscription** session and a **command** session concurrently and validates the device serial number.
- One **reader loop** per session classifies each line as a `publishToken` update (to the state store and MQTT), a `+OK`/`-ERR` response (to the command in flight) or echo/noise (ignored).
- Commands are serialised per session with a lock; no fixed delays.
- **Supervisor** (`run()`): reconnects with exponential backoff and resubscribes on loss; refreshes subscriptions every `resubscription_time` seconds. Loss is detected via EOF, command timeout or heartbeat failure.

See [Tesira Connection API](../api/tesira-connection.md).

### `src/telnet.py` – BiampTesiraTelnetConnection

Line-oriented wrapper over `telnetlib3`: connects, enables TCP keepalive, waits for the welcome banner, writes `CR LF`-terminated commands and reads `CR LF`/`CR NUL`-terminated lines.

### `src/models/` – configuration

Pydantic models (`Config`, `MqttConfig`, `TesiraConfig`, `Subscription`) validate `config.yaml` at startup.

## Data Flows

### Startup

```
Load config ─► connect MQTT
  └─► open() : both sessions concurrently ─► banner ─► reader loops ─► DEVICE get serialNumber
  └─► subscribe_all() : per subscription (level: get minLevel/maxLevel) ─► subscribe
        └─► Tesira replies with current value ─► state store ─► MQTT state + discovery
  └─► publish "online", start run() and the MQTT listener
```

The Tesira sets sessions up one at a time at ~3 s each, so startup is dominated by ~6 s of session setup; subscriptions take milliseconds.

### State change on the Tesira

```
! "publishToken":"<identifier>" "value":<v>   (subscription session)
  └─► coerce value ─► state store ─► publish_state()
        ├─► <base_topic>/<identifier>/state       (retained)
        ├─► <base_topic>/<identifier>/attributes  (retained)
        └─► homeassistant/.../config              (first time only)
```

### Command from Home Assistant

```
<base_topic>/<identifier>/set
  └─► "<instance_tag> set <attribute> <index> <payload>"   (command session) ─► +OK
Tesira then sends a publishToken update, which follows the path above.
```

### Connection loss

```
EOF / command timeout / heartbeat failure
  └─► in-flight commands fail ─► run() tears down both sessions
        └─► open() + subscribe_all(), backoff 1 s → 60 s while unreachable
```

## MQTT Topic Layout

```
<base_topic>/
├── availability                         {"state": "online"|"offline"}   (retained, last will)
└── <identifier>/                        <instance_tag>_<attribute>_<index>
    ├── state                            JSON value, retained
    ├── attributes                       state-store entry, retained
    └── set                              command topic

homeassistant/
├── switch/<unique_id>/config            mute
└── number/<unique_id>/config            level (min/max/step in dB)
```

Example for `OfficeSpeakersPCLevel` on serial `03787145`:

```
tesira2mqtt/OfficeSpeakersPCLevel_level_1/state
tesira2mqtt/OfficeSpeakersPCLevel_level_1/attributes
tesira2mqtt/OfficeSpeakersPCLevel_level_1/set
homeassistant/number/03787145_OfficeSpeakersPCLevel_level_1/config
```

## Error Handling

| Situation | Behaviour |
|-----------|-----------|
| Tesira unreachable at startup | `offline` published, exit 1. |
| Tesira connection lost | `run()` reconnects and resubscribes. |
| Command times out | Connection rebuilt (a late reply would be matched to the next command). |
| Subscription rejected | Logged and skipped. |
| MQTT command rejected | Logged. |
| MQTT publish fails in reader loop | Logged. |
| MQTT broker connection lost | Process exits; orchestrator restarts it. |
| Invalid configuration | Exit at startup. |

## Deployment

Single container (`Dockerfile`) reading `/config/config.yaml`. Needs network access to the broker and TCP port 23 on the Tesira. The Tesira allows 32 telnet sessions; Tesira2MQTT uses two.

## Testing

`tests/fake_tesira.py` is a fake Tesira server; `tests/` covers the telnet wrapper and the connection (echo, line endings, interleaving, timeouts, disconnects, reconnect/resubscribe). Run with `scripts/test`.

---

**Last Updated**: September 2026
**Architecture Version**: 1.1.28

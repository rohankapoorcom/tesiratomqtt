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

Loads and validates `config.yaml`, then runs independent supervisor tasks:

- `BiampTesiraConnection.run()` – Tesira sessions and subscriptions.
- `MqttConnection.run()` – broker connection; `set` messages are applied to the Tesira, rejected commands are logged.
- `HealthServer.run()` – `aiohttp` probes (`/livez`, `/readyz`, `/health`) on the configured `health` port (default 8080).

Neither Tesira nor MQTT failure affects the other; an unexpected exception restarts that loop. `SIGINT`/`SIGTERM` publish `offline`, close the telnet sessions and stop both loops.

### `src/mqtt_connection.py` – MqttConnection

- Holds the `aiomqtt` client open and reconnects with exponential backoff on loss.
- Keeps the latest entry per identifier. On every (re)connect it publishes `online`, subscribes to each `set` topic and republishes discovery, state and attributes for every entry, so a fresh broker (blue/green deploy) is fully repopulated.
- `publish_state()` publishes `<base_topic>/<identifier>/state` and `/attributes` (retained, QoS 2) plus the discovery message on first sight; while disconnected it only stores the entry and never raises.

See [MQTT Connection API](../api/mqtt-connection.md).

### `src/tesira.py` – BiampTesiraConnection

- Opens a **subscription** session and a **command** session concurrently and validates the device serial number.
- One **reader loop** per session classifies each line as a `publishToken` update (to the state store and MQTT), a `+OK`/`-ERR` response (to the command in flight) or echo/noise (ignored).
- Commands are serialised per session with a lock; no fixed delays.
- **Supervisor** (`run()`): reconnects with exponential backoff and resubscribes on loss; refreshes subscriptions every `resubscription_time` seconds. Loss is detected via EOF, command timeout or heartbeat failure.

See [Tesira Connection API](../api/tesira-connection.md).

### `src/telnet.py` – BiampTesiraTelnetConnection

Line-oriented wrapper over `telnetlib3`: connects, enables TCP keepalive, waits for the welcome banner, writes `CR LF`-terminated commands and reads `CR LF`/`CR NUL`-terminated lines.

### `src/health.py` – HealthServer

`aiohttp` app on `AppRunner`/`TCPSite`. `/livez` is always 200. `/readyz` and `/health` are 200 only when `mqtt.connected` and `tesira.connected`. A connection outage must not restart the process; use `/livez` for liveness and `/readyz` for readiness.

### `src/models/` – configuration

Pydantic models (`Config`, `MqttConfig`, `TesiraConfig`, `Subscription`, `HealthConfig`) validate `config.yaml` at startup.

## Data Flows

### Startup

```
Load config ─► connect MQTT
  └─► open() : both sessions concurrently ─► banner ─► reader loops ─► DEVICE get serialNumber
  └─► subscribe_all() : per subscription (level: get minLevel/maxLevel) ─► subscribe
        └─► Tesira replies with current value ─► state store ─► MQTT state + discovery
  └─► MQTT: connect ─► publish "online" ─► republish stored entries
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

### Tesira connection loss

```
EOF / command timeout / heartbeat failure
  └─► in-flight commands fail ─► run() tears down both sessions
        └─► open() + subscribe_all(), backoff 1 s → 60 s while unreachable
```

### MQTT connection loss

```
broker drops (last will publishes "offline")
  └─► MqttConnection.run() reconnects, backoff 1 s → 60 s
        └─► publish "online" ─► resubscribe set topics ─► republish discovery + latest state
Tesira sessions and subscriptions are untouched; updates during the outage are kept in the store.
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
| Tesira unreachable or connection lost | `run()` retries with backoff and resubscribes. |
| Command times out | Connection rebuilt (a late reply would be matched to the next command). |
| Subscription rejected | Logged and skipped. |
| MQTT command rejected | Logged. |
| MQTT publish fails in reader loop | Logged. |
| MQTT broker connection lost | `MqttConnection.run()` reconnects and republishes everything; Tesira untouched. |
| Invalid configuration | Exit at startup. |

## Deployment

Single container (`Dockerfile`) reading `/config/config.yaml`. Needs network access to the broker and TCP port 23 on the Tesira. Probes default to port `8080`; the image `HEALTHCHECK` hits `$HEALTHCHECK_URL` (default `http://127.0.0.1:8080/health`). The Tesira allows 32 telnet sessions; Tesira2MQTT uses two.

```yaml
livenessProbe:
  httpGet: { path: /livez, port: 8080 }
readinessProbe:
  httpGet: { path: /readyz, port: 8080 }
  initialDelaySeconds: 10
```

## Testing

`tests/fake_tesira.py` and `tests/fake_mqtt.py` stand in for the device and the broker; `tests/test_bridge.py` runs both supervisors together through Tesira and MQTT outages. Run with `scripts/test`.

---

**Last Updated**: September 2026
**Architecture Version**: 1.1.35

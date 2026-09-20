# System Architecture Overview

## Overview

Tesira2MQTT is a bidirectional MQTT bridge for Biamp Tesira Digital Signal Processors (DSPs). It keeps two telnet sessions open to a Tesira, subscribes to the level and mute controls listed in the configuration, publishes their state (and Home Assistant discovery messages) to an MQTT broker, and applies commands received on MQTT back to the Tesira.

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

### `src/__init__.py` – application entry point

Parses arguments, loads and validates `config.yaml`, connects to MQTT, establishes the Tesira connection and creates all subscriptions, then runs two long-lived tasks in a `TaskGroup`:

- `BiampTesiraConnection.run()` – supervises the Tesira connection (see below).
- `listen_to_incoming_mqtt_messages()` – applies every message on a `<base_topic>/<identifier>/set` topic to the Tesira. Rejected or undeliverable commands are logged, not fatal.

`SIGINT`/`SIGTERM` publish `offline` on the availability topic, cancel the tasks and close the telnet sessions.

### `src/mqtt_connection.py` – MqttConnection

- Publishes the retained availability message (`online`/`offline`) and a last-will `offline`.
- `publish_state()` publishes the value to `<base_topic>/<identifier>/state` and the full entry to `<base_topic>/<identifier>/attributes` (both retained, QoS 2). The first time an identifier is seen it also publishes the Home Assistant discovery message and subscribes to the identifier's `set` topic.
- Discovery messages go to `homeassistant/<switch|number>/<unique_id>/config`, where `unique_id` is `<serial>_<instance_tag>_<attribute>_<index>`.

The MQTT client (aiomqtt) does not reconnect on its own: if the broker connection drops the process exits and relies on the container orchestrator to restart it.

### `src/tesira.py` – BiampTesiraConnection

- Opens the **subscription** and **command** sessions concurrently and verifies the device by reading and validating its serial number.
- Runs one **reader loop** per session. Every incoming line is classified as a subscription update (`! "publishToken"`), a command response (`+OK`/`-ERR`) or noise (the Tesira echoes every command back; blank lines; terminal preamble). Updates go to the state store and MQTT; responses resolve the command currently awaiting one on that session; noise is ignored.
- Serialises commands per session with a lock and matches each command to exactly one response. No fixed delays are used anywhere.
- Keeps the **state store**: one dictionary per subscription with the current value, the Home Assistant metadata (`unique_id`, `device_id`, names) and, for levels, the min/max range.
- **Supervisor** (`run()`): reconnects with exponential backoff and re-creates all subscriptions when the connection is lost, and refreshes them every `resubscription_time` seconds. Connection loss is detected by EOF/read errors, by a command that gets no response within `command_timeout`, and by a periodic heartbeat on the command session.

See [Tesira Connection API](../api/tesira-connection.md) for details.

### `src/telnet.py` – BiampTesiraTelnetConnection

Thin line-oriented wrapper over `telnetlib3`: connects, enables TCP keepalive, waits for the `Welcome to the Tesira Text Protocol Server` banner (skipping the terminal preamble the Tesira prints first), writes commands terminated with `CR LF` and reads lines terminated with either `CR LF` or `CR NUL`. It knows nothing about TTP beyond the banner.

### `src/models/` – configuration models

Pydantic models (`Config`, `MqttConfig`, `TesiraConfig`, `Subscription`) validate `config.yaml` on startup. `Subscription` is hashable so duplicates collapse into a set.

## Data Flows

### Startup

```
Load + validate config
  └─► Connect to MQTT ─► create MqttConnection
        └─► BiampTesiraConnection.open()
              ├─► open subscription + command sessions (concurrently)
              ├─► wait for banner on each, start reader loops
              └─► DEVICE get serialNumber ─► validate
        └─► subscribe_all()
              └─► per subscription: (level: get minLevel/maxLevel) ─► subscribe
                    └─► Tesira replies with current value ─► state store ─► MQTT state + discovery
        └─► publish availability "online", start run() and MQTT listener
```

The Tesira admits sessions one at a time and takes about three seconds each before sending its banner, so startup is dominated by roughly six seconds of session setup; creating the subscriptions themselves takes tens of milliseconds each.

### State change on the Tesira

```
Tesira sends  ! "publishToken":"<identifier>" "value":<v>   (subscription session)
  └─► reader loop classifies as update
        └─► parse + coerce value (bool / float) ─► state store
              └─► MqttConnection.publish_state()
                    ├─► <base_topic>/<identifier>/state       (retained)
                    ├─► <base_topic>/<identifier>/attributes  (retained)
                    └─► homeassistant/.../config              (first time only)
```

### Command from Home Assistant

```
MQTT message on <base_topic>/<identifier>/set
  └─► update_state_and_command(identifier, payload)
        └─► "<instance_tag> set <attribute> <index> <payload>"   (command session)
              └─► +OK resolves the command
Tesira then sends a publishToken update on the subscription session,
which flows through the state-change path above and updates MQTT.
```

### Connection loss

```
EOF / read error / command timeout / heartbeat failure
  └─► in-flight commands fail with ClientConnectionError
  └─► run(): tear down both sessions
        └─► open() + subscribe_all(), retried with backoff 1s → 60s while the Tesira is unreachable
              └─► current values are re-published as the subscriptions are re-created
```

## MQTT Topic Layout

```
<base_topic>/
├── availability                         {"state": "online"|"offline"}   (retained, last will)
└── <identifier>/                        identifier = <instance_tag>_<attribute>_<index>
    ├── state                            JSON value (true/false or number), retained
    ├── attributes                       JSON copy of the state-store entry, retained
    └── set                              command topic (subscribed by Tesira2MQTT)

homeassistant/
├── switch/<unique_id>/config            mute controls
└── number/<unique_id>/config            level controls (min/max/step in dB)
```

Example for `OfficeSpeakersPCLevel` on device serial `03787145`:

```
tesira2mqtt/OfficeSpeakersPCLevel_level_1/state
tesira2mqtt/OfficeSpeakersPCLevel_level_1/attributes
tesira2mqtt/OfficeSpeakersPCLevel_level_1/set
homeassistant/number/03787145_OfficeSpeakersPCLevel_level_1/config
```

## Error Handling

| Situation | Behaviour |
|-----------|-----------|
| Tesira unreachable at startup | Logged, `offline` published, process exits with status 1. |
| Tesira connection lost while running | `run()` reconnects with backoff and re-creates all subscriptions. |
| Command times out | Connection treated as dead and rebuilt (a late reply would otherwise be attributed to the next command). |
| Tesira rejects a subscription (`-ERR`) | Logged and skipped; other subscriptions continue. |
| Tesira rejects a command from MQTT | Logged; the bridge keeps running. |
| MQTT publish fails inside the reader loop | Logged; the Tesira connection is unaffected. |
| MQTT broker connection lost | Process exits; restart via the container orchestrator. |
| Invalid configuration | Validation error at startup, process exits. |

## Deployment

The application is packaged as a single container (see `Dockerfile`) that reads `/config/config.yaml` by default. It needs network access to the MQTT broker and to TCP port 23 on the Tesira. The Tesira allows up to 32 concurrent telnet sessions; Tesira2MQTT uses two and closes them on shutdown.

## Testing

`tests/` contains a fake Tesira TTP server (`fake_tesira.py`) and pytest suites for the telnet wrapper and the Tesira connection covering echo handling, line endings, interleaved updates, timeouts, disconnects and the supervisor's reconnect and resubscribe behaviour. Run them with `scripts/test`.

---

**Last Updated**: September 2026
**Architecture Version**: 1.1.28

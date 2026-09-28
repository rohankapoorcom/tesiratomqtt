# MQTT Connection API Documentation

## Overview

`MqttConnection` (`src/mqtt_connection.py`) owns the connection to the MQTT broker. It keeps the last delivered entry for every subscribed control so that after a broker outage (for example a blue/green redeploy) it can republish availability, discovery, command subscriptions and state without involving the Tesira.

## How it works

`run()` holds an `aiomqtt.Client` open and reconnects with exponential backoff (1 s to 60 s) whenever the connection drops. On every (re)connect it:

1. publishes `online` to `<base_topic>/availability` (retained; the last will is `offline`),
2. for every stored entry: subscribes to `<base_topic>/<identifier>/set`, publishes the Home Assistant discovery message, then the state and attributes.

`publish_state()` publishes the entry if connected and never raises, so the Tesira side keeps running through an outage. It returns `False` when the state was not delivered, and nothing is stored for replay. The Tesira publisher keeps such values and sends the latest one once the broker is back, or drops it if the Tesira session ends first, so a value from a dead session is never published late.

Incoming messages on `<base_topic>/<identifier>/set` are passed to the command handler given to `run()`. Handler errors are logged, not fatal.

## Class: MqttConnection

```python
def __init__(self, config: MqttConfig) -> None
```

No connection is made until `run()`.

### Properties

- `connected: bool` – broker currently connected.

### Methods

#### run(on_command)

Stays connected until `close()`. `on_command(identifier, payload)` is awaited for each `set` message. The entry point runs this as a long-lived task.

#### close()

Publishes `offline` (if connected) and makes `run()` return.

#### wait_connected()

Waits until the broker is connected.

#### publish_state(name, data, serial)

If connected, publishes the entry (see the state store in [Tesira Connection](tesira-connection.md)) and keeps it for replay once its state is delivered:

- `<base_topic>/<identifier>/state` – JSON value, retained, QoS 1
- `<base_topic>/<identifier>/attributes` – the entry, retained, QoS 1
- `homeassistant/<switch|number>/<serial>_<identifier>/config` – on the first publish of an identifier per connection. The payload's `unique_id` stays `<serial>_<instance_tag>_<attribute>_<index>`, so Home Assistant keeps the same entity. For a tag whose identifier is encoded, the retained config, state and attributes left at its earlier raw-tag identifier are cleared first; that identifier can never belong to another subscription.

Never raises. Returns `False` when disconnected or when the broker rejects the publish, so the caller can retry.

#### publish_status(status="online")

Publishes `{"state": <status>}` to `<base_topic>/availability` (retained). Raises `aiomqtt.MqttError` if not connected.

## Discovery message

```json
{
  "dev": {"ids": "tesira2mqtt_03787145_Mic1", "name": "Mic 1", "mf": "Biamp Systems, LLC", "sn": "03787145"},
  "origin": {"name": "Tesira2MQTT"},
  "availability": [{"topic": "tesira2mqtt/availability", "value_template": "{{ value_json.state }}"}],
  "name": "Mute",
  "state_topic": "tesira2mqtt/Mic1_mute_1/state",
  "command_topic": "tesira2mqtt/Mic1_mute_1/set",
  "unique_id": "03787145_Mic1_mute_1",
  "value_template": "{{ value_json }}",
  "default_entity_id": "switch.mic_1_mute",
  "payload_on": true,
  "payload_off": false
}
```

`level` controls become `number` entities with `min`/`max` from the block's `minLevel`/`maxLevel`, `step: 0.1` and `unit_of_measurement: dB`.

## Example

```python
import asyncio

from models import MqttConfig
from mqtt_connection import MqttConnection


async def main():
    mqtt = MqttConnection(MqttConfig(base_topic="tesira2mqtt", server="broker", port=1883,
                                     user="u", password="p", keepalive=60))

    async def on_command(identifier: str, payload: str) -> None:
        print(f"set {identifier} = {payload}")

    task = asyncio.create_task(mqtt.run(on_command))
    await mqtt.wait_connected()
    try:
        await asyncio.sleep(3600)
    finally:
        await mqtt.close()
        await task
```

## Testing

`tests/fake_mqtt.py` replaces `aiomqtt.Client` with an in-memory broker that can refuse or drop connections. `tests/test_mqtt.py` covers this class; `tests/test_bridge.py` runs it together with the Tesira connection.

---

**Last Updated**: September 2026
**API Version**: 1.1.37

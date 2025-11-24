# MQTT Connection API Documentation

## Overview

The `MqttConnection` class manages all MQTT broker communications, including publishing device states, Home Assistant discovery messages, and availability status.

## Class: MqttConnection

```python
class MqttConnection:
    """MqttConnection used for communication with MQTT."""
```

### Constructor

```python
def __init__(self, client: aiomqtt.Client, base_topic: str) -> None:
    """Initialize an object to manage MQTT communications."""
```

**Parameters:**
- `client` (aiomqtt.Client): The MQTT client instance for broker communication
- `base_topic` (str): Base topic prefix for all MQTT messages

**Example:**
```python
import aiomqtt
from mqtt_connection import MqttConnection

async with aiomqtt.Client("mqtt.broker.com") as client:
    mqtt_conn = MqttConnection(client, "tesira2mqtt")
```

### Methods

#### publish_status()

```python
async def publish_status(self, status: str = "online") -> None:
    """Indicate that the server is available."""
```

Publishes the availability status of the Tesira2MQTT service to the MQTT broker.

**Parameters:**
- `status` (str, optional): Availability status. Defaults to "online". Use "offline" for shutdown.

**MQTT Topic:** `{base_topic}/availability`

**Payload:** JSON object with state information
```json
{"state": "online"}
```

**Example:**
```python
# Publish online status
await mqtt_conn.publish_status("online")

# Publish offline status during shutdown
await mqtt_conn.publish_status("offline")
```

#### publish_state()

```python
async def publish_state(self, name: str, data: dict, serial: str) -> None:
    """Publish the state of an object."""
```

Publishes the current state of a Tesira device attribute to the MQTT broker.

**Parameters:**
- `name` (str): Display name of the attribute (e.g., "Level", "Mute")
- `data` (dict): State data containing device information
- `serial` (str): Unique identifier for the device/attribute combination

**Data Structure:**
```python
data = {
    "state": "50",           # Current state value
    "identifier": "unique_id", # Unique identifier
    "device_name": "Office Speakers PC",  # Device name
    "attribute": "level",    # Attribute type
    "instance_tag": "OfficeSpeakersPCLevel"  # Tesira instance tag
}
```

**MQTT Topics Published:**
- State: `{base_topic}/{identifier}/state`
- Attributes: `{base_topic}/{identifier}/attributes`

**Example:**
```python
data = {
    "state": "75",
    "identifier": "office_speakers_pc_level",
    "device_name": "Office Speakers PC",
    "attribute": "level",
    "instance_tag": "OfficeSpeakersPCLevel"
}

await mqtt_conn.publish_state("Level", data, "office_speakers_pc_level")
```

#### publish_discovery()

```python
async def publish_discovery(self, name: str, data: dict, serial: str) -> None:
    """Publish Home Assistant discovery message."""
```

Publishes Home Assistant discovery messages to automatically configure entities in Home Assistant.

**Parameters:**
- `name` (str): Display name of the attribute
- `data` (dict): Discovery data containing entity configuration
- `serial` (str): Unique identifier for the device

**Discovery Data Structure:**
```python
data = {
    "name": "Office Speakers PC Level",
    "state_topic": "tesira2mqtt/office_speakers_pc_level/state",
    "command_topic": "tesira2mqtt/office_speakers_pc_level/set",
    "availability_topic": "tesira2mqtt/availability",
    "device": {
        "identifiers": ["tesira2mqtt_office_speakers_pc"],
        "name": "Office Speakers PC",
        "manufacturer": "Biamp Systems, LLC",
        "model": "Tesira DSP"
    },
    "unique_id": "tesira2mqtt_office_speakers_pc_level"
}
```

**MQTT Topic:** `homeassistant/{entity_type}/{unique_id}/config`

**Entity Types:**
- `number`: For level controls (0-100 range)
- `switch`: For mute controls (on/off)

**Example:**
```python
discovery_data = {
    "name": "Office Speakers PC Level",
    "state_topic": "tesira2mqtt/office_speakers_pc_level/state",
    "command_topic": "tesira2mqtt/office_speakers_pc_level/set",
    "availability_topic": "tesira2mqtt/availability",
    "min": 0,
    "max": 100,
    "step": 1,
    "unit_of_measurement": "%",
    "device": {
        "identifiers": ["tesira2mqtt_office_speakers_pc"],
        "name": "Office Speakers PC",
        "manufacturer": "Biamp Systems, LLC"
    },
    "unique_id": "tesira2mqtt_office_speakers_pc_level"
}

await mqtt_conn.publish_discovery("Level", discovery_data, "office_speakers_pc_level")
```

### Constants

#### AVAILABILITY_TOPIC
```python
AVAILABILITY_TOPIC = "{0}/availability"
```
Template for availability topic. Use with `.format(base_topic)`.

#### MANUFACTURER
```python
MANUFACTURER = "Biamp Systems, LLC"
```
Manufacturer name used in Home Assistant discovery messages.

### Error Handling

The MQTT connection handles various error conditions:

- **Connection Errors**: Automatic reconnection attempts
- **Publish Failures**: Logged with retry mechanisms
- **Topic Validation**: Ensures valid MQTT topic formats
- **Payload Validation**: Validates JSON payloads before publishing

### Usage Examples

#### Basic Setup
```python
import asyncio
import aiomqtt
from mqtt_connection import MqttConnection

async def main():
    async with aiomqtt.Client("mqtt.broker.com", port=1883) as client:
        mqtt_conn = MqttConnection(client, "tesira2mqtt")

        # Publish availability
        await mqtt_conn.publish_status("online")

        # Publish device state
        data = {
            "state": "50",
            "identifier": "test_device_level",
            "device_name": "Test Device",
            "attribute": "level",
            "instance_tag": "TestDeviceLevel"
        }
        await mqtt_conn.publish_state("Level", data, "test_device_level")

if __name__ == "__main__":
    asyncio.run(main())
```

#### Home Assistant Integration
```python
async def setup_home_assistant_entity(mqtt_conn, device_info):
    # Publish discovery message
    discovery_data = {
        "name": f"{device_info['device_name']} {device_info['attribute'].title()}",
        "state_topic": f"tesira2mqtt/{device_info['identifier']}/state",
        "command_topic": f"tesira2mqtt/{device_info['identifier']}/set",
        "availability_topic": "tesira2mqtt/availability",
        "device": {
            "identifiers": [f"tesira2mqtt_{device_info['device_name'].lower().replace(' ', '_')}"],
            "name": device_info['device_name'],
            "manufacturer": "Biamp Systems, LLC"
        },
        "unique_id": f"tesira2mqtt_{device_info['identifier']}"
    }

    entity_type = "number" if device_info['attribute'] == "level" else "switch"
    await mqtt_conn.publish_discovery(
        device_info['attribute'].title(),
        discovery_data,
        device_info['identifier']
    )
```

### Best Practices

1. **Topic Naming**: Use consistent, descriptive topic names
2. **Retained Messages**: Use retained messages for state topics
3. **QoS Levels**: Use QoS 2 for critical messages
4. **Error Handling**: Always handle connection and publish errors
5. **Availability**: Always publish availability status on startup/shutdown

---

**Last Updated**: September 2025
**API Version**: 1.1.11

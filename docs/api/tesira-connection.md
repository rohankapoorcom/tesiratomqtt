# Tesira Connection API Documentation

## Overview

The `BiampTesiraConnection` class manages communication with Biamp Tesira DSPs through telnet connections. It handles device subscriptions, command execution, and state monitoring.

This class implements the **Tesira Text Protocol (TTP)** for communication with Biamp Tesira DSPs. For detailed information about TTP commands, responses, and subscription features, refer to the official Biamp documentation:

**[Tesira Text Protocol Documentation](https://support.biamp.com/Tesira/Control/Tesira_Text_Protocol)**

## TTP Command Mapping

The API methods in this class map to specific TTP commands:

| API Method | TTP Command | Description |
|------------|-------------|-------------|
| `subscribe()` | `subscribe` | Subscribe to device attribute changes |
| `unsubscribe()` | `unsubscribe` | Remove subscription to device attribute |
| `set_level()` | `set level` | Set audio level for device |
| `set_mute()` | `set mute` | Set mute state for device |
| `get_level()` | `get level` | Get current audio level |
| `get_mute()` | `get mute` | Get current mute state |

### TTP Command Format

All TTP commands follow this format:
```
<Instance Tag> <Command> <Attribute> <Index> [Custom Label] [Value]
```

Example TTP commands sent by this API:
```
OfficeSpeakersPCLevel subscribe level 1 MyCustomLabel
OfficeSpeakersPCLevel set level 1 75
OfficeSpeakersPCLevel get mute 1
```

## Class: BiampTesiraConnection

```python
class BiampTesiraConnection:
    """BiampTesiraConnection used for communication with Biamp Tesira DSPs."""
```

### Constructor

```python
def __init__(self, tesira: TesiraConfig, mqtt: MqttConnection) -> None:
    """Initiate a Biamp Tesira connection."""
```

**Parameters:**
- `tesira` (TesiraConfig): Configuration object containing Tesira device connection details
- `mqtt` (MqttConnection): MQTT connection instance for publishing device states

**Example:**
```python
from models import TesiraConfig
from mqtt_connection import MqttConnection
from tesira import BiampTesiraConnection

tesira_config = TesiraConfig(
    host="tesira.device.com",
    port=23,
    resubscription_time=300
)

mqtt_conn = MqttConnection(mqtt_client, "tesira2mqtt")
tesira_conn = BiampTesiraConnection(tesira_config, mqtt_conn)
```

### Methods

#### open()

```python
async def open(self) -> None:
    """Open the telnet clients for communication."""
```

Establishes telnet connections to the Tesira device. Creates separate connections for subscriptions and commands.

**Raises:**
- `ClientConnectionError`: If unable to connect to Tesira device
- `ClientTimeoutError`: If connection times out

**Example:**
```python
try:
    await tesira_conn.open()
    print("Successfully connected to Tesira device")
except ClientConnectionError as e:
    print(f"Failed to connect: {e}")
```

#### close()

```python
async def close(self) -> None:
    """Close the telnet clients."""
```

Closes all telnet connections to the Tesira device.

**Example:**
```python
await tesira_conn.close()
print("Tesira connections closed")
```

#### subscribe()

```python
async def subscribe(self, subscription: Subscription) -> None:
    """Subscribe to a Tesira device attribute."""
```

Subscribes to a specific Tesira device attribute for state monitoring.

**Parameters:**
- `subscription` (Subscription): Subscription configuration object

**Subscription Object:**
```python
subscription = Subscription(
    instance_tag="OfficeSpeakersPCLevel",
    attribute="level",
    index=1,
    name="Level",
    device_name="Office Speakers PC"
)
```

**Tesira Command:** `SUBSCRIBE "OfficeSpeakersPCLevel" level 1`

**Example:**
```python
from models import Subscription

subscription = Subscription(
    instance_tag="OfficeSpeakersPCLevel",
    attribute="level",
    index=1,
    name="Level",
    device_name="Office Speakers PC"
)

await tesira_conn.subscribe(subscription)
```

#### unsubscribe()

```python
async def unsubscribe(self, subscription: Subscription) -> None:
    """Unsubscribe from a Tesira device attribute."""
```

Unsubscribes from a Tesira device attribute.

**Parameters:**
- `subscription` (Subscription): Subscription configuration object to unsubscribe from

**Tesira Command:** `UNSUBSCRIBE "OfficeSpeakersPCLevel" level 1`

**Example:**
```python
await tesira_conn.unsubscribe(subscription)
```

#### set_level()

```python
async def set_level(self, instance_tag: str, index: int, level: int) -> None:
    """Set the level of a Tesira device."""
```

Sets the audio level for a specific Tesira device instance.

**Parameters:**
- `instance_tag` (str): Tesira device instance tag
- `index` (int): Device index (usually 1)
- `level` (int): Level value (0-100)

**Tesira Command:** `SET "OfficeSpeakersPCLevel" level 1 75`

**Example:**
```python
# Set level to 75%
await tesira_conn.set_level("OfficeSpeakersPCLevel", 1, 75)
```

#### set_mute()

```python
async def set_mute(self, instance_tag: str, index: int, mute: bool) -> None:
    """Set the mute state of a Tesira device."""
```

Sets the mute state for a specific Tesira device instance.

**Parameters:**
- `instance_tag` (str): Tesira device instance tag
- `index` (int): Device index (usually 1)
- `mute` (bool): Mute state (True = muted, False = unmuted)

**Tesira Command:** `SET "OfficeSpeakersPCLevel" mute 1 1` (muted) or `SET "OfficeSpeakersPCLevel" mute 1 0` (unmuted)

**Example:**
```python
# Mute the device
await tesira_conn.set_mute("OfficeSpeakersPCLevel", 1, True)

# Unmute the device
await tesira_conn.set_mute("OfficeSpeakersPCLevel", 1, False)
```

#### get_level()

```python
async def get_level(self, instance_tag: str, index: int) -> int:
    """Get the current level of a Tesira device."""
```

Retrieves the current audio level for a specific Tesira device instance.

**Parameters:**
- `instance_tag` (str): Tesira device instance tag
- `index` (int): Device index (usually 1)

**Returns:**
- `int`: Current level value (0-100)

**Tesira Command:** `GET "OfficeSpeakersPCLevel" level 1`

**Example:**
```python
current_level = await tesira_conn.get_level("OfficeSpeakersPCLevel", 1)
print(f"Current level: {current_level}%")
```

#### get_mute()

```python
async def get_mute(self, instance_tag: str, index: int) -> bool:
    """Get the current mute state of a Tesira device."""
```

Retrieves the current mute state for a specific Tesira device instance.

**Parameters:**
- `instance_tag` (str): Tesira device instance tag
- `index` (int): Device index (usually 1)

**Returns:**
- `bool`: Current mute state (True = muted, False = unmuted)

**Tesira Command:** `GET "OfficeSpeakersPCLevel" mute 1`

**Example:**
```python
is_muted = await tesira_conn.get_mute("OfficeSpeakersPCLevel", 1)
print(f"Device is {'muted' if is_muted else 'unmuted'}")
```

### Properties

#### serial_number

```python
@property
def serial_number(self) -> str | None:
    """Get the serial number of the connected Tesira device."""
```

Returns the serial number of the connected Tesira device, or None if not connected.

**Example:**
```python
if tesira_conn.serial_number:
    print(f"Connected to Tesira device: {tesira_conn.serial_number}")
```

### Error Handling

The Tesira connection handles various error conditions:

#### Custom Exceptions

- `ClientConnectionError`: Connection failures to Tesira device
- `ClientTimeoutError`: Command timeouts
- `ClientResponseError`: Invalid responses from Tesira device
- `ClientError`: General client errors

#### Error Handling Example

```python
from errors import ClientConnectionError, ClientTimeoutError

try:
    await tesira_conn.set_level("OfficeSpeakersPCLevel", 1, 75)
except ClientConnectionError:
    print("Lost connection to Tesira device")
    # Attempt to reconnect
    await tesira_conn.open()
except ClientTimeoutError:
    print("Command timed out")
    # Retry or handle gracefully
except Exception as e:
    print(f"Unexpected error: {e}")
```

### Connection Management

#### Automatic Reconnection

The connection automatically handles reconnection scenarios:

```python
async def ensure_connection(self) -> None:
    """Ensure telnet connections are active."""
```

This method checks connection status and reconnects if necessary.

#### Resubscription Management

The connection automatically resubscribes to device attributes at regular intervals:

```python
async def _resubscribe_all(self) -> None:
    """Resubscribe to all active subscriptions."""
```

### Usage Examples

#### Complete Setup and Control Example

```python
import asyncio
from models import TesiraConfig, Subscription
from mqtt_connection import MqttConnection
from tesira import BiampTesiraConnection

async def main():
    # Configuration
    tesira_config = TesiraConfig(
        host="tesira.device.com",
        port=23,
        resubscription_time=300
    )

    # MQTT connection (assuming mqtt_client is already set up)
    mqtt_conn = MqttConnection(mqtt_client, "tesira2mqtt")

    # Tesira connection
    tesira_conn = BiampTesiraConnection(tesira_config, mqtt_conn)

    try:
        # Connect to Tesira device
        await tesira_conn.open()
        print(f"Connected to Tesira: {tesira_conn.serial_number}")

        # Subscribe to device attributes
        subscription = Subscription(
            instance_tag="OfficeSpeakersPCLevel",
            attribute="level",
            index=1,
            name="Level",
            device_name="Office Speakers PC"
        )
        await tesira_conn.subscribe(subscription)

        # Get current state
        current_level = await tesira_conn.get_level("OfficeSpeakersPCLevel", 1)
        print(f"Current level: {current_level}%")

        # Set new level
        await tesira_conn.set_level("OfficeSpeakersPCLevel", 1, 80)
        print("Level set to 80%")

        # Mute the device
        await tesira_conn.set_mute("OfficeSpeakersPCLevel", 1, True)
        print("Device muted")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        await tesira_conn.close()

if __name__ == "__main__":
    asyncio.run(main())
```

#### Monitoring Multiple Devices

```python
async def monitor_multiple_devices(tesira_conn, subscriptions):
    """Monitor multiple Tesira device attributes."""

    # Subscribe to all devices
    for subscription in subscriptions:
        await tesira_conn.subscribe(subscription)

    # Monitor state changes (this would typically be in a loop)
    while True:
        try:
            # Check each device
            for subscription in subscriptions:
                if subscription.attribute == "level":
                    level = await tesira_conn.get_level(
                        subscription.instance_tag,
                        subscription.index
                    )
                    print(f"{subscription.device_name} level: {level}%")
                elif subscription.attribute == "mute":
                    muted = await tesira_conn.get_mute(
                        subscription.instance_tag,
                        subscription.index
                    )
                    print(f"{subscription.device_name} muted: {muted}")

            await asyncio.sleep(5)  # Check every 5 seconds

        except Exception as e:
            print(f"Monitoring error: {e}")
            await asyncio.sleep(10)  # Wait before retrying
```

### Best Practices

1. **Connection Management**: Always use try/finally blocks to ensure connections are closed
2. **Error Handling**: Handle all connection and command errors gracefully
3. **Resubscription**: Let the connection handle automatic resubscription
4. **Serial Number**: Check serial number after connection to verify device identity
5. **Command Validation**: Validate parameters before sending commands to Tesira device

---

**Last Updated**: September 2025
**API Version**: 1.1.10

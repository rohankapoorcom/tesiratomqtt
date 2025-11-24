# Configuration Guide

## Overview

This is the complete configuration guide for Tesira2MQTT. The configuration is defined in YAML format and validated using Pydantic models. This guide covers everything from basic setup to advanced configuration scenarios.

## Quick Start

Create a `config.yaml` file with the following basic structure:

```yaml
mqtt:
  base_topic: tesira2mqtt
  server: your-mqtt-broker.com
  port: 1883
  user: your-mqtt-username
  password: your-mqtt-password
  keepalive: 60

tesira:
  host: your-tesira-device.com
  port: 23
  resubscription_time: 300

subscriptions:
  - instance_tag: OfficeSpeakersPCLevel
    attribute: level
    index: 1
    name: Level
    device_name: Office Speakers PC
  - instance_tag: OfficeSpeakersPCLevel
    attribute: mute
    index: 1
    name: Mute
    device_name: Office Speakers PC
```

## Configuration File Structure

The complete configuration schema:

```yaml
mqtt:
  base_topic: string
  server: string
  port: integer
  user: string
  password: string
  keepalive: integer

tesira:
  host: string
  port: integer
  resubscription_time: integer

subscriptions:
  - instance_tag: string
    attribute: string
    index: integer
    name: string
    device_name: string
```

## MQTT Configuration

### Section: `mqtt`

Controls MQTT broker connection and message publishing settings.

#### Required Fields

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `base_topic` | `string` | Base topic prefix for all MQTT messages | `tesira2mqtt` |
| `server` | `string` | MQTT broker hostname or IP address | `mqtt.broker.com` |
| `port` | `integer` | MQTT broker port number | `1883` |
| `user` | `string` | MQTT broker username | `mqtt_user` |
| `password` | `string` | MQTT broker password | `mqtt_password` |

#### Optional Fields

| Field | Type | Default | Description | Example |
|-------|------|---------|-------------|---------|
| `keepalive` | `integer` | `60` | MQTT keepalive interval in seconds | `60` |

#### Example

```yaml
mqtt:
  base_topic: tesira2mqtt
  server: mqtt.broker.com
  port: 1883
  user: mqtt_user
  password: mqtt_password
  keepalive: 60
```

#### Validation Rules

- `base_topic`: Must be non-empty string, will be used as topic prefix
- `server`: Must be non-empty string, valid hostname or IP address
- `port`: Must be integer between 1-65535
- `user`: Must be non-empty string
- `password`: Must be non-empty string
- `keepalive`: Must be positive integer (recommended: 60-300 seconds)

## Tesira Configuration

### Section: `tesira`

Controls Biamp Tesira device connection settings.

#### Required Fields

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `host` | `string` | Tesira device hostname or IP address | `tesira.device.com` |
| `port` | `integer` | Tesira telnet port number | `23` |

#### Optional Fields

| Field | Type | Default | Description | Example |
|-------|------|---------|-------------|---------|
| `resubscription_time` | `integer` | `300` | Resubscription interval in seconds | `300` |

#### Example

```yaml
tesira:
  host: tesira.device.com
  port: 23
  resubscription_time: 300
```

#### Validation Rules

- `host`: Must be non-empty string, valid hostname or IP address
- `port`: Must be integer between 1-65535 (default Tesira port: 23)
- `resubscription_time`: Must be positive integer (recommended: 60-600 seconds)

## Subscription Configuration

### Section: `subscriptions`

Defines which Tesira device attributes to monitor and control. Each subscription represents a single attribute of a device.

#### Subscription Object Structure

| Field | Type | Required | Description | Example |
|-------|------|----------|-------------|---------|
| `instance_tag` | `string` | Yes | Tesira device instance tag | `OfficeSpeakersPCLevel` |
| `attribute` | `string` | Yes | Attribute type (`level` or `mute`) | `level` |
| `index` | `integer` | Yes | Device index (usually 1) | `1` |
| `name` | `string` | Yes | Display name for the attribute | `Level` |
| `device_name` | `string` | Yes | Device name for grouping | `Office Speakers PC` |

#### Example

```yaml
subscriptions:
  - instance_tag: OfficeSpeakersPCLevel
    attribute: level
    index: 1
    name: Level
    device_name: Office Speakers PC
  - instance_tag: OfficeSpeakersPCLevel
    attribute: mute
    index: 1
    name: Mute
    device_name: Office Speakers PC
  - instance_tag: LivingRoomSpeakersLevel
    attribute: level
    index: 1
    name: Level
    device_name: Living Room Speakers
```

#### Validation Rules

- `instance_tag`: Must be non-empty string, matches Tesira device configuration
- `attribute`: Must be exactly `level` or `mute`
- `index`: Must be positive integer (typically 1)
- `name`: Must be non-empty string, used for display purposes
- `device_name`: Must be non-empty string, used for device grouping

## Complete Configuration Example

```yaml
# MQTT Broker Configuration
mqtt:
  base_topic: tesira2mqtt
  server: mqtt.broker.com
  port: 1883
  user: mqtt_user
  password: mqtt_password
  keepalive: 60

# Tesira Device Configuration
tesira:
  host: tesira.device.com
  port: 23
  resubscription_time: 300

# Device Subscriptions
subscriptions:
  # Office Speakers PC - Level Control
  - instance_tag: OfficeSpeakersPCLevel
    attribute: level
    index: 1
    name: Level
    device_name: Office Speakers PC

  # Office Speakers PC - Mute Control
  - instance_tag: OfficeSpeakersPCLevel
    attribute: mute
    index: 1
    name: Mute
    device_name: Office Speakers PC

  # Office Speakers Mac - Level Control
  - instance_tag: OfficeSpeakersMacLevel
    attribute: level
    index: 1
    name: Level
    device_name: Office Speakers Mac

  # Office Speakers Mac - Mute Control
  - instance_tag: OfficeSpeakersMacLevel
    attribute: mute
    index: 1
    name: Mute
    device_name: Office Speakers Mac

  # Living Room Ceiling Mics - Mute Control
  - instance_tag: LivingRoomCeilingMicsMute
    attribute: mute
    index: 1
    name: Mute
    device_name: Living Room Ceiling Mics
```

## Configuration Validation

### Automatic Validation

The application automatically validates the configuration file on startup:

```python
from models import Config
import yaml

def load_and_validate_config(config_path: str) -> Config:
    """Load and validate configuration file."""
    with open(config_path, 'r') as file:
        config_data = yaml.safe_load(file)

    # Pydantic automatically validates the configuration
    return Config(**config_data)
```

### Common Validation Errors

#### Invalid Attribute Type

```yaml
# ❌ Invalid - attribute must be 'level' or 'mute'
subscriptions:
  - instance_tag: OfficeSpeakersPCLevel
    attribute: volume  # Invalid attribute
    index: 1
    name: Volume
    device_name: Office Speakers PC
```

#### Missing Required Fields

```yaml
# ❌ Invalid - missing required fields
mqtt:
  base_topic: tesira2mqtt
  server: mqtt.broker.com
  # Missing: port, user, password
```

#### Invalid Data Types

```yaml
# ❌ Invalid - port must be integer
mqtt:
  base_topic: tesira2mqtt
  server: mqtt.broker.com
  port: "1883"  # Should be integer, not string
  user: mqtt_user
  password: mqtt_password
```

## Environment Variable Override

Configuration can be overridden using environment variables:

```bash
# Override configuration file path
export CONFIG=/path/to/config.yaml

# Override log level
export LOGLEVEL=debug

# Run application
python -m src
```

## Configuration Best Practices

### 1. Use Descriptive Names

```yaml
# ✅ Good - descriptive device names
device_name: Office Speakers PC
device_name: Living Room Ceiling Mics

# ❌ Avoid - generic names
device_name: Speaker1
device_name: Mic1
```

### 2. Consistent Instance Tags

```yaml
# ✅ Good - consistent naming pattern
instance_tag: OfficeSpeakersPCLevel
instance_tag: OfficeSpeakersMacLevel
instance_tag: OfficeSpeakersCCALevel

# ❌ Avoid - inconsistent patterns
instance_tag: OfficeSpeakersPCLevel
instance_tag: MacSpeakerLevel
instance_tag: ChromecastAudioLevel
```

### 3. Logical Grouping

```yaml
# ✅ Good - group related attributes
subscriptions:
  # Office Speakers PC
  - instance_tag: OfficeSpeakersPCLevel
    attribute: level
    index: 1
    name: Level
    device_name: Office Speakers PC
  - instance_tag: OfficeSpeakersPCLevel
    attribute: mute
    index: 1
    name: Mute
    device_name: Office Speakers PC

  # Office Speakers Mac
  - instance_tag: OfficeSpeakersMacLevel
    attribute: level
    index: 1
    name: Level
    device_name: Office Speakers Mac
  - instance_tag: OfficeSpeakersMacLevel
    attribute: mute
    index: 1
    name: Mute
    device_name: Office Speakers Mac
```

### 4. Security Considerations

```yaml
# ✅ Good - use environment variables for sensitive data
mqtt:
  base_topic: tesira2mqtt
  server: ${MQTT_SERVER}
  port: ${MQTT_PORT}
  user: ${MQTT_USER}
  password: ${MQTT_PASSWORD}
  keepalive: 60
```

### 5. Documentation

```yaml
# ✅ Good - document your configuration
mqtt:
  base_topic: tesira2mqtt  # Base topic for all MQTT messages
  server: mqtt.broker.com  # MQTT broker hostname
  port: 1883               # MQTT broker port
  user: mqtt_user          # MQTT broker username
  password: mqtt_password  # MQTT broker password
  keepalive: 60            # MQTT keepalive interval (seconds)

tesira:
  host: tesira.device.com  # Tesira device hostname
  port: 23                 # Tesira telnet port
  resubscription_time: 300 # Resubscription interval (seconds)
```

## Advanced Configuration Scenarios

### Multiple Tesira Devices

```yaml
tesira:
  host: tesira-primary.device.com
  port: 23
  resubscription_time: 300

subscriptions:
  # Primary device
  - instance_tag: PrimarySpeakersLevel
    attribute: level
    index: 1
    name: Level
    device_name: Primary Speakers
  - instance_tag: PrimarySpeakersMute
    attribute: mute
    index: 1
    name: Mute
    device_name: Primary Speakers

  # Secondary device (requires separate Tesira2MQTT instance)
  - instance_tag: SecondarySpeakersLevel
    attribute: level
    index: 1
    name: Level
    device_name: Secondary Speakers
```

### Environment Variable Configuration

```bash
# Use environment variables for sensitive data
export MQTT_SERVER="mqtt.broker.com"
export MQTT_USER="mqtt_user"
export MQTT_PASSWORD="secure_password"
export TESIRA_HOST="tesira.device.com"
```

```yaml
mqtt:
  base_topic: tesira2mqtt
  server: ${MQTT_SERVER}
  port: 1883
  user: ${MQTT_USER}
  password: ${MQTT_PASSWORD}
  keepalive: 60

tesira:
  host: ${TESIRA_HOST}
  port: 23
  resubscription_time: 300
```

### Production Configuration

```yaml
mqtt:
  base_topic: tesira2mqtt
  server: mqtt.production.com
  port: 8883
  user: tesira2mqtt_user
  password: ${MQTT_PASSWORD}
  keepalive: 60
  # ca: /etc/ssl/certs/mqtt-ca.pem  # For TLS

tesira:
  host: tesira.production.com
  port: 23
  resubscription_time: 60  # Faster updates for production

subscriptions:
  - instance_tag: ConferenceRoomSpeakersLevel
    attribute: level
    index: 1
    name: Level
    device_name: Conference Room Speakers
  - instance_tag: ConferenceRoomSpeakersMute
    attribute: mute
    index: 1
    name: Mute
    device_name: Conference Room Speakers
  - instance_tag: LobbySpeakersLevel
    attribute: level
    index: 1
    name: Level
    device_name: Lobby Speakers
  - instance_tag: LobbySpeakersMute
    attribute: mute
    index: 1
    name: Mute
    device_name: Lobby Speakers
```

## Troubleshooting Configuration

### Configuration Validation Errors

```bash
# Check configuration syntax
python -c "
import yaml
from models import Config
with open('config.yaml', 'r') as f:
    data = yaml.safe_load(f)
config = Config(**data)
print('Configuration is valid')
"
```

### Common Issues

1. **YAML Syntax Errors**: Use a YAML validator to check syntax
2. **Missing Fields**: Ensure all required fields are present
3. **Invalid Types**: Check that numeric fields are integers, not strings
4. **Duplicate Subscriptions**: Each subscription must be unique
5. **Invalid Instance Tags**: Verify instance tags match Tesira device configuration

---

**Last Updated**: September 2025
**Schema Version**: 1.1.12

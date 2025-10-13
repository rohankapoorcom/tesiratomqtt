# Data Models API Documentation

## Overview

The models module contains Pydantic data models for configuration validation and type safety. These models ensure proper data structure and validation throughout the application.

## Models

### MqttConfig

```python
class MqttConfig(BaseModel):
    """A datamodel representing the MQTT config in config.yaml."""
```

Configuration model for MQTT broker connection settings.

#### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `base_topic` | `str` | Yes | Base MQTT topic prefix for all messages |
| `server` | `str` | Yes | MQTT broker hostname or IP address |
| `port` | `int` | Yes | MQTT broker port number |
| `user` | `str` | Yes | MQTT broker username |
| `password` | `str` | Yes | MQTT broker password |
| `keepalive` | `int` | Yes | MQTT keepalive interval in seconds |

#### Example

```python
from models import MqttConfig

mqtt_config = MqttConfig(
    base_topic="tesira2mqtt",
    server="mqtt.broker.com",
    port=1883,
    user="mqtt_user",
    password="mqtt_password",
    keepalive=60
)
```

#### Validation

- `port`: Must be a valid port number (1-65535)
- `keepalive`: Must be a positive integer
- `server`: Must be a non-empty string
- `user` and `password`: Must be non-empty strings

### TesiraConfig

```python
class TesiraConfig(BaseModel):
    """A datamodel representing the Tesira config in config.yaml."""
```

Configuration model for Biamp Tesira device connection settings.

#### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `host` | `str` | Yes | Tesira device hostname or IP address |
| `port` | `int` | Yes | Tesira telnet port number |
| `resubscription_time` | `int` | Yes | Resubscription interval in seconds |

#### Example

```python
from models import TesiraConfig

tesira_config = TesiraConfig(
    host="tesira.device.com",
    port=23,
    resubscription_time=300
)
```

#### Validation

- `port`: Must be a valid port number (1-65535)
- `resubscription_time`: Must be a positive integer
- `host`: Must be a non-empty string

### Subscription

```python
class Subscription(BaseModel):
    """A datamodel representing the subscription config in config.yaml."""
```

Configuration model for Tesira device attribute subscriptions.

#### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `instance_tag` | `str` | Yes | Tesira device instance tag |
| `attribute` | `Literal["mute", "level"]` | Yes | Attribute type to monitor/control |
| `index` | `int` | Yes | Device index (usually 1) |
| `name` | `str` | Yes | Display name for the attribute |
| `device_name` | `str` | Yes | Device name for grouping |

#### Example

```python
from models import Subscription

subscription = Subscription(
    instance_tag="OfficeSpeakersPCLevel",
    attribute="level",
    index=1,
    name="Level",
    device_name="Office Speakers PC"
)
```

#### Validation

- `attribute`: Must be either "mute" or "level"
- `index`: Must be a positive integer
- `instance_tag`, `name`, `device_name`: Must be non-empty strings

#### Special Methods

The Subscription model implements custom hash and equality methods for use in sets:

```python
def __hash__(self) -> int:
    """Return the hashkey of this object."""

def __eq__(self, other: object) -> bool:
    """Check the equality of this object with another one."""
```

This allows Subscription objects to be used in sets and as dictionary keys.

### Config

```python
class Config(BaseModel):
    """A datamodel representing the config in config.yaml."""
```

Main configuration model that combines all configuration sections.

#### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `mqtt` | `MqttConfig` | Yes | MQTT broker configuration |
| `tesira` | `TesiraConfig` | Yes | Tesira device configuration |
| `subscriptions` | `set[Subscription]` | Yes | Set of device subscriptions |

#### Example

```python
from models import Config, MqttConfig, TesiraConfig, Subscription

config = Config(
    mqtt=MqttConfig(
        base_topic="tesira2mqtt",
        server="mqtt.broker.com",
        port=1883,
        user="mqtt_user",
        password="mqtt_password",
        keepalive=60
    ),
    tesira=TesiraConfig(
        host="tesira.device.com",
        port=23,
        resubscription_time=300
    ),
    subscriptions={
        Subscription(
            instance_tag="OfficeSpeakersPCLevel",
            attribute="level",
            index=1,
            name="Level",
            device_name="Office Speakers PC"
        ),
        Subscription(
            instance_tag="OfficeSpeakersPCLevel",
            attribute="mute",
            index=1,
            name="Mute",
            device_name="Office Speakers PC"
        )
    }
)
```

## Usage Examples

### Loading Configuration from YAML

```python
import yaml
from models import Config

def load_config_from_file(config_path: str) -> Config:
    """Load configuration from YAML file."""
    with open(config_path, 'r') as file:
        config_data = yaml.safe_load(file)

    return Config(**config_data)

# Usage
config = load_config_from_file("config.yaml")
print(f"MQTT Server: {config.mqtt.server}")
print(f"Tesira Host: {config.tesira.host}")
print(f"Subscriptions: {len(config.subscriptions)}")
```

### Validating Configuration

```python
from models import Config
from pydantic import ValidationError

def validate_config(config_data: dict) -> Config:
    """Validate configuration data."""
    try:
        return Config(**config_data)
    except ValidationError as e:
        print(f"Configuration validation failed: {e}")
        raise

# Example with invalid data
invalid_config = {
    "mqtt": {
        "base_topic": "tesira2mqtt",
        "server": "mqtt.broker.com",
        "port": "invalid_port",  # Should be int
        "user": "mqtt_user",
        "password": "mqtt_password",
        "keepalive": 60
    },
    "tesira": {
        "host": "tesira.device.com",
        "port": 23,
        "resubscription_time": 300
    },
    "subscriptions": []
}

try:
    config = validate_config(invalid_config)
except ValidationError as e:
    print("Validation failed:", e)
```

### Working with Subscriptions

```python
from models import Subscription

# Create subscriptions
level_subscription = Subscription(
    instance_tag="OfficeSpeakersPCLevel",
    attribute="level",
    index=1,
    name="Level",
    device_name="Office Speakers PC"
)

mute_subscription = Subscription(
    instance_tag="OfficeSpeakersPCLevel",
    attribute="mute",
    index=1,
    name="Mute",
    device_name="Office Speakers PC"
)

# Use in sets (duplicates are automatically removed)
subscriptions = {level_subscription, mute_subscription}

# Check if subscription exists
if level_subscription in subscriptions:
    print("Level subscription found")

# Iterate over subscriptions
for subscription in subscriptions:
    print(f"Monitoring {subscription.device_name} {subscription.name}")
```

### Configuration Serialization

```python
import yaml
from models import Config

def save_config_to_file(config: Config, config_path: str) -> None:
    """Save configuration to YAML file."""
    config_dict = config.model_dump()

    # Convert sets to lists for YAML serialization
    config_dict['subscriptions'] = list(config_dict['subscriptions'])

    with open(config_path, 'w') as file:
        yaml.dump(config_dict, file, default_flow_style=False)

# Usage
config = Config(...)  # Your config object
save_config_to_file(config, "config.yaml")
```

### Type Hints and IDE Support

```python
from models import Config, Subscription
from typing import List

def process_subscriptions(config: Config) -> List[str]:
    """Process all subscriptions and return device names."""
    device_names = []

    for subscription in config.subscriptions:
        if subscription.attribute == "level":
            device_names.append(f"{subscription.device_name} Level")
        elif subscription.attribute == "mute":
            device_names.append(f"{subscription.device_name} Mute")

    return device_names

# IDE will provide autocomplete and type checking
config = Config(...)
device_names = process_subscriptions(config)
```

## Error Handling

### Validation Errors

```python
from pydantic import ValidationError

try:
    subscription = Subscription(
        instance_tag="OfficeSpeakersPCLevel",
        attribute="invalid_attribute",  # Invalid value
        index=1,
        name="Level",
        device_name="Office Speakers PC"
    )
except ValidationError as e:
    print(f"Validation error: {e}")
    # Output: Validation error: 1 validation error for Subscription
    # attribute
    #   Input should be 'mute' or 'level' [type=literal_error, input_value='invalid_attribute', input_type=str]
```

### Field Validation

```python
from pydantic import Field, ValidationError

class EnhancedSubscription(BaseModel):
    instance_tag: str = Field(..., min_length=1, description="Tesira instance tag")
    attribute: Literal["mute", "level"] = Field(..., description="Attribute type")
    index: int = Field(..., ge=1, description="Device index")
    name: str = Field(..., min_length=1, description="Display name")
    device_name: str = Field(..., min_length=1, description="Device name")

# This will raise ValidationError for invalid index
try:
    subscription = EnhancedSubscription(
        instance_tag="OfficeSpeakersPCLevel",
        attribute="level",
        index=0,  # Invalid: must be >= 1
        name="Level",
        device_name="Office Speakers PC"
    )
except ValidationError as e:
    print(f"Validation error: {e}")
```

## Best Practices

1. **Type Safety**: Always use the models for configuration validation
2. **Error Handling**: Handle ValidationError exceptions appropriately
3. **Serialization**: Use model_dump() for converting to dictionaries
4. **Validation**: Validate configuration early in application startup
5. **Documentation**: Use Field descriptions for better API documentation

---

**Last Updated**: September 2025
**API Version**: 1.1.10

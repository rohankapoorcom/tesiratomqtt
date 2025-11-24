"""Maintains connections to the MQTT Server."""

import json
import logging

import aiomqtt
import slugify

_LOGGER = logging.getLogger(__name__)

AVAILABILITY_TOPIC = "{0}/availability"
MANUFACTURER = "Biamp Systems, LLC"


class MqttConnection:
    """MqttConnection used for communication with MQTT."""

    def __init__(self, client: aiomqtt.Client, base_topic: str) -> None:
        """Initialize an object to manage MQTT communications."""
        self._client = client
        self._base_topic = base_topic
        self._published_names = set()
        self._qos = 2

    async def publish_status(self, status: str = "online") -> None:
        """Indicate that the server is available."""
        await self._client.publish(
            topic=AVAILABILITY_TOPIC.format(self._base_topic),
            payload=json.dumps({"state": status}),
            retain=True,
            qos=self._qos,
        )

    async def publish_state(self, name: str, data: dict, serial: str) -> None:
        """Publish the state of an object."""
        state: str = data["state"]
        identifier: str = data["identifier"]
        topic_name: str = f"{data['device_name']} {name}"
        topic_state: str = f"{self._base_topic}/{identifier}/state"
        _LOGGER.debug(
            "Publishing state %s for %s to %s", state, topic_name, topic_state
        )
        await self._client.publish(
            topic=topic_state, payload=json.dumps(state), retain=True, qos=self._qos
        )

        topic_attributes = f"{self._base_topic}/{identifier}/attributes"
        attributes = json.dumps(data)
        _LOGGER.debug(
            "Publishing attributes %s for %s to %s",
            attributes,
            topic_name,
            topic_attributes,
        )

        await self._client.publish(
            topic=topic_attributes, payload=attributes, retain=True, qos=self._qos
        )

        if identifier not in self._published_names:
            await self.publish_discovery(
                name, data, serial, topic_state, topic_name, identifier
            )
            self._published_names.add(identifier)

    async def publish_discovery(  # noqa: PLR0913
        self,
        name: str,
        data: dict,
        serial: str,
        topic_state: str,
        topic_name: str,
        identifier: str,
    ) -> None:
        """Publish the discovery message for Home Assistant."""
        _LOGGER.info("Publishing discovery info for %s", identifier)
        ha_type = ""
        topic_command = f"{self._base_topic}/{identifier}/set"
        payload = {
            "dev": {
                "ids": f"tesira2mqtt_{data['device_id']}",
                "name": data["device_name"],
                "mf": MANUFACTURER,
                "sn": serial,
            },
            "origin": {"name": "Tesira2MQTT"},
            "availability": [
                {
                    "topic": AVAILABILITY_TOPIC.format(self._base_topic),
                    "value_template": "{{ value_json.state }}",
                }
            ],
            "name": name,
            "state_topic": topic_state,
            "unique_id": data["unique_id"],
            "value_template": "{{ value_json }}",
            "command_topic": topic_command,
        }

        match data["variable_type"]:
            case "bool":
                ha_type = "switch"
                payload["payload_on"] = True
                payload["payload_off"] = False
            case "float":
                ha_type = "number"
                payload.update(
                    {
                        "max": data["max_level"],
                        "min": data["min_level"],
                        "step": 0.1,
                        "unit_of_measurement": "dB",
                    }
                )
            case _:
                # We only support mute / levels currently
                return

        slug = slugify.slugify(topic_name, separator="_")
        payload["default_entity_id"] = f"{ha_type}.{slug}"

        await self._client.subscribe(topic_command)

        topic_config = f"homeassistant/{ha_type}/{data['unique_id']}/config"
        await self._client.publish(
            topic=topic_config, payload=json.dumps(payload), retain=True, qos=self._qos
        )
        _LOGGER.debug("Published discovery info for %s to %s", identifier, topic_config)

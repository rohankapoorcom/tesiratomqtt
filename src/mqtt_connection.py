"""Maintains the connection to the MQTT broker."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import aiomqtt
import slugify

if TYPE_CHECKING:
    from models import MqttConfig

_LOGGER = logging.getLogger(__name__)

AVAILABILITY_TOPIC = "{0}/availability"
MANUFACTURER = "Biamp Systems, LLC"

_RECONNECT_BACKOFF_INITIAL = 1.0
_RECONNECT_BACKOFF_MAX = 60.0

CommandHandler = Callable[[str, str], Awaitable[None]]


class MqttConnection:
    """
    Supervised MQTT connection.

    Keeps the latest entry for every subscription so that after a broker
    reconnect availability, discovery, command subscriptions and state can all
    be republished. ``publish_state`` never raises: while disconnected the
    entry is stored and flushed on reconnect.
    """

    def __init__(self, config: MqttConfig) -> None:
        """Initialise; no connection is made until ``run()``."""
        self._config = config
        self._base_topic = config.base_topic
        self._qos = 2
        self._client: aiomqtt.Client | None = None
        self._entries: dict[str, tuple[str, dict[str, Any], str]] = {}
        self._announced: set[str] = set()
        self._connected = asyncio.Event()
        self._stop = asyncio.Event()

    @property
    def connected(self) -> bool:
        """Return whether the broker is currently connected."""
        return self._client is not None

    async def wait_connected(self) -> None:
        """Wait until the broker is connected."""
        await self._connected.wait()

    def _make_client(self) -> aiomqtt.Client:
        return aiomqtt.Client(
            hostname=self._config.server,
            port=self._config.port,
            username=self._config.user,
            password=self._config.password,
            keepalive=self._config.keepalive,
            identifier=self._config.client_id,
            will=aiomqtt.Will(
                topic=AVAILABILITY_TOPIC.format(self._base_topic),
                payload=json.dumps({"state": "offline"}),
                retain=True,
            ),
        )

    async def run(self, on_command: CommandHandler) -> None:
        """Stay connected until ``close()``; reconnect with backoff on loss."""
        _LOGGER.info("Starting MQTT supervisor loop")
        self._stop.clear()
        backoff = _RECONNECT_BACKOFF_INITIAL
        while not self._stop.is_set():
            try:
                async with self._make_client() as client:
                    try:
                        await self._on_connect(client)
                        backoff = _RECONNECT_BACKOFF_INITIAL
                        await self._serve(client, on_command)
                    finally:
                        self._client = None
                        self._connected.clear()
            except aiomqtt.MqttError as err:
                if self._stop.is_set():
                    break
                _LOGGER.warning(
                    "MQTT connection lost (%s); retrying in %.0f seconds", err, backoff
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _RECONNECT_BACKOFF_MAX)

    async def _serve(self, client: aiomqtt.Client, on_command: CommandHandler) -> None:
        """Handle incoming messages until the connection drops or ``close()``."""
        reader = asyncio.create_task(self._read_messages(client, on_command))
        stopper = asyncio.create_task(self._stop.wait())
        try:
            done, _ = await asyncio.wait(
                {reader, stopper}, return_when=asyncio.FIRST_COMPLETED
            )
            if reader in done:
                reader.result()
        finally:
            for task in (reader, stopper):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def _read_messages(
        self, client: aiomqtt.Client, on_command: CommandHandler
    ) -> None:
        async for message in client.messages:
            await self._handle_message(message, on_command)

    async def close(self) -> None:
        """Publish ``offline`` and stop ``run()``."""
        if self._client is not None:
            with contextlib.suppress(aiomqtt.MqttError, TimeoutError):
                await asyncio.wait_for(self.publish_status("offline"), timeout=5.0)
        self._stop.set()

    async def _on_connect(self, client: aiomqtt.Client) -> None:
        _LOGGER.info(
            "Connected to MQTT at %s:%s", self._config.server, self._config.port
        )
        self._client = client
        self._announced.clear()
        await self.publish_status("online")
        for name, data, serial in list(self._entries.values()):
            await self._publish_entry(name, data, serial)
        self._connected.set()
        _LOGGER.info("Republished %d entities to MQTT", len(self._entries))

    async def _handle_message(
        self, message: aiomqtt.Message, on_command: CommandHandler
    ) -> None:
        payload = (
            message.payload.decode("utf-8")
            if isinstance(message.payload, bytes)
            else str(message.payload)
        )
        _LOGGER.debug("%s - Received MQTT message: %s", message.topic, payload)
        parts = message.topic.value.split("/")
        if len(parts) != 3 or parts[2] != "set":  # noqa: PLR2004
            _LOGGER.warning("Ignoring message on unexpected topic %s", message.topic)
            return
        try:
            await on_command(parts[1], payload)
        except Exception:
            _LOGGER.exception("Failed to apply %s to %s", payload, parts[1])

    async def publish_status(self, status: str = "online") -> None:
        """Publish the availability topic."""
        client = self._client
        if client is None:
            msg = "MQTT not connected"
            raise aiomqtt.MqttError(msg)
        await client.publish(
            topic=AVAILABILITY_TOPIC.format(self._base_topic),
            payload=json.dumps({"state": status}),
            retain=True,
            qos=self._qos,
        )

    async def publish_state(self, name: str, data: dict, serial: str) -> None:
        """Store the latest state and publish it if connected."""
        identifier: str = data["identifier"]
        self._entries[identifier] = (name, data, serial)
        if self._client is None:
            _LOGGER.debug("MQTT disconnected; deferring state for %s", identifier)
            return
        try:
            await self._publish_entry(name, data, serial)
        except aiomqtt.MqttError as err:
            _LOGGER.warning("Failed to publish %s (%s); will retry", identifier, err)

    async def _publish_entry(self, name: str, data: dict, serial: str) -> None:
        client = self._client
        if client is None:
            return
        identifier: str = data["identifier"]
        topic_name = f"{data['device_name']} {name}"
        topic_state = f"{self._base_topic}/{identifier}/state"
        topic_attributes = f"{self._base_topic}/{identifier}/attributes"

        if identifier not in self._announced:
            await self._publish_discovery(
                client, name, data, serial, topic_state, topic_name
            )
            self._announced.add(identifier)

        _LOGGER.debug(
            "Publishing %s = %s to %s", topic_name, data["state"], topic_state
        )
        await client.publish(
            topic=topic_state,
            payload=json.dumps(data["state"]),
            retain=True,
            qos=self._qos,
        )
        await client.publish(
            topic=topic_attributes, payload=json.dumps(data), retain=True, qos=self._qos
        )

    async def _publish_discovery(  # noqa: PLR0913
        self,
        client: aiomqtt.Client,
        name: str,
        data: dict,
        serial: str,
        topic_state: str,
        topic_name: str,
    ) -> None:
        """Subscribe to the command topic and publish the discovery message."""
        identifier: str = data["identifier"]
        topic_command = f"{self._base_topic}/{identifier}/set"
        await client.subscribe(topic_command)

        payload: dict[str, Any] = {
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
                return

        slug = slugify.slugify(topic_name, separator="_")
        payload["default_entity_id"] = f"{ha_type}.{slug}"

        topic_config = f"homeassistant/{ha_type}/{data['unique_id']}/config"
        _LOGGER.info("Publishing discovery info for %s", identifier)
        await client.publish(
            topic=topic_config, payload=json.dumps(payload), retain=True, qos=self._qos
        )

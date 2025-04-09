"""Maintains multiple connections to the Biamp Tesira device."""

import asyncio
import logging

import telnetlib3
from pydantic import TypeAdapter

from errors import (
    ClientConnectionError,
    ClientError,
    ClientResponseError,
    ClientTimeoutError,
)
from models import Subscription, TesiraConfig
from mqtt_connection import MqttConnection
from telnet import BiampTesiraTelnetConnection

_LOGGER = logging.getLogger(__name__)


class BiampTesiraConnection:
    """BiampTesiraConnection used for communication with Biamp Tesira DSPs."""

    def __init__(self, tesira: TesiraConfig, mqtt: MqttConnection) -> None:
        """Initiate a Biamp Tesira connection."""
        self._tesira: TesiraConfig = tesira
        self._mqtt: MqttConnection = mqtt
        self._timeout: int = 10
        self._semaphore = asyncio.Semaphore(1)
        self._subscription_telnet: BiampTesiraTelnetConnection | None = None
        self._command_telnet: BiampTesiraTelnetConnection | None = None
        self._serial_number: str | None = None
        self._subscriptions: dict = {}

    async def open(self) -> None:
        """Open the telnet clients for communication."""
        msg = f"Connecting to Tesira at {self._tesira.host}:{self._tesira.port}"
        _LOGGER.info(msg)
        if (
            self._subscription_telnet is None
            or self._subscription_telnet.writer is None
            or self._subscription_telnet.writer.is_closing()
        ):
            self._subscription_telnet = await self._async_create_telnet_client(
                "subscription_telnet"
            )
        if (
            self._command_telnet is None
            or self._command_telnet.writer is None
            or self._command_telnet.writer.is_closing()
        ):
            self._command_telnet = await self._async_create_telnet_client(
                "command_telnet"
            )

        self._serial_number = await self.command("DEVICE get serialNumber")
        msg = f"Connected to Tesira at {self._tesira.host}:{self._tesira.port}"
        _LOGGER.info(msg)

    async def _async_create_telnet_client(
        self, identifier: str
    ) -> BiampTesiraTelnetConnection:
        """Create a telnet client for communication."""
        try:
            connection = BiampTesiraTelnetConnection(
                *await asyncio.wait_for(
                    telnetlib3.open_connection(
                        self._tesira.host,
                        self._tesira.port,
                        encoding="utf8",
                        cols=1310,
                        rows=125,
                    ),
                    timeout=self._timeout,
                ),
                identifier=identifier,
            )

            await connection.readuntil(
                "Welcome to the Tesira Text Protocol Server...\r\n", self._timeout
            )
            msg = f"Successfully connected to {self._tesira.host}:{self._tesira.port}"
            _LOGGER.debug(msg)

        except TimeoutError as error:
            msg = f"Timeout connecting to {self._tesira.host}:{self._tesira.port}"
            raise ClientTimeoutError(msg) from error

        except OSError as error:
            msg = f"Failed to connect to {self._tesira.host}:{self._tesira.port}"
            raise ClientConnectionError(msg) from error
        else:
            return connection

    async def subscribe_all(self, subscriptions: set[Subscription]) -> None:
        """Create all of the Tesira subscriptions."""
        _LOGGER.info("Subscribing to Tesira")
        for subscription in subscriptions:
            await self.subscribe(subscription)
            await asyncio.sleep(1)
        _LOGGER.info("Tesira Subscriptions created successfully")

    async def subscribe(self, subscription: Subscription) -> None:
        """Create a single Tesira subscription."""
        if self._subscription_telnet is None or self._subscription_telnet.closed:
            msg = "Client not connected."
            raise ClientConnectionError(msg)

        _LOGGER.debug("Creating subscription for %s", subscription)

        identifier = (
            f"{subscription.instance_tag}_{subscription.attribute}_{subscription.index}"
        )
        command = f"{subscription.instance_tag} subscribe {subscription.attribute} {subscription.index} {identifier}"  # noqa: E501
        first_response = (
            await self._write(
                command,
                self._subscription_telnet,
            )
        ).strip()

        if not first_response or first_response in ("\r\n"):
            first_response = (
                await self._subscription_telnet.readline(self._timeout)
            ).strip()

        if "-ERR ALREADY_SUBSCRIBED" in first_response:
            return

        if "-ERR" in first_response:
            raise ClientResponseError(first_response)

        if identifier in self._subscriptions:
            while first_response.startswith('! "publishToken":'):
                await self.process_tesira_response(first_response)
                first_response = (
                    await self._subscription_telnet.readline(self._timeout)
                ).strip()

        second_response = (
            await self._subscription_telnet.readline(self._timeout)
        ).strip()

        if identifier in self._subscriptions:
            while second_response.startswith('! "publishToken":'):
                await self.process_tesira_response(first_response)
                first_response = (
                    await self._subscription_telnet.readline(self._timeout)
                ).strip()

        if second_response == "+OK":
            original_value: str = first_response.split(" ")[2].split(":")[1]
            other_items = {}

            # Eliminate the blank line before continuing onwards
            await self.process_tesira_response(
                await self._subscription_telnet.readline(self._timeout)
            )

            match subscription.attribute:
                case "mute":
                    variable_type = "bool"
                case "level":
                    variable_type = "float"
                    other_items = await self.get_min_max_levels(subscription)
                case _:
                    variable_type = "str"

            unique_id = f"{self._serial_number}_{subscription.instance_tag}_{subscription.attribute}_{subscription.index}"  # noqa: E501
            data = {
                "instance_tag": subscription.instance_tag,
                "attribute": subscription.attribute,
                "index": subscription.index,
                "state": TypeAdapter(variable_type).validate_python(original_value),
                "variable_type": variable_type,
                "device_id": f"{self._serial_number}_{subscription.instance_tag}",
                "unique_id": unique_id,
                "name": subscription.name,
                "device_name": subscription.device_name,
                "identifier": identifier,
                **other_items,
            }
            self._subscriptions[identifier] = data
            await self._mqtt.publish_state(subscription.name, data, self._serial_number)
        else:
            # Eliminate the blank line before continuing onwards
            await self.process_tesira_response(
                await self._subscription_telnet.readline(self._timeout)
            )

    async def get_min_max_levels(self, subscription: Subscription) -> dict[str, float]:
        """Get the min and max levels of a level control block."""
        min_level = 0.0
        max_level = 0.0

        if self._subscription_telnet is None or self._subscription_telnet.closed:
            msg = "Client not connected."
            raise ClientConnectionError(msg)

        response = (
            await self._write(
                f"{subscription.instance_tag} get minLevel {subscription.index}",
                self._subscription_telnet,
            )
        ).strip()

        if "+OK" in response:
            min_level = TypeAdapter(float).validate_python(
                response.split(" ")[1].split(":")[1]
            )
        elif response.startswith('! "publishToken":'):
            await self.process_tesira_response(response)
        else:
            raise ClientResponseError(response)

        # Eliminate the blank line before continuing onwards
        await self.process_tesira_response(
            await self._subscription_telnet.readline(self._timeout)
        )

        response = (
            await self._write(
                f"{subscription.instance_tag} get maxLevel {subscription.index}",
                self._subscription_telnet,
            )
        ).strip()

        if "+OK" in response:
            max_level = TypeAdapter(float).validate_python(
                response.split(" ")[1].split(":")[1]
            )
        elif response.startswith('! "publishToken":'):
            await self.process_tesira_response(response)
        else:
            raise ClientResponseError(response)

        # Eliminate the blank line before continuing onwards
        await self.process_tesira_response(
            await self._subscription_telnet.readline(self._timeout)
        )

        return {"min_level": min_level, "max_level": max_level}

    async def update_state_and_command(self, key: str, value: str) -> None:
        """Update the state on the Tesira DSP for the specified key to the value."""
        if not self._subscriptions.get(key):
            msg = f"Key: {key} does not match any subscriptions"
            raise ClientError(msg)

        await self.command(
            f"{self._subscriptions[key]['instance_tag']} set {self._subscriptions[key]['attribute']} {self._subscriptions[key]['index']} {value}"  # noqa: E501
        )

    async def command(self, command: str) -> str | None:
        """Send a command and return the response."""
        response = (await self._write(command, self._command_telnet)).strip()
        if "-ERR" in response:
            raise ClientResponseError(response)
        if response == "+OK":
            return None
        if "value" in response:
            # Remove the +OK "value": from the beginning of the string and unquote it
            return response[13:-1]
        return response

    async def listen_to_incoming_messages(self, barrier: asyncio.Barrier) -> None:
        """Receive incoming messages from the Tesira and process."""
        if self._subscription_telnet is None or self._subscription_telnet.closed:
            msg = "Client not connected."
            raise ClientConnectionError(msg)

        _LOGGER.info("Starting Tesira subscription telnet reading loop")
        await barrier.wait()
        while True:
            try:
                response = None
                async with self._semaphore:
                    response = await self._subscription_telnet.readline(self._timeout)

                await self.process_tesira_response(response)
            except ClientTimeoutError:
                continue

    async def process_tesira_response(self, response: str) -> None:
        """Process the response to a command."""
        if not response or response in ("\r\n", "+OK"):
            return
        if not response.startswith('! "publishToken":'):
            return
        tokens = response.split(" ")
        key = tokens[1].split(":")[1][1:-1]
        self._subscriptions[key]["state"] = TypeAdapter(
            self._subscriptions[key]["variable_type"]
        ).validate_python(tokens[2].split(":")[1])

        await self._mqtt.publish_state(
            self._subscriptions[key]["name"],
            self._subscriptions[key],
            self._serial_number,
        )

    async def automatically_subscribe_on_schedule(
        self, barrier: asyncio.Barrier, subscriptions: set[Subscription]
    ) -> None:
        """Rerun the subscription process automatically every minute."""
        _LOGGER.info("Starting Tesira subscription setting loop")
        await barrier.wait()
        while True:
            await asyncio.sleep(self._tesira.resubscription_time)
            _LOGGER.debug("Resubscribing to all subscriptions")
            async with self._semaphore:
                await self.subscribe_all(subscriptions)

    async def _write(
        self, command: str, telnet: BiampTesiraTelnetConnection | None
    ) -> str:
        """Send a command and return the response."""
        if telnet is None or telnet.closed:
            msg = "Client not connected."
            raise ClientConnectionError(msg)
        await telnet.write(command)
        await asyncio.sleep(1)
        response = await telnet.readline(self._timeout)
        if command not in response:
            return response
        return await telnet.readline(self._timeout)

    async def close(self) -> None:
        """Close all telnet connections."""
        if self._subscription_telnet is not None:
            self._subscription_telnet.close()
        if self._command_telnet is not None:
            self._command_telnet.close()

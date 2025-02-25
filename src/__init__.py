"""The main class, establishes workers and sets up bidirectional communciation."""

import argparse
import asyncio
import json
import logging
import signal
from signal import SIGINT

import aiomqtt
import yaml

from models import Config, Subscription, TesiraConfig
from mqtt_connection import AVAILABILITY_TOPIC, MqttConnection
from tesira import BiampTesiraConnection
from utils.arguments import EnvDefault

_LOGGER = logging.getLogger(__name__)


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments and environmental variables."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-c",
        "--config",
        action=EnvDefault,
        envvar="CONFIG",
        help=(
            "Specify the complete path to the config file "
            "(can also be provided by using the environmental variable CONFIG)."
        ),
    )
    return parser.parse_args()


def load_config(config_path: str) -> Config:
    """Load the configuration file to a dictionary."""
    with open(config_path) as f:
        return Config(**yaml.safe_load(f))


async def establish_tesira_connection(
    tesira: TesiraConfig, subscriptions: set[Subscription], mqtt: MqttConnection
) -> BiampTesiraConnection:
    """Establish a connection to the Tesira device and create all subscriptions."""
    connection = BiampTesiraConnection(tesira, mqtt)
    await connection.open()
    await connection.subscribe_all(subscriptions)
    return connection


async def handle_exit(
    mqtt: MqttConnection, tesira: BiampTesiraConnection, tasks: list[asyncio.Task]
) -> None:
    _LOGGER.info("Exiting gracefully")
    await mqtt.publish_status("offline")
    for task in tasks:
        task.cancel()
    await tesira.close()


async def listen_to_incoming_mqtt_messages(
    mqtt_client: aiomqtt.Client, tesira_connection: BiampTesiraConnection
) -> None:
    _LOGGER.debug("Starting MQTT subscription reading loop")
    async for message in mqtt_client.messages:
        key = message.topic.value.split("/")[1]
        await tesira_connection.update_state_and_command(key, message.payload.decode())


async def async_main() -> None:
    mqtt_client = aiomqtt.Client(
        hostname=config.mqtt.server,
        port=config.mqtt.port,
        username=config.mqtt.user,
        password=config.mqtt.password,
        keepalive=config.mqtt.keepalive,
        will=aiomqtt.Will(
            topic=AVAILABILITY_TOPIC.format(config.mqtt.base_topic),
            payload=json.dumps({"state": "offline"}),
            retain=True,
        ),
    )

    async with mqtt_client:  # noqa: SIM117
        async with asyncio.TaskGroup() as tg:
            mqtt_connection = MqttConnection(mqtt_client, config.mqtt.base_topic)
            await mqtt_connection.publish_status()
            tesira_connection = await establish_tesira_connection(
                config.tesira, config.subscriptions, mqtt_connection
            )

            tasks = []
            tasks.append(
                tg.create_task(tesira_connection.listen_to_incoming_messages())
            )

            tasks.append(
                tg.create_task(
                    listen_to_incoming_mqtt_messages(mqtt_client, tesira_connection)
                )
            )

            for signame in ("SIGINT", "SIGTERM"):
                asyncio.get_running_loop().add_signal_handler(
                    getattr(signal, signame),
                    lambda: asyncio.create_task(
                        handle_exit(mqtt_connection, tesira_connection, tasks)
                    ),
                )

    for signame in ("SIGINT", "SIGTERM"):
        asyncio.get_running_loop().remove_signal_handler(getattr(signal, signame))


if __name__ == "__main__":
    args = parse_arguments()
    config = load_config(args.config)

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(levelname)s - %(message)s",
        force=True,
    )
    asyncio.run(async_main())

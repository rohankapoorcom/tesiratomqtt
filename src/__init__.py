"""The main class, establishes workers and sets up bidirectional communciation."""

import argparse
import asyncio
import json
import logging
import signal
import sys
from pathlib import Path

import aiomqtt
import yaml

from _version import __version__
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
    parser.add_argument(
        "-l",
        "--loglevel",
        action=EnvDefault,
        envvar="LOGLEVEL",
        default="info",
        choices=logging._nameToLevel.keys(),  # noqa: SLF001
        help="Specify the logging level that should be used; default=info",
    )
    return parser.parse_args()


def load_config(config_file: str) -> Config:
    """Load the configuration file to a dictionary."""
    config_path = Path(config_file)
    if not config_path.exists() and not config_path.is_file():
        sys.exit(f"Config file: {config_file} does not exist")
    with Path(config_path).open() as f:
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
    """Gracefully exit by cancelling all long-running tasks."""
    _LOGGER.info("Exiting gracefully")
    await mqtt.publish_status("offline")
    for task in tasks:
        task.cancel()
    await tesira.close()


async def listen_to_incoming_mqtt_messages(
    barrier: asyncio.Barrier,
    mqtt_client: aiomqtt.Client,
    tesira_connection: BiampTesiraConnection,
) -> None:
    """Infinitely process incoming MQTT messages."""
    _LOGGER.info("Starting MQTT subscription reading loop")
    await barrier.wait()
    async for message in mqtt_client.messages:
        decoded_payload: str = message.payload.decode()  # type: ignore  # noqa: PGH003
        _LOGGER.debug("%s - Received MQTT message: %s", message.topic, decoded_payload)
        key = message.topic.value.split("/")[1]
        await tesira_connection.update_state_and_command(key, decoded_payload)


async def async_main() -> None:
    """Run Tesira2MQTT."""
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

            barrier = asyncio.Barrier(4)
            tasks = []
            tasks.append(
                tg.create_task(tesira_connection.listen_to_incoming_messages(barrier))
            )

            tasks.append(
                tg.create_task(
                    listen_to_incoming_mqtt_messages(
                        barrier, mqtt_client, tesira_connection
                    )
                )
            )

            tasks.append(
                tg.create_task(
                    tesira_connection.automatically_subscribe_on_schedule(
                        barrier, config.subscriptions
                    )
                )
            )

            for signame in ("SIGINT", "SIGTERM"):
                asyncio.get_running_loop().add_signal_handler(
                    getattr(signal, signame),
                    lambda: asyncio.create_task(
                        handle_exit(mqtt_connection, tesira_connection, tasks)
                    ),
                )

            await barrier.wait()
            _LOGGER.info("Tesira2MQTT is ready")

    for signame in ("SIGINT", "SIGTERM"):
        asyncio.get_running_loop().remove_signal_handler(getattr(signal, signame))


if __name__ == "__main__":
    args = parse_arguments()
    logging.basicConfig(
        level=args.loglevel.upper(),
        format="%(asctime)s - %(levelname)s - %(message)s",
        force=True,
    )
    _LOGGER.info("Tesira2MQTT version %s", __version__)
    config = load_config(args.config)
    asyncio.run(async_main())

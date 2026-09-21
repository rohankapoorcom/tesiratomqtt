"""The main class, establishes workers and sets up bidirectional communciation."""

import argparse
import asyncio
import logging
import signal
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import yaml

from _version import __version__
from errors import ClientError
from models import Config
from mqtt_connection import MqttConnection
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


async def supervise(name: str, start: Callable[[], Awaitable[None]]) -> None:
    """Restart ``start()`` if it ever raises; only cancellation stops it."""
    while True:
        try:
            await start()
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("%s loop crashed; restarting in %.0f seconds", name, 5.0)
            await asyncio.sleep(5.0)
        else:
            return


async def async_main(config: Config) -> None:
    """Run Tesira2MQTT until SIGINT/SIGTERM."""
    mqtt = MqttConnection(config.mqtt)
    tesira = BiampTesiraConnection(config.tesira, mqtt)

    async def on_command(key: str, value: str) -> None:
        try:
            await tesira.update_state_and_command(key, value)
        except ClientError as err:
            _LOGGER.warning("Failed to apply %s to %s: %s", value, key, err)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signame in ("SIGINT", "SIGTERM"):
        loop.add_signal_handler(getattr(signal, signame), stop.set)

    tasks = [
        asyncio.create_task(
            supervise("Tesira", lambda: tesira.run(config.subscriptions)),
            name="tesira-supervisor",
        ),
        asyncio.create_task(
            supervise("MQTT", lambda: mqtt.run(on_command)), name="mqtt-supervisor"
        ),
    ]
    _LOGGER.info("Tesira2MQTT started")

    try:
        await stop.wait()
    finally:
        _LOGGER.info("Exiting gracefully")
        await mqtt.close()
        await tesira.close()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for signame in ("SIGINT", "SIGTERM"):
            loop.remove_signal_handler(getattr(signal, signame))


if __name__ == "__main__":
    args = parse_arguments()
    logging.basicConfig(
        level=args.loglevel.upper(),
        format="%(asctime)s - %(levelname)s - %(message)s",
        force=True,
    )
    _LOGGER.info("Tesira2MQTT version %s", __version__)
    asyncio.run(async_main(load_config(args.config)))

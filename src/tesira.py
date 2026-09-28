"""Maintains multiple connections to the Biamp Tesira device."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter, ValidationError

from errors import (
    ClientConnectionError,
    ClientError,
    ClientResponseError,
    ClientTimeoutError,
)
from models import subscription_identifier
from telnet import BiampTesiraTelnetConnection

if TYPE_CHECKING:
    from models import Subscription, TesiraConfig
    from mqtt_connection import MqttConnection

_LOGGER = logging.getLogger(__name__)

# The "+OK" of a subscribe command is sometimes folded onto the update line.
_PUBLISH_TOKEN_RE = re.compile(
    r'^! "publishToken":"(?P<token>[^"]*)" "value":(?P<value>.*?)(?P<ok> \+OK)?$'
)
_OK_RE = re.compile(r'^\+OK(?: "value":(?P<value>.*))?$')
# Any "-..." line is a failure (-ERR, -CANNOT_DELIVER, -GENERAL_FAILURE, ...).
_ERR_PREFIX = "-"
_ALREADY_SUBSCRIBED = "ALREADY_SUBSCRIBED"

# Used in MQTT topics and Home Assistant object ids, which only allow these.
_SERIAL_RE = re.compile(r"^[A-Za-z0-9_-]+$")

_TYPE_ADAPTERS: dict[str, TypeAdapter] = {
    "bool": TypeAdapter(bool),
    "float": TypeAdapter(float),
    "str": TypeAdapter(str),
}

_RECONNECT_BACKOFF_INITIAL = 1.0
_RECONNECT_BACKOFF_MAX = 60.0
_PUBLISH_RETRY = 0.5


@dataclass
class _Channel:
    """A telnet session and its in-flight command."""

    name: str
    telnet: BiampTesiraTelnetConnection | None = None
    reader_task: asyncio.Task | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: asyncio.Future[str] | None = None
    pending_command: str | None = None

    @property
    def connected(self) -> bool:
        """Return whether the session is usable."""
        return self.telnet is not None and not self.telnet.closed

    def fail_pending(self, error: Exception) -> None:
        """Fail the in-flight command, if any."""
        if self.pending is not None and not self.pending.done():
            self.pending.set_exception(error)

    def resolve_pending(self, response: str) -> bool:
        """Resolve the in-flight command; return False if there was none."""
        if self.pending is not None and not self.pending.done():
            self.pending.set_result(response)
            return True
        return False


def _quote_tag(tag: str) -> str:
    """Quote an instance tag when it contains whitespace."""
    if any(char.isspace() for char in tag):
        return f'"{tag}"'
    return tag


def _subscription_identifier(subscription: Subscription) -> str:
    """MQTT and publishToken id for a subscription."""
    return subscription_identifier(
        subscription.instance_tag, subscription.attribute, subscription.index
    )


def _format_level(number: float, minimum: float, maximum: float) -> str:
    """Format a clamped level without rounding it back outside the bounds."""
    text = f"{number:.2f}"
    if minimum <= float(text) <= maximum:
        return text
    # repr() is the shortest string that parses back to exactly ``number``;
    # Decimal writes it without an exponent.
    return format(Decimal(repr(number)), "f")


def _command_value(entry: dict[str, Any], value: str) -> str:
    """Normalize an MQTT payload into a single TTP set argument."""
    kind = entry["variable_type"]
    if kind == "bool":
        normalized = value.strip().lower()
        if normalized not in {"true", "false"}:
            msg = f"Invalid mute value: {value!r}"
            raise ClientResponseError(msg)
        return normalized
    if kind == "float":
        try:
            number = float(value)
        except ValueError as err:
            msg = f"Invalid level value: {value!r}"
            raise ClientResponseError(msg) from err
        if not math.isfinite(number):
            msg = f"Invalid level value: {value!r}"
            raise ClientResponseError(msg)
        minimum = float(entry["min_level"])
        maximum = float(entry["max_level"])
        number = min(max(number, minimum), maximum)
        return _format_level(number, minimum, maximum)
    msg = f"Unsupported value for {entry['attribute']}: {value!r}"
    raise ClientResponseError(msg)


def _unquote(value: str) -> str:
    """Strip surrounding double quotes."""
    if len(value) >= 2 and value[0] == value[-1] == '"':  # noqa: PLR2004
        return value[1:-1]
    return value


class BiampTesiraConnection:
    """
    Communication with a Biamp Tesira DSP over two telnet sessions.

    One session carries subscriptions (and their ``publishToken`` updates), the
    other carries commands. A reader task per session routes updates to the state
    store, ``+OK`` and any line starting with ``-`` to the pending command, and
    ignores the rest (the Tesira echoes every command back).
    """

    def __init__(self, tesira: TesiraConfig, mqtt: MqttConnection) -> None:
        """Initiate a Biamp Tesira connection."""
        self._tesira: TesiraConfig = tesira
        self._mqtt: MqttConnection = mqtt
        self._subscription_channel = _Channel("subscription_telnet")
        self._command_channel = _Channel("command_telnet")
        self._serial_number: str | None = None
        self._subscriptions: dict[str, dict[str, Any]] = {}
        self._connection_lost = asyncio.Event()
        self._heartbeat_task: asyncio.Task | None = None
        self._publisher_task: asyncio.Task | None = None
        self._dirty: set[str] = set()
        self._publish_event = asyncio.Event()
        self._closing = False

    @property
    def serial_number(self) -> str | None:
        """Return the Tesira serial number once connected."""
        return self._serial_number

    @property
    def connected(self) -> bool:
        """Return whether both sessions are usable."""
        return (
            self._subscription_channel.connected
            and self._command_channel.connected
            and not self._connection_lost.is_set()
        )

    async def open(self) -> None:
        """Open both sessions and read the serial number."""
        _LOGGER.info(
            "Connecting to Tesira at %s:%s", self._tesira.host, self._tesira.port
        )
        await self._teardown()
        self._connection_lost.clear()

        channels = (self._subscription_channel, self._command_channel)
        results = await asyncio.gather(
            *(
                BiampTesiraTelnetConnection.connect(
                    self._tesira.host,
                    self._tesira.port,
                    channel.name,
                    self._tesira.command_timeout,
                )
                for channel in channels
            ),
            return_exceptions=True,
        )
        errors = [result for result in results if isinstance(result, BaseException)]
        if errors:
            for result in results:
                if isinstance(result, BiampTesiraTelnetConnection):
                    result.close()
            raise errors[0]

        for channel, telnet in zip(channels, results, strict=True):
            channel.telnet = telnet
            channel.reader_task = asyncio.create_task(
                self._reader_loop(channel), name=f"tesira-reader-{channel.name}"
            )

        try:
            serial_number = await self.command("DEVICE get serialNumber")
        except ClientError:
            await self._teardown()
            raise

        if not serial_number or not _SERIAL_RE.match(serial_number):
            await self._teardown()
            msg = (
                f"Unexpected serial number {serial_number!r} from Tesira at "
                f"{self._tesira.host}:{self._tesira.port}"
            )
            raise ClientResponseError(msg)
        self._serial_number = serial_number
        self._publisher_task = asyncio.create_task(
            self._publisher_loop(), name="tesira-publisher"
        )

        if self._tesira.heartbeat_interval > 0:
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(), name="tesira-heartbeat"
            )

        _LOGGER.info(
            "Connected to Tesira %s at %s:%s",
            self._serial_number,
            self._tesira.host,
            self._tesira.port,
        )

    async def close(self) -> None:
        """Close the sessions and stop background tasks."""
        self._closing = True
        await self._teardown()

    async def wait_closed(self) -> None:
        """Wait until the connection is lost or closed."""
        await self._connection_lost.wait()

    async def _teardown(self) -> None:
        """Stop tasks, close sessions and fail in-flight commands."""
        # Set first so the cancelled readers do not log a "lost" connection.
        self._connection_lost.set()

        for attr in ("_heartbeat_task", "_publisher_task"):
            task: asyncio.Task | None = getattr(self, attr)
            setattr(self, attr, None)
            if task is not None and task is not asyncio.current_task():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        for channel in (self._subscription_channel, self._command_channel):
            task, channel.reader_task = channel.reader_task, None
            if task is not None and task is not asyncio.current_task():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if channel.telnet is not None:
                channel.telnet.close()
                _LOGGER.debug("%s - connection closed", channel.name)
                channel.telnet = None
            channel.fail_pending(ClientConnectionError("Client not connected."))

        # The device may have changed while the session was down, so values
        # from it that were never published are dropped rather than sent late.
        # Resubscribing fetches and publishes the current ones.
        self._dirty.clear()
        for entry in self._subscriptions.values():
            entry["state"] = None

    def _on_connection_lost(self, channel: _Channel, reason: str) -> None:
        """Mark the connection lost and fail in-flight commands."""
        if not self._connection_lost.is_set():
            _LOGGER.warning("%s - Tesira connection lost: %s", channel.name, reason)
        error = ClientConnectionError(f"{channel.name} - connection lost: {reason}")
        self._subscription_channel.fail_pending(error)
        self._command_channel.fail_pending(error)
        self._connection_lost.set()

    async def run(self, subscriptions: set[Subscription]) -> None:
        """Reconnect and resubscribe on loss; refresh subscriptions on schedule."""
        _LOGGER.info("Starting Tesira supervisor loop")
        self._closing = False
        backoff = _RECONNECT_BACKOFF_INITIAL
        while not self._closing:
            if not self.connected:
                try:
                    await self.open()
                    await self.subscribe_all(subscriptions)
                except ClientError as err:
                    _LOGGER.warning(
                        "Tesira unavailable (%s); retrying in %.0f seconds",
                        err,
                        backoff,
                    )
                    await self._teardown()
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, _RECONNECT_BACKOFF_MAX)
                    continue
                backoff = _RECONNECT_BACKOFF_INITIAL

            try:
                await asyncio.wait_for(
                    self._connection_lost.wait(), self._tesira.resubscription_time
                )
            except TimeoutError:
                _LOGGER.debug("Resubscribing to all subscriptions")
                with contextlib.suppress(ClientError):
                    await self.subscribe_all(subscriptions)
                continue

            if not self._closing:
                _LOGGER.warning("Tesira connection lost; reconnecting")
                await self._teardown()

    async def _reader_loop(self, channel: _Channel) -> None:
        """Dispatch every line from one session."""
        telnet = channel.telnet
        if telnet is None:
            return
        reason = "reader stopped"
        try:
            while True:
                line = await telnet.readline()
                await self._handle_line(channel, line)
        except ClientConnectionError as err:
            reason = str(err) or type(err).__name__
        except asyncio.CancelledError:
            reason = "closed"
            raise
        except Exception:
            _LOGGER.exception("%s - reader loop failed", channel.name)
            reason = "reader loop failed"
        finally:
            self._on_connection_lost(channel, reason)

    async def _handle_line(self, channel: _Channel, line: str) -> None:
        """Route a line to the state store or the pending command."""
        if not line:
            return

        match = _PUBLISH_TOKEN_RE.match(line)
        if match is not None:
            await self._apply_state(match.group("token"), match.group("value"))
            if match.group("ok"):
                channel.resolve_pending("+OK")
            return

        # The device echoes the command. A tag such as "-Mic" would otherwise
        # look like -CANNOT_DELIVER / -ERR.
        if line == channel.pending_command:
            _LOGGER.debug("%s - Ignoring echo: %s", channel.name, line)
            return

        if line.startswith(_ERR_PREFIX) or _OK_RE.match(line) is not None:
            if not channel.resolve_pending(line):
                _LOGGER.debug("%s - Unsolicited response: %s", channel.name, line)
            return

        _LOGGER.debug("%s - Ignoring echo: %s", channel.name, line)

    async def _publisher_loop(self) -> None:
        """Publish the newest value of each dirty identifier."""
        retry_after: dict[str, float] = {}
        while True:
            if self._dirty and not self._mqtt.connected:
                await self._mqtt.wait_connected()
            loop = asyncio.get_running_loop()
            now = loop.time()
            ready = [item for item in self._dirty if retry_after.get(item, 0) <= now]
            if not ready:
                if not self._dirty:
                    await self._publish_event.wait()
                else:
                    delay = min(retry_after[item] for item in self._dirty) - now
                    if delay > 0:
                        with contextlib.suppress(TimeoutError):
                            await asyncio.wait_for(self._publish_event.wait(), delay)
                self._publish_event.clear()
                continue

            self._publish_event.clear()
            for identifier in ready:
                if self._connection_lost.is_set():
                    # Teardown drops what is left; resubscribing refreshes it.
                    return
                self._dirty.discard(identifier)
                entry = self._subscriptions.get(identifier)
                serial = self._serial_number
                if entry is None or entry.get("state") is None or serial is None:
                    retry_after.pop(identifier, None)
                    continue
                try:
                    published = await self._mqtt.publish_state(
                        entry["name"], {**entry}, serial
                    )
                except Exception:
                    _LOGGER.exception("Failed to publish state for %s", identifier)
                    published = False
                if not published:
                    retry_after[identifier] = loop.time() + _PUBLISH_RETRY
                    self._dirty.add(identifier)
                else:
                    retry_after.pop(identifier, None)

    async def _heartbeat_loop(self) -> None:
        """Detect a silently dead session."""
        while True:
            await asyncio.sleep(self._tesira.heartbeat_interval)
            try:
                await self.command("DEVICE get serialNumber")
            except ClientError as err:
                self._on_connection_lost(
                    self._command_channel, f"heartbeat failed ({err})"
                )
                return

    async def _request(self, channel: _Channel, command: str) -> str:
        """Send a command and return its ``+OK`` or ``-...`` error line."""
        async with channel.lock:
            telnet = channel.telnet
            if telnet is None or telnet.closed or self._connection_lost.is_set():
                msg = "Client not connected."
                raise ClientConnectionError(msg)

            future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
            channel.pending = future
            channel.pending_command = command
            try:
                _LOGGER.debug("%s - Sending %s", channel.name, command)
                await telnet.write(command)
                return await asyncio.wait_for(future, self._tesira.command_timeout)
            except TimeoutError as err:
                # Otherwise a late reply would be attributed to the next command.
                self._on_connection_lost(channel, f"no response to {command!r}")
                msg = f"Timeout waiting for a response to {command!r}"
                raise ClientTimeoutError(msg) from err
            finally:
                channel.pending = None
                channel.pending_command = None

    @staticmethod
    def _parse_response(response: str) -> str | None:
        """Return the value from a response line, or None for a bare ``+OK``."""
        if response.startswith(_ERR_PREFIX):
            raise ClientResponseError(response)
        match = _OK_RE.match(response)
        if match is None:
            msg = f"Unexpected response: {response}"
            raise ClientResponseError(msg)
        value = match.group("value")
        return None if value is None else _unquote(value)

    async def command(self, command: str) -> str | None:
        """Send a command and return the response value."""
        response = await self._request(self._command_channel, command)
        return self._parse_response(response)

    async def update_state_and_command(self, key: str, value: str) -> None:
        """Update the state on the Tesira DSP for the specified key to the value."""
        entry = self._subscriptions.get(key)
        if entry is None:
            msg = f"Key: {key} does not match any subscriptions"
            raise ClientError(msg)

        normalized = _command_value(entry, value)
        tag = _quote_tag(entry["instance_tag"])
        await self.command(
            f"{tag} set {entry['attribute']} {entry['index']} {normalized}"
        )

    async def get_min_max_levels(self, subscription: Subscription) -> dict[str, float]:
        """Get the min and max levels of a level control block."""
        levels: dict[str, float] = {}
        for key, attribute in (("min_level", "minLevel"), ("max_level", "maxLevel")):
            tag = _quote_tag(subscription.instance_tag)
            raw = await self.command(f"{tag} get {attribute} {subscription.index}")
            try:
                levels[key] = _TYPE_ADAPTERS["float"].validate_python(raw)
            except ValidationError as err:
                msg = f"Invalid {attribute} for {subscription.instance_tag}: {raw!r}"
                raise ClientResponseError(msg) from err
        return levels

    async def subscribe_all(self, subscriptions: set[Subscription]) -> None:
        """Create or refresh all subscriptions; rejected ones are logged and skipped."""
        _LOGGER.info("Subscribing to Tesira")
        failed = 0
        for subscription in subscriptions:
            try:
                await self.subscribe(subscription)
            except ClientResponseError as err:
                failed += 1
                _LOGGER.error("Failed to subscribe to %s: %s", subscription, err)  # noqa: TRY400
        _LOGGER.info(
            "Tesira subscriptions created (%d ok, %d failed)",
            len(subscriptions) - failed,
            failed,
        )

    async def subscribe(self, subscription: Subscription) -> None:
        """Create a subscription and publish its current value."""
        if not self._subscription_channel.connected:
            msg = "Client not connected."
            raise ClientConnectionError(msg)

        _LOGGER.debug("Creating subscription for %s", subscription)
        identifier = _subscription_identifier(subscription)
        is_new = identifier not in self._subscriptions
        if is_new:
            self._subscriptions[identifier] = await self._build_entry(
                subscription, identifier
            )

        tag = _quote_tag(subscription.instance_tag)
        command = (
            f"{tag} subscribe {subscription.attribute} "
            f"{subscription.index} {identifier}"
        )
        try:
            response = await self._request(self._subscription_channel, command)
            if response.startswith(_ERR_PREFIX):
                if _ALREADY_SUBSCRIBED not in response:
                    raise ClientResponseError(response)
                _LOGGER.debug("%s already subscribed", identifier)
            elif _OK_RE.match(response) is None:
                msg = f"Unexpected response to subscribe: {response}"
                raise ClientResponseError(msg)
        except ClientError:
            if is_new:
                self._subscriptions.pop(identifier, None)
            raise

        if self._subscriptions[identifier]["state"] is None:
            # No initial publishToken (already subscribed): fetch the value.
            value = await self.command(
                f"{tag} get {subscription.attribute} {subscription.index}"
            )
            await self._apply_state(identifier, value or "")

    async def _build_entry(
        self, subscription: Subscription, identifier: str
    ) -> dict[str, Any]:
        """Build the state-store entry for a subscription."""
        other_items: dict[str, float] = {}
        match subscription.attribute:
            case "mute":
                variable_type = "bool"
            case "level":
                variable_type = "float"
                other_items = await self.get_min_max_levels(subscription)
            case _:
                variable_type = "str"

        return {
            "instance_tag": subscription.instance_tag,
            "attribute": subscription.attribute,
            "index": subscription.index,
            "state": None,
            "variable_type": variable_type,
            "device_id": f"{self._serial_number}_{subscription.instance_tag}",
            "unique_id": (
                f"{self._serial_number}_{subscription.instance_tag}_"
                f"{subscription.attribute}_{subscription.index}"
            ),
            "name": subscription.name,
            "device_name": subscription.device_name,
            "identifier": identifier,
            **other_items,
        }

    async def _apply_state(self, identifier: str, raw_value: str) -> None:
        """Store a new value and mark it for the publisher task."""
        entry = self._subscriptions.get(identifier)
        if entry is None:
            _LOGGER.warning("Received update for unknown subscription %s", identifier)
            return

        try:
            entry["state"] = _TYPE_ADAPTERS[entry["variable_type"]].validate_python(
                raw_value
            )
        except ValidationError:
            _LOGGER.warning("Ignoring invalid value %r for %s", raw_value, identifier)
            return

        self._dirty.add(identifier)
        self._publish_event.set()

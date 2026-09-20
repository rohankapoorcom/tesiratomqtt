"""Maintains multiple connections to the Biamp Tesira device."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter, ValidationError

from errors import (
    ClientConnectionError,
    ClientError,
    ClientResponseError,
    ClientTimeoutError,
)
from telnet import BiampTesiraTelnetConnection

if TYPE_CHECKING:
    from models import Subscription, TesiraConfig
    from mqtt_connection import MqttConnection

_LOGGER = logging.getLogger(__name__)

# Subscription update, optionally with the "+OK" of the subscribe command folded
# onto the same line (Biamp documents both shapes).
_PUBLISH_TOKEN_RE = re.compile(
    r'^! "publishToken":"(?P<token>[^"]*)" "value":(?P<value>.*?)(?P<ok> \+OK)?$'
)
# Successful command, optionally carrying a value: `+OK` or `+OK "value":<x>`.
_OK_RE = re.compile(r'^\+OK(?: "value":(?P<value>.*))?$')
_ERR_PREFIX = "-ERR"
_ALREADY_SUBSCRIBED = "ALREADY_SUBSCRIBED"

# The serial number ends up in MQTT topics and Home Assistant object ids, which
# only allow this character set. Anything else means we parsed the wrong line.
_SERIAL_RE = re.compile(r"^[A-Za-z0-9_-]+$")

_TYPE_ADAPTERS: dict[str, TypeAdapter] = {
    "bool": TypeAdapter(bool),
    "float": TypeAdapter(float),
    "str": TypeAdapter(str),
}

_RECONNECT_BACKOFF_INITIAL = 1.0
_RECONNECT_BACKOFF_MAX = 60.0


@dataclass
class _Channel:
    """A single telnet session plus the bookkeeping for its in-flight command."""

    name: str
    telnet: BiampTesiraTelnetConnection | None = None
    reader_task: asyncio.Task | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: asyncio.Future[str] | None = None

    @property
    def connected(self) -> bool:
        """Return whether the underlying telnet session is usable."""
        return self.telnet is not None and not self.telnet.closed

    def fail_pending(self, error: Exception) -> None:
        """Fail the in-flight command, if any."""
        if self.pending is not None and not self.pending.done():
            self.pending.set_exception(error)

    def resolve_pending(self, response: str) -> bool:
        """Hand a response line to the in-flight command; return whether one existed."""
        if self.pending is not None and not self.pending.done():
            self.pending.set_result(response)
            return True
        return False


def _unquote(value: str) -> str:
    """Strip one pair of surrounding double quotes, if present."""
    if len(value) >= 2 and value[0] == value[-1] == '"':  # noqa: PLR2004
        return value[1:-1]
    return value


class BiampTesiraConnection:
    """
    BiampTesiraConnection used for communication with Biamp Tesira DSPs.

    Two telnet sessions are used: one carries subscriptions (and therefore the
    asynchronous ``! "publishToken"`` updates), the other carries commands. Each
    session has a background reader task which classifies every incoming line:
    subscription updates go to the state store, ``+OK``/``-ERR`` lines resolve
    the command currently awaiting a response, and everything else (the Tesira
    echoes every command back through a terminal layer) is ignored.
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
        self._closing = False

    @property
    def serial_number(self) -> str | None:
        """Return the serial number of the connected Tesira, if known."""
        return self._serial_number

    @property
    def connected(self) -> bool:
        """Return whether both telnet sessions are usable."""
        return (
            self._subscription_channel.connected
            and self._command_channel.connected
            and not self._connection_lost.is_set()
        )

    # ------------------------------------------------------------------ lifecycle

    async def open(self) -> None:
        """Open both telnet sessions, start their readers and verify the device."""
        _LOGGER.info(
            "Connecting to Tesira at %s:%s", self._tesira.host, self._tesira.port
        )
        await self._teardown()
        self._closing = False
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
        """Close all telnet connections and stop background tasks."""
        self._closing = True
        await self._teardown()

    async def wait_closed(self) -> None:
        """Return once the connection to the Tesira has been lost or closed."""
        await self._connection_lost.wait()

    async def _teardown(self) -> None:
        """Stop background tasks and close the sessions, failing in-flight commands."""
        # Mark the connection as gone first so the readers we are about to cancel
        # do not report a "lost" connection that we are closing on purpose.
        self._connection_lost.set()

        if self._heartbeat_task is not None:
            task, self._heartbeat_task = self._heartbeat_task, None
            if task is not asyncio.current_task():
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

    def _on_connection_lost(self, channel: _Channel, reason: str) -> None:
        """Record that a session is no longer usable and wake anyone waiting on it."""
        if not self._connection_lost.is_set():
            _LOGGER.warning("%s - Tesira connection lost: %s", channel.name, reason)
        error = ClientConnectionError(f"{channel.name} - connection lost: {reason}")
        self._subscription_channel.fail_pending(error)
        self._command_channel.fail_pending(error)
        self._connection_lost.set()

    async def run(
        self, barrier: asyncio.Barrier, subscriptions: set[Subscription]
    ) -> None:
        """
        Supervise the connection until cancelled.

        Reconnects (with exponential backoff) and resubscribes whenever the
        connection is lost, and refreshes all subscriptions every
        ``resubscription_time`` seconds while connected.
        """
        _LOGGER.info("Starting Tesira supervisor loop")
        await barrier.wait()
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

    # ---------------------------------------------------------------- background

    async def _reader_loop(self, channel: _Channel) -> None:
        """Read lines from one session forever and dispatch them."""
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
        """Classify one line from the Tesira and act on it."""
        if not line:
            return

        match = _PUBLISH_TOKEN_RE.match(line)
        if match is not None:
            await self._apply_state(match.group("token"), match.group("value"))
            if match.group("ok"):
                channel.resolve_pending("+OK")
            return

        if line.startswith(_ERR_PREFIX) or _OK_RE.match(line) is not None:
            if not channel.resolve_pending(line):
                _LOGGER.debug("%s - Unsolicited response: %s", channel.name, line)
            return

        # Anything else is the Tesira echoing our own command back (possibly
        # wrapped or repeated by its terminal layer) and carries no information.
        _LOGGER.debug("%s - Ignoring line: %s", channel.name, line)

    async def _heartbeat_loop(self) -> None:
        """Periodically prove the command session is alive."""
        while True:
            await asyncio.sleep(self._tesira.heartbeat_interval)
            try:
                await self.command("DEVICE get serialNumber")
            except ClientError as err:
                self._on_connection_lost(
                    self._command_channel, f"heartbeat failed ({err})"
                )
                return

    # ------------------------------------------------------------------ commands

    async def _request(self, channel: _Channel, command: str) -> str:
        """Send a command on a session and return the raw ``+OK``/``-ERR`` line."""
        async with channel.lock:
            telnet = channel.telnet
            if telnet is None or telnet.closed or self._connection_lost.is_set():
                msg = "Client not connected."
                raise ClientConnectionError(msg)

            future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
            channel.pending = future
            try:
                _LOGGER.debug("%s - Sending %s", channel.name, command)
                await telnet.write(command)
                return await asyncio.wait_for(future, self._tesira.command_timeout)
            except TimeoutError as err:
                # A late reply would otherwise be attributed to the next command,
                # so treat an unresponsive session as dead.
                self._on_connection_lost(channel, f"no response to {command!r}")
                msg = f"Timeout waiting for a response to {command!r}"
                raise ClientTimeoutError(msg) from err
            finally:
                channel.pending = None

    @staticmethod
    def _parse_response(response: str) -> str | None:
        """Turn a raw response line into its value (or None for a bare ``+OK``)."""
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

        await self.command(
            f"{entry['instance_tag']} set {entry['attribute']} {entry['index']} {value}"
        )

    async def get_min_max_levels(self, subscription: Subscription) -> dict[str, float]:
        """Get the min and max levels of a level control block."""
        levels: dict[str, float] = {}
        for key, attribute in (("min_level", "minLevel"), ("max_level", "maxLevel")):
            raw = await self.command(
                f"{subscription.instance_tag} get {attribute} {subscription.index}"
            )
            try:
                levels[key] = _TYPE_ADAPTERS["float"].validate_python(raw)
            except ValidationError as err:
                msg = f"Invalid {attribute} for {subscription.instance_tag}: {raw!r}"
                raise ClientResponseError(msg) from err
        return levels

    # ------------------------------------------------------------- subscriptions

    async def subscribe_all(self, subscriptions: set[Subscription]) -> None:
        """
        Create (or refresh) all of the Tesira subscriptions.

        Subscriptions the Tesira rejects (for example a mistyped instance tag)
        are logged and skipped so one bad entry does not take the rest down;
        connection problems propagate to the caller.
        """
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
        """Create a single Tesira subscription and publish its initial state."""
        if not self._subscription_channel.connected:
            msg = "Client not connected."
            raise ClientConnectionError(msg)

        _LOGGER.debug("Creating subscription for %s", subscription)
        identifier = (
            f"{subscription.instance_tag}_{subscription.attribute}_{subscription.index}"
        )
        is_new = identifier not in self._subscriptions
        if is_new:
            self._subscriptions[identifier] = await self._build_entry(
                subscription, identifier
            )

        command = (
            f"{subscription.instance_tag} subscribe {subscription.attribute} "
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
            # The Tesira reports the current value when a subscription is
            # created; if that did not happen (already subscribed), ask for it.
            value = await self.command(
                f"{subscription.instance_tag} get {subscription.attribute} "
                f"{subscription.index}"
            )
            await self._apply_state(identifier, value or "")

    async def _build_entry(
        self, subscription: Subscription, identifier: str
    ) -> dict[str, Any]:
        """Assemble the state-store entry for a subscription (state not yet known)."""
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
            "unique_id": f"{self._serial_number}_{identifier}",
            "name": subscription.name,
            "device_name": subscription.device_name,
            "identifier": identifier,
            **other_items,
        }

    async def process_tesira_response(self, response: str) -> None:
        """Process a ``! "publishToken"`` line from the Tesira."""
        match = _PUBLISH_TOKEN_RE.match(response.strip())
        if match is None:
            return
        await self._apply_state(match.group("token"), match.group("value"))

    async def _apply_state(self, identifier: str, raw_value: str) -> None:
        """Store a new value for a subscription and publish it to MQTT."""
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

        try:
            await self._mqtt.publish_state(entry["name"], entry, self._serial_number)
        except Exception:
            _LOGGER.exception("Failed to publish state for %s", identifier)

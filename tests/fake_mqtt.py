"""Fake aiomqtt client backed by an in-memory broker."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Self

import aiomqtt

_CLOSE = object()


@dataclass
class Published:
    topic: str
    payload: Any
    retain: bool


class FakeBroker:
    """Records everything published; can refuse or drop connections."""

    def __init__(self) -> None:
        self.published: list[Published] = []
        self.clients: list[FakeClient] = []
        self.refuse = False
        self.connects = 0

    def client_factory(self, **kwargs: Any) -> FakeClient:
        """Drop-in replacement for ``aiomqtt.Client``."""
        return FakeClient(self, kwargs)

    @property
    def current(self) -> FakeClient | None:
        return next((c for c in reversed(self.clients) if c.connected), None)

    def drop(self) -> None:
        """Kill the live connection as a broker outage would."""
        if self.current is not None:
            self.current.drop()

    async def inject(self, topic: str, payload: str) -> None:
        """Deliver a message to the connected client."""
        client = self.current
        assert client is not None
        await client.deliver(topic, payload)

    def on(self, topic: str) -> list[Any]:
        return [p.payload for p in self.published if p.topic == topic]

    def clear(self) -> None:
        self.published.clear()


class FakeClient:
    def __init__(self, broker: FakeBroker, kwargs: dict[str, Any]) -> None:
        self.broker = broker
        self.kwargs = kwargs
        self.connected = False
        self.subscriptions: list[str] = []
        self._queue: asyncio.Queue[Any] = asyncio.Queue()

    async def __aenter__(self) -> Self:
        if self.broker.refuse:
            msg = "connection refused"
            raise aiomqtt.MqttError(msg)
        self.broker.connects += 1
        self.broker.clients.append(self)
        self.connected = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.connected = False
        self._queue.put_nowait(_CLOSE)

    def drop(self) -> None:
        self.connected = False
        self._queue.put_nowait(aiomqtt.MqttError("connection lost"))

    async def deliver(self, topic: str, payload: str) -> None:
        await self.deliver_raw(topic, payload.encode())

    async def deliver_raw(self, topic: str, payload: bytes) -> None:
        message = aiomqtt.Message(
            topic=topic, payload=payload, qos=0, retain=False, mid=0, properties=None
        )
        await self._queue.put(message)

    async def publish(
        self,
        topic: str,
        payload: str,
        retain: bool = False,
        qos: int = 0,  # noqa: ARG002
    ) -> None:
        if not self.connected:
            msg = "not connected"
            raise aiomqtt.MqttError(msg)
        self.broker.published.append(Published(topic, json.loads(payload), retain))

    async def subscribe(self, topic: str) -> None:
        if not self.connected:
            msg = "not connected"
            raise aiomqtt.MqttError(msg)
        self.subscriptions.append(topic)

    @property
    def messages(self) -> AsyncIterator[aiomqtt.Message]:
        return self._iter_messages()

    async def _iter_messages(self) -> AsyncIterator[aiomqtt.Message]:
        while True:
            item = await self._queue.get()
            if item is _CLOSE:
                return
            if isinstance(item, Exception):
                raise item
            yield item

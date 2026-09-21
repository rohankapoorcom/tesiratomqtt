"""HTTP health endpoints for Docker and Kubernetes probes."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Protocol

from aiohttp import web

if TYPE_CHECKING:
    from models import HealthConfig

_LOGGER = logging.getLogger(__name__)


class HasConnected(Protocol):
    """Anything that exposes a live connection flag."""

    @property
    def connected(self) -> bool:
        """Return whether the connection is currently usable."""


def _snapshot(mqtt: HasConnected, tesira: HasConnected) -> dict[str, bool | str]:
    mqtt_ok = mqtt.connected
    tesira_ok = tesira.connected
    return {
        "status": "ok" if mqtt_ok and tesira_ok else "unavailable",
        "mqtt": mqtt_ok,
        "tesira": tesira_ok,
    }


def create_app(mqtt: HasConnected, tesira: HasConnected) -> web.Application:
    """Build the probe app (``/livez``, ``/readyz``, ``/health``)."""

    async def livez(_: web.Request) -> web.Response:
        return web.json_response(_snapshot(mqtt, tesira))

    async def readyz(_: web.Request) -> web.Response:
        body = _snapshot(mqtt, tesira)
        status = 200 if body["status"] == "ok" else 503
        return web.json_response(body, status=status)

    app = web.Application()
    app.router.add_get("/livez", livez)
    app.router.add_get("/readyz", readyz)
    app.router.add_get("/health", readyz)
    return app


class HealthServer:
    """Serves probe endpoints until ``close()``."""

    def __init__(
        self, mqtt: HasConnected, tesira: HasConnected, config: HealthConfig
    ) -> None:
        """Bind connection sources and listen settings; no socket until ``run()``."""
        self._mqtt = mqtt
        self._tesira = tesira
        self._config = config
        self._stop = asyncio.Event()

    async def run(self) -> None:
        """Listen until ``close()``."""
        self._stop.clear()
        runner = web.AppRunner(create_app(self._mqtt, self._tesira))
        await runner.setup()
        site = web.TCPSite(runner, self._config.host, self._config.port)
        await site.start()
        _LOGGER.info(
            "Health server listening on %s:%s", self._config.host, self._config.port
        )
        try:
            await self._stop.wait()
        finally:
            await runner.cleanup()

    async def close(self) -> None:
        """Stop the server."""
        self._stop.set()

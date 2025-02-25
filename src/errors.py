"""Client exceptions."""

import asyncio


class ClientError(Exception):
    """Base exception for clients."""


class ClientConnectionError(ClientError):
    """Exception for client connection errors."""


class ClientTimeoutError(asyncio.TimeoutError, ClientConnectionError):
    """Exception for client connection errors caused by timeouts."""


class ClientResponseError(ClientError):
    """Exception for client response errors."""


class CommandError(ClientError):
    """Base exception for errors caused by sending commands or requests."""

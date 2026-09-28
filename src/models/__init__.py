"""Datamodels used by Tesira2MQTT."""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Characters that break TTP quoting, MQTT topics, or broker handling.
# Includes ASCII controls and Unicode line breaks (NEL, LS, PS).
_UNSAFE_TAG_RE = re.compile('["/&+#\x00-\x1f\x7f\u0085\u2028\u2029]')
# Topic-safe tags are used unchanged so existing identifiers and topics stay put.
_SAFE_TAG_RE = re.compile(r"^[A-Za-z0-9_-]+\Z")


def subscription_identifier(instance_tag: str, attribute: str, index: int) -> str:
    """
    Return the MQTT and publishToken id, unique for every subscription.

    A ``[A-Za-z0-9_-]+`` tag gives ``<tag>_<attribute>_<index>``, as in earlier
    releases. Any other tag gives ``<attribute>_<index>__<hex of the tag>``.
    Tag-first ids always end in ``e_<index>`` (``mute`` and ``level`` end in
    ``e``), while in an encoded id the last ``_`` follows another ``_``. So an
    encoded id never equals a current or earlier tag-first id, and cleaning up
    an earlier id can never touch a live one.
    """
    if _SAFE_TAG_RE.fullmatch(instance_tag):
        return f"{instance_tag}_{attribute}_{index}"
    try:
        encoded = instance_tag.encode()
    except UnicodeEncodeError as err:
        msg = f"instance tag {instance_tag!r} is not valid Unicode"
        raise ValueError(msg) from err
    return f"{attribute}_{index}__{encoded.hex()}"


class MqttConfig(BaseModel):
    """A datamodel representing the MQTT config in config.yaml."""

    base_topic: str
    server: str
    port: int
    user: str
    password: str
    keepalive: int
    client_id: str | None = None


class TesiraConfig(BaseModel):
    """A datamodel representing the Tesira config in config.yaml."""

    host: str
    port: int
    resubscription_time: float
    command_timeout: float = 10.0
    heartbeat_interval: float = 60.0  # 0 disables


class Subscription(BaseModel):
    """A datamodel representing the subscription config in config.yaml."""

    instance_tag: str
    attribute: Literal["mute", "level"]
    index: int
    name: str
    device_name: str

    @field_validator("instance_tag")
    @classmethod
    def _reject_unsafe_instance_tag(cls, value: str) -> str:
        """Reject tags that break TTP commands or MQTT topics."""
        if not value or _UNSAFE_TAG_RE.search(value):
            msg = (
                'instance_tag must be non-empty and must not contain ", /, &, +, #, '
                "control characters, or line breaks"
            )
            raise ValueError(msg)
        try:
            value.encode()
        except UnicodeEncodeError as err:
            msg = "instance_tag must be valid Unicode"
            raise ValueError(msg) from err
        return value

    def __key(self) -> tuple:
        return (
            self.instance_tag,
            self.attribute,
            self.index,
            self.name,
            self.device_name,
        )

    def __hash__(self) -> int:
        """Return the hashkey of this object."""
        return hash(self.__key())

    def __eq__(self, other: object) -> bool:
        """Check the equality of this object with another one."""
        if isinstance(other, Subscription):
            return self.__key() == other.__key()
        return NotImplemented


class HealthConfig(BaseModel):
    """HTTP probe listener; omitted from config.yaml uses these defaults."""

    enabled: bool = True
    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8080


class Config(BaseModel):
    """A datamodel representing the config in config.yaml."""

    mqtt: MqttConfig
    tesira: TesiraConfig
    subscriptions: set[Subscription]
    health: HealthConfig = Field(default_factory=HealthConfig)

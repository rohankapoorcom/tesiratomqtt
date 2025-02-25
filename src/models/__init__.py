"""Datamodels used by Tesira2MQTT."""

from typing import Literal

from pydantic import BaseModel


class MqttConfig(BaseModel):
    """A datamodel representing the MQTT config in config.yaml."""

    base_topic: str
    server: str
    port: int
    user: str
    password: str
    keepalive: int


class TesiraConfig(BaseModel):
    """A datamodel representing the Tesira config in config.yaml."""

    host: str
    port: int
    resubscription_time: int


class Subscription(BaseModel):
    """A datamodel representing the subscription config in config.yaml."""

    instance_tag: str
    attribute: Literal["mute", "level"]
    index: int
    name: str
    device_name: str

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


class Config(BaseModel):
    """A datamodel representing the config in config.yaml."""

    mqtt: MqttConfig
    tesira: TesiraConfig
    subscriptions: set[Subscription]

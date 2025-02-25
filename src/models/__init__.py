"""Datamodels used by Tesira2MQTT."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class MqttConfig(BaseModel):
    base_topic: str
    server: str
    port: int
    user: str
    password: str
    keepalive: int
    # ca: Path


class TesiraConfig(BaseModel):
    host: str
    port: int


class Subscription(BaseModel):
    instance_tag: str
    attribute: Literal["mute", "level"]
    index: int
    name: str
    device_name: str

    def __key(self):
        return (
            self.instance_tag,
            self.attribute,
            self.index,
            self.name,
            self.device_name,
        )

    def __hash__(self):
        return hash(self.__key())

    def __eq__(self, other):
        if isinstance(other, Subscription):
            return self.__key() == other.__key()
        return NotImplemented


class Config(BaseModel):
    mqtt: MqttConfig
    tesira: TesiraConfig
    subscriptions: set[Subscription]

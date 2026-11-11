"""Radio data is independent of Spotify models and credentials."""

from __future__ import annotations

import hashlib
import unicodedata
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .radio_http import validate_url


def plain(value: str, limit: int = 400) -> str:
    return "".join(c for c in value if not unicodedata.category(c).startswith("C"))[
        :limit
    ].strip()


class Station(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=400)
    url: str = Field(max_length=4096, repr=False)
    tags: str = Field(default="", max_length=500)
    country: str = Field(default="", max_length=80)
    language: str = Field(default="", max_length=200)
    codec: str = Field(default="", max_length=40)
    bitrate: int = Field(default=0, ge=0, le=10000)
    custom: bool = False

    _url = field_validator("url")(validate_url)

    @field_validator("name", "tags", "country", "language", "codec")
    @classmethod
    def text(cls, value: str) -> str:
        result = plain(value, 500)
        if value and not result:
            raise ValueError("Text must contain visible characters")
        return result

    @classmethod
    def from_directory(cls, data: dict) -> Station:
        if data.get("lastcheckok") != 1 or data.get("hls", 0) != 0:
            raise ValueError("Unavailable or HLS station")
        codec = data.get("codec", "").upper()
        if codec not in ("MP3", "AAC", "AAC+", "HE-AAC"):
            raise ValueError("Unsupported codec")
        return cls(
            id=data["stationuuid"],
            name=data["name"],
            url=data.get("url_resolved") or data["url"],
            tags=data.get("tags", ""),
            country=data.get("countrycode", ""),
            language=data.get("language", ""),
            codec=codec,
            bitrate=data.get("bitrate", 0),
        )

    @classmethod
    def from_custom(cls, name: str, url: str) -> Station:
        validate_url(url)
        return cls(
            id="custom:" + hashlib.sha256(url.encode()).hexdigest()[:24],
            name=name,
            url=url,
            custom=True,
        )


class RadioStatus(StrEnum):
    STOPPED = "STOPPED"
    BUFFERING = "BUFFERING"
    PLAYING = "PLAYING"
    RECONNECTING = "RECONNECTING"
    ERROR = "ERROR"


class RadioPlayback(BaseModel):
    station: Station | None = None
    status: RadioStatus = RadioStatus.STOPPED
    volume: int = Field(default=70, ge=0, le=100)
    metadata: str = ""
    message: str = ""


class RadioState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1] = 1
    favorites: list[Station] = Field(default_factory=list, max_length=100)
    custom: list[Station] = Field(default_factory=list, max_length=100)
    last: Station | None = None
    volume: int = Field(default=70, ge=0, le=100)

    @model_validator(mode="after")
    def unique_stations(self) -> RadioState:
        for entries in (self.favorites, self.custom):
            if len({station.id for station in entries}) != len(entries):
                raise ValueError(
                    "Saved station lists must not contain duplicate identifiers"
                )
        if any(not station.custom for station in self.custom):
            raise ValueError("Custom stations must be marked as custom")
        return self

"""Validate the Spotify fields used by the controller."""

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Token(BaseModel):
    model_config = ConfigDict(strict=True)

    access_token: SecretStr = Field(min_length=1)
    refresh_token: SecretStr | None = Field(default=None, min_length=1)
    expires_in: int = Field(gt=0)


class Artist(BaseModel):
    model_config = ConfigDict(strict=True)
    name: str = ""


class Album(BaseModel):
    model_config = ConfigDict(strict=True)
    name: str = ""


class Track(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str = ""
    artists: list[Artist] = Field(default_factory=list)
    album: Album | None = None
    duration_ms: int = Field(default=0, ge=0)


class Device(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str = ""
    id: str | None = None


class Playback(BaseModel):
    model_config = ConfigDict(strict=True)

    is_playing: bool = False
    item: Track | None = None
    device: Device | None = None
    progress_ms: int | None = Field(default=None, ge=0)

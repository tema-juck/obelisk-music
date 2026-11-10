"""Validate the Spotify fields used by the controller."""

from typing import Generic, Literal, TypeVar

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SecretStr


class Token(BaseModel):
    model_config = ConfigDict(strict=True)

    access_token: SecretStr = Field(min_length=1)
    refresh_token: SecretStr | None = Field(default=None, min_length=1)
    expires_in: int = Field(gt=0)


class Artist(BaseModel):
    model_config = ConfigDict(strict=True)
    id: str | None = Field(default=None, min_length=1)
    name: str = ""
    uri: str | None = None


class Album(BaseModel):
    model_config = ConfigDict(strict=True)
    id: str | None = Field(default=None, min_length=1)
    name: str = ""
    uri: str | None = None
    artists: list[Artist] = Field(default_factory=list)
    release_date: str = ""
    album_type: str = ""
    total_tracks: int | None = Field(default=None, ge=0)
    restrictions: dict[str, str] = Field(default_factory=dict)


class Track(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str = ""
    artists: list[Artist] = Field(default_factory=list)
    album: Album | None = None
    duration_ms: int = Field(default=0, ge=0)
    uri: str | None = None
    type: str = "track"
    is_local: bool = False
    is_playable: bool | None = None
    restrictions: dict[str, str] = Field(default_factory=dict)

    @property
    def playable(self) -> bool:
        return bool(
            self.uri
            and self.uri.startswith("spotify:track:")
            and self.type == "track"
            and self.is_playable is not False
            and not self.is_local
            and not self.restrictions
        )


class Device(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str = ""
    id: str | None = None
    type: str = "Unknown"
    is_active: bool = False
    is_restricted: bool = False


class Playback(BaseModel):
    model_config = ConfigDict(strict=True)

    is_playing: bool = False
    item: Track | None = None
    device: Device | None = None
    progress_ms: int | None = Field(default=None, ge=0)


class Owner(BaseModel):
    model_config = ConfigDict(strict=True)
    display_name: str | None = None
    id: str | None = None


class ItemCount(BaseModel):
    model_config = ConfigDict(strict=True)
    total: int = Field(ge=0)


class Playlist(BaseModel):
    model_config = ConfigDict(strict=True)

    id: str | None = Field(default=None, min_length=1)
    name: str = "Unavailable playlist"
    owner: Owner | None = None
    items: ItemCount | None = Field(
        default=None, validation_alias=AliasChoices("items", "tracks")
    )


class PlaylistEntry(BaseModel):
    model_config = ConfigDict(strict=True)

    item: Track | None = Field(
        default=None, validation_alias=AliasChoices("item", "track")
    )
    is_local: bool = False

    @property
    def playable(self) -> bool:
        return bool(self.item and self.item.playable and not self.is_local)


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    model_config = ConfigDict(strict=True)

    offset: int = Field(ge=0)
    limit: int = Field(gt=0, le=50)
    total: int = Field(ge=0)
    items: list[T]

    @property
    def next_offset(self) -> int | None:
        following = self.offset + len(self.items)
        return following if self.items and following < self.total else None


CatalogKind = Literal["track", "album", "artist"]
CatalogItem = Track | Album | Artist | None

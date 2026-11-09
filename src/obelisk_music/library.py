"""Session-local page caches; browsing cursors never determine API offsets."""

from dataclasses import dataclass, field
from typing import Generic, TypeVar

from .models import Page, Playlist, PlaylistEntry
from .spotify import SpotifyClient

T = TypeVar("T")


@dataclass
class Collection(Generic[T]):
    pages: dict[int, Page[T]] = field(default_factory=dict)

    @property
    def entries(self) -> list[tuple[int, T]]:
        entries: list[tuple[int, T]] = []
        offset: int | None = 0
        while offset in self.pages:
            page = self.pages[offset]
            entries.extend(
                (page.offset + index, item) for index, item in enumerate(page.items)
            )
            offset = page.next_offset
        return entries

    @property
    def next_offset(self) -> int | None:
        offset: int | None = 0
        while offset in self.pages:
            offset = self.pages[offset].next_offset
        return offset


class SpotifyLibrary:
    def __init__(self, spotify: SpotifyClient) -> None:
        self.spotify = spotify
        self.revision = 0
        self.playlists: Collection[Playlist | None] = Collection()
        self.tracks: dict[str, Collection[PlaylistEntry | None]] = {}

    def clear(self) -> None:
        self.revision += 1
        self.playlists = Collection()
        self.tracks.clear()

    def clear_tracks(self, playlist_id: str) -> None:
        self.revision += 1
        self.tracks.pop(playlist_id, None)

    async def load_playlists(self) -> None:
        collection, revision = self.playlists, self.revision
        offset = collection.next_offset
        if offset is None:
            return
        page = await self.spotify.playlists(offset)
        if revision == self.revision:
            collection.pages[offset] = page

    async def load_tracks(self, playlist_id: str) -> None:
        collection = self.tracks.setdefault(playlist_id, Collection())
        revision, offset = self.revision, collection.next_offset
        if offset is None:
            return
        page = await self.spotify.playlist_items(playlist_id, offset)
        if revision == self.revision:
            collection.pages[offset] = page

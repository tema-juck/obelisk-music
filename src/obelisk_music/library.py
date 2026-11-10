"""Session-local page caches; browsing cursors never determine API offsets."""

from dataclasses import dataclass, field
from typing import Generic, TypeVar

from .models import Album, Artist, CatalogItem, Page, Playlist, PlaylistEntry
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
        self.catalog: dict[tuple[str, ...], Collection[CatalogItem]] = {}
        self.metadata: dict[tuple[str, str], Album | Artist] = {}
        self._catalog_reads: dict[tuple[str, ...], object] = {}

    def clear(self) -> None:
        self.revision += 1
        self.playlists = Collection()
        self.tracks.clear()
        self.catalog.clear()
        self.metadata.clear()
        self._catalog_reads.clear()

    def clear_catalog(self, key: tuple[str, ...]) -> None:
        self.catalog.pop(key, None)
        self._catalog_reads.pop(key, None)
        mode, identifier, *_ = key
        if mode != "search":
            self.metadata.pop((mode, identifier), None)
            self._catalog_reads.pop(("metadata", mode, identifier), None)

    async def load_catalog(self, key: tuple[str, ...]) -> Collection[CatalogItem]:
        collection = self.catalog.setdefault(key, Collection())
        revision, offset = self.revision, collection.next_offset
        if offset is None:
            return collection
        token = self._catalog_reads[key] = object()
        mode, identifier, *category = key
        if mode == "search":
            if offset > 1000:
                return collection
            page = await self.spotify.search(identifier, category[0], offset)
        elif mode == "album":
            page = await self.spotify.album_tracks(identifier, offset)
        elif mode == "artist":
            page = await self.spotify.artist_albums(identifier, offset)
        else:
            raise ValueError("Unknown catalog view")
        if revision == self.revision and self._catalog_reads.get(key) is token:
            collection.pages[offset] = page
        return collection

    async def load_metadata(self, mode: str, identifier: str) -> Album | Artist:
        key, revision = (mode, identifier), self.revision
        if key in self.metadata:
            return self.metadata[key]
        read_key = ("metadata", mode, identifier)
        token = self._catalog_reads[read_key] = object()
        resource = await getattr(self.spotify, mode)(identifier)
        if revision == self.revision and self._catalog_reads.get(read_key) is token:
            self.metadata[key] = resource
        return resource

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

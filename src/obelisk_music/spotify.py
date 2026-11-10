"""Async Spotify Connect requests with session recovery and shared cooldown."""

from __future__ import annotations

import asyncio
import math
import time
from email.utils import parsedate_to_datetime
from typing import Any, TypeVar
from urllib.parse import quote

import httpx
from pydantic import TypeAdapter, ValidationError

from .auth import AuthError
from .models import (
    Album,
    Artist,
    CatalogItem,
    CatalogKind,
    Device,
    Page,
    Playback,
    Playlist,
    PlaylistEntry,
    Track,
)
from .session import SpotifySession

BASE = "https://api.spotify.com/v1"
T = TypeVar("T")


class SpotifyError(RuntimeError):
    def __init__(
        self, message: str, *, status_code: int | None = None, retry_after: float = 0
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class SpotifyClient:
    """The app owns and closes the HTTP client shared with its session."""

    def __init__(self, session: SpotifySession, http: httpx.AsyncClient) -> None:
        self.session = session
        self.http = http
        self._request_lock = asyncio.Lock()
        self._retry_at = 0.0
        # The shared HTTP client also sends OAuth token requests.
        http.event_hooks["request"].append(self._check_cooldown)
        http.event_hooks["response"].append(self._record_cooldown)

    async def _check_cooldown(self, _request: httpx.Request | None = None) -> None:
        remaining = self._retry_at - time.monotonic()
        if remaining > 0:
            raise SpotifyError(
                f"Spotify rate limited; retry in {math.ceil(remaining)}s",
                status_code=429,
                retry_after=remaining,
            )

    async def _record_cooldown(self, response: httpx.Response) -> None:
        if response.status_code == 429:
            delay = self._retry_after(response.headers.get("Retry-After"))
            self._retry_at = max(self._retry_at, time.monotonic() + delay)
            raise SpotifyError(
                f"Spotify rate limited; retry in {math.ceil(delay)}s",
                status_code=429,
                retry_after=delay,
            )

    @staticmethod
    def _retry_after(value: str | None) -> float:
        try:
            seconds = float(value)
            return seconds if math.isfinite(seconds) and seconds >= 0 else 30
        except (TypeError, ValueError):
            try:
                return max(0, parsedate_to_datetime(value).timestamp() - time.time())
            except (TypeError, ValueError, OverflowError):
                return 30

    async def request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        # Serialize sends so a 429 applies before another queued request is sent.
        async with self._request_lock:
            await self._check_cooldown()
            token, generation = await self.session.access_token()
            for attempt in range(2):
                response = await self.http.request(
                    method,
                    BASE + path,
                    headers={"Authorization": f"Bearer {token}"},
                    **kwargs,
                )
                if response.status_code != 401:
                    break
                if attempt:
                    error = AuthError(
                        "Spotify rejected the refreshed session; press L to authorize"
                    )
                    self.session.invalidate(generation, error)
                    raise error
                token, generation = await self.session.access_token(
                    rejected_generation=generation
                )
            if response.status_code == 403:
                raise SpotifyError(
                    "Spotify denied the request (403); check app access and playback restrictions",
                    status_code=403,
                )
            if response.status_code == 404:
                raise SpotifyError(
                    "No active Spotify device (404); open Spotify and start playback"
                    if path.startswith("/me/player")
                    else "Spotify resource is unavailable or deleted (404)",
                    status_code=404,
                )
            if not response.is_success:
                raise SpotifyError(
                    f"Spotify API error ({response.status_code})",
                    status_code=response.status_code,
                )
            if response.status_code == 204 or not response.content:
                return {}
            try:
                payload = response.json()
            except ValueError:
                raise SpotifyError("Spotify returned invalid JSON") from None
            if not isinstance(payload, dict):
                raise SpotifyError("Spotify returned an invalid response")
            return payload

    async def playback(self) -> Playback:
        payload = await self.request("GET", "/me/player")
        try:
            return Playback.model_validate(payload)
        except ValidationError:
            raise SpotifyError("Spotify returned invalid playback data") from None

    async def devices(self) -> list[Device]:
        result = await self.request("GET", "/me/player/devices")
        try:
            return TypeAdapter(list[Device]).validate_python(result.get("devices", []))
        except ValidationError:
            raise SpotifyError("Spotify returned invalid device data") from None

    async def playlists(self, offset: int = 0) -> Page[Playlist | None]:
        if not 0 <= offset <= 100_000:
            raise ValueError("Playlist offset must be between 0 and 100000")
        payload = await self.request(
            "GET", "/me/playlists", params={"offset": offset, "limit": 50}
        )
        try:
            page = Page[Playlist | None].model_validate(payload)
        except ValidationError:
            raise SpotifyError("Spotify returned invalid playlist data") from None
        if page.offset != offset:
            raise SpotifyError("Spotify returned an unexpected playlist offset")
        return page

    async def playlist_items(
        self, playlist_id: str, offset: int = 0
    ) -> Page[PlaylistEntry | None]:
        if offset < 0 or not playlist_id:
            raise ValueError("A playlist ID and nonnegative offset are required")
        payload = await self.request(
            "GET",
            f"/playlists/{quote(playlist_id, safe='')}/items",
            params={"offset": offset, "limit": 50},
        )
        try:
            page = Page[PlaylistEntry | None].model_validate(payload)
        except ValidationError:
            raise SpotifyError("Spotify returned invalid playlist item data") from None
        if page.offset != offset:
            raise SpotifyError("Spotify returned an unexpected playlist item offset")
        return page

    async def pause(self, device_id: str | None = None) -> None:
        await self.request(
            "PUT",
            "/me/player/pause",
            params={"device_id": device_id} if device_id else None,
        )

    async def _catalog_page(
        self,
        path: str,
        model: type[T],
        offset: int,
        *,
        limit: int = 10,
        params: dict[str, object] | None = None,
        key: str | None = None,
    ) -> Page[T | None]:
        if offset < 0:
            raise ValueError("A nonnegative catalog offset is required")
        payload = await self.request(
            "GET", path, params={**(params or {}), "offset": offset, "limit": limit}
        )
        try:
            page = Page[model | None].model_validate(payload[key] if key else payload)
        except (ValidationError, KeyError):
            raise SpotifyError("Spotify returned invalid catalog data") from None
        if page.offset != offset or len(page.items) > page.limit:
            raise SpotifyError("Spotify returned an unexpected catalog page")
        return page

    async def search(
        self, query: str, kind: CatalogKind = "track", offset: int = 0
    ) -> Page[CatalogItem]:
        if not query.strip() or len(query) > 256 or not 0 <= offset <= 1000:
            raise ValueError("Search needs 1–256 characters and an offset up to 1000")
        models = {"track": Track, "album": Album, "artist": Artist}
        if kind not in models:
            raise ValueError("Search supports tracks, albums and artists")
        return await self._catalog_page(
            "/search",
            models[kind],
            offset,
            params={"q": query.strip(), "type": kind},
            key=kind + "s",
        )

    async def album(self, identifier: str) -> Album:
        return await self._catalog_resource("albums", identifier, Album)

    async def artist(self, identifier: str) -> Artist:
        return await self._catalog_resource("artists", identifier, Artist)

    async def _catalog_resource(self, kind: str, identifier: str, model: type[T]) -> T:
        if not identifier:
            raise ValueError("A catalog ID is required")
        payload = await self.request("GET", f"/{kind}/{quote(identifier, safe='')}")
        try:
            resource = TypeAdapter(model).validate_python(payload)
        except ValidationError:
            raise SpotifyError("Spotify returned invalid catalog metadata") from None
        if resource.id != identifier:
            raise SpotifyError("Spotify returned an unexpected catalog ID")
        return resource

    async def album_tracks(
        self, identifier: str, offset: int = 0
    ) -> Page[Track | None]:
        if not identifier:
            raise ValueError("An album ID is required")
        return await self._catalog_page(
            f"/albums/{quote(identifier, safe='')}/tracks", Track, offset, limit=50
        )

    async def artist_albums(
        self, identifier: str, offset: int = 0
    ) -> Page[Album | None]:
        if not identifier:
            raise ValueError("An artist ID is required")
        return await self._catalog_page(
            f"/artists/{quote(identifier, safe='')}/albums", Album, offset
        )

    async def play_track(self, uri: str, device_id: str | None = None) -> None:
        if not uri.startswith("spotify:track:") or not uri.removeprefix(
            "spotify:track:"
        ):
            raise ValueError("A Spotify track URI is required")
        await self.request(
            "PUT",
            "/me/player/play",
            params={"device_id": device_id} if device_id else None,
            json={"uris": [uri]},
        )

    async def play_album(
        self, identifier: str, position: int, device_id: str | None = None
    ) -> None:
        if not identifier or position < 0:
            raise ValueError("An album ID and nonnegative position are required")
        await self.request(
            "PUT",
            "/me/player/play",
            params={"device_id": device_id} if device_id else None,
            json={
                "context_uri": f"spotify:album:{identifier}",
                "offset": {"position": position},
            },
        )

    async def play(self, device_id: str | None = None) -> None:
        await self.request(
            "PUT",
            "/me/player/play",
            params={"device_id": device_id} if device_id else None,
        )

    async def next(self, device_id: str | None = None) -> None:
        await self.request(
            "POST",
            "/me/player/next",
            params={"device_id": device_id} if device_id else None,
        )

    async def previous(self, device_id: str | None = None) -> None:
        await self.request(
            "POST",
            "/me/player/previous",
            params={"device_id": device_id} if device_id else None,
        )

    async def play_playlist(
        self, playlist_id: str, position: int, device_id: str | None = None
    ) -> None:
        if not playlist_id or position < 0:
            raise ValueError("A playlist ID and nonnegative position are required")
        await self.request(
            "PUT",
            "/me/player/play",
            params={"device_id": device_id} if device_id else None,
            json={
                "context_uri": f"spotify:playlist:{playlist_id}",
                "offset": {"position": position},
            },
        )

    async def transfer(self, device_id: str) -> None:
        if not device_id:
            raise ValueError("A target device ID is required")
        await self.request(
            "PUT", "/me/player", json={"device_ids": [device_id], "play": False}
        )

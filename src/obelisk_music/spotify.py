"""Async Spotify Connect requests with session recovery and shared cooldown."""

from __future__ import annotations

import asyncio
import math
import time
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import TypeAdapter, ValidationError

from .auth import AuthError
from .models import Device, Page, Playback, Playlist, PlaylistEntry
from .session import SpotifySession

BASE = "https://api.spotify.com/v1"


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

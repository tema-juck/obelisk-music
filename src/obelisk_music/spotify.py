"""A minimal Spotify Connect Web API adapter."""
from __future__ import annotations
import httpx

BASE = "https://api.spotify.com/v1"


class SpotifyError(RuntimeError):
    pass


class SpotifyClient:
    def __init__(self, token: str):
        self.token = token

    async def request(self, method: str, path: str, **kwargs: object) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.request(
                method, BASE + path,
                headers={"Authorization": f"Bearer {self.token}"}, **kwargs,
            )
        if response.status_code == 204:
            return {}
        if response.status_code == 429:
            raise SpotifyError("Spotify API rate limit (429); retry later")
        if response.status_code == 401:
            raise SpotifyError("Access token expired; log in again")
        if response.status_code == 403:
            raise SpotifyError("Spotify denied playback control (403)")
        if response.is_error:
            raise SpotifyError(f"Spotify API error {response.status_code}: {response.text[:160]}")
        return response.json() if response.content else {}

    async def playback(self) -> dict:
        return await self.request("GET", "/me/player")

    async def devices(self) -> list[dict]:
        result = await self.request("GET", "/me/player/devices")
        return result.get("devices", [])

    async def pause(self) -> None:
        await self.request("PUT", "/me/player/pause")

    async def play(self) -> None:
        await self.request("PUT", "/me/player/play")

    async def next(self) -> None:
        await self.request("POST", "/me/player/next")

    async def previous(self) -> None:
        await self.request("POST", "/me/player/previous")

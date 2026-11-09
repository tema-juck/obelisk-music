"""Coordinate token expiry, refresh and interactive authorization."""

from __future__ import annotations

import asyncio
import time

import httpx

from .auth import AuthError, interactive_login, refresh_access_token
from .models import Token


class SpotifySession:
    def __init__(self, client_id: str, http: httpx.AsyncClient) -> None:
        self.client_id = client_id
        self.http = http
        self._token: Token | None = None
        self._auth_error: AuthError | None = None
        self._refresh_at = 0.0
        self.generation = 0
        self._lock = asyncio.Lock()

    @property
    def authorized(self) -> bool:
        return self._token is not None

    def _install(self, token: Token) -> None:
        self._token = token
        self._auth_error = None
        self._refresh_at = (
            time.monotonic() + token.expires_in - min(30, token.expires_in / 10)
        )
        self.generation += 1

    async def restore(self) -> bool:
        async with self._lock:
            if self._token is not None:
                return True
            token = await refresh_access_token(self.client_id, self.http)
            if token is None:
                return False
            self._install(token)
            return True

    async def login(self, redirect_uri: str) -> None:
        async with self._lock:
            token = await interactive_login(self.client_id, redirect_uri, self.http)
            self._install(token)

    def invalidate(self, generation: int, error: AuthError) -> None:
        if generation == self.generation:
            self._token = None
            self._auth_error = error
            self._refresh_at = 0
            self.generation += 1

    async def access_token(
        self, rejected_generation: int | None = None
    ) -> tuple[str, int]:
        async with self._lock:
            if self._auth_error is not None:
                raise self._auth_error
            if rejected_generation == self.generation:
                self._refresh_at = 0
            if self._token is None or time.monotonic() >= self._refresh_at:
                try:
                    token = await refresh_access_token(self.client_id, self.http)
                except AuthError as exc:
                    self.invalidate(self.generation, exc)
                    raise
                if token is None:
                    error = AuthError("Press L to authorize Spotify")
                    self.invalidate(self.generation, error)
                    raise error
                self._install(token)
            return self._token.access_token.get_secret_value(), self.generation

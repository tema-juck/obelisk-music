"""Minimal keyboard-first Spotify Connect terminal UI."""

from __future__ import annotations

import asyncio
import os
from typing import ClassVar

import httpx
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from .auth import AuthError
from .session import SpotifySession
from .spotify import SpotifyClient, SpotifyError


class ObeliskApp(App):
    """A quiet, text-first interface: Spotify Desktop handles all audio."""

    TITLE = "Obelisk Music"
    CSS_PATH = "obelisk.tcss"
    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("space", "toggle", "Play / Pause"),
        ("n", "next_track", "Next"),
        ("p", "previous_track", "Previous"),
        ("l", "login", "Login"),
        ("r", "refresh", "Refresh"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.client_id = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
        self.redirect_uri = os.environ.get(
            "SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8765/callback"
        )
        self.spotify: SpotifyClient | None = None
        self.http = httpx.AsyncClient(timeout=15)
        self.session = SpotifySession(self.client_id, self.http)
        self.is_playing = False
        self.busy = False

    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            with Horizontal(id="topbar"):
                yield Static("◆  OBELISK", id="brand")
                yield Static("M U S I C   /   S P O T I F Y", id="subtitle")
                yield Static("● OFFLINE", id="connection")

            yield Static(
                "────────────────────────────────────────────────────────────",
                id="top-rule",
            )

            with Horizontal(id="workspace"):
                with Vertical(id="sidebar"):
                    yield Static("LIBRARY", classes="section-label")
                    yield Static(
                        "▸  Now playing", id="nav-playing", classes="nav-active"
                    )
                    yield Static("   Spotify Connect", classes="nav-item")
                    yield Static("", classes="sidebar-gap")
                    yield Static("SESSION", classes="section-label")
                    yield Static(
                        "No active device", id="sidebar-device", classes="side-note"
                    )
                    yield Static("", classes="sidebar-gap")
                    yield Static("SHORTCUTS", classes="section-label")
                    yield Static("space   play / pause", classes="side-note")
                    yield Static("n / p   next / prev", classes="side-note")
                    yield Static("l       sign in", classes="side-note")
                    yield Static("r       refresh", classes="side-note")
                    yield Static("q       quit", classes="side-note")

                with Vertical(id="content"):
                    yield Static("NOW PLAYING", classes="section-label")
                    yield Static("Nothing playing", id="track")
                    yield Static(
                        "Open Spotify Desktop and press L to connect", id="artist"
                    )
                    yield Static("", id="album")
                    yield Static("", classes="flex-space")
                    yield Static(
                        "─── ○ ───────────────────────────────────", id="progress"
                    )
                    with Horizontal(id="time-row"):
                        yield Static("0:00", id="elapsed")
                        yield Static("0:00", id="duration")
                    yield Static(
                        "← PREV           ▷ PLAY           NEXT →", id="controls"
                    )
                    yield Static("", classes="content-bottom")
                    yield Static("OUTPUT", classes="section-label")
                    yield Static("Spotify Desktop · not connected", id="device")

            yield Static(
                "────────────────────────────────────────────────────────────",
                id="bottom-rule",
            )
            with Horizontal(id="bottom-bar"):
                yield Static("Press L to authorize Spotify", id="status")
                yield Static("OBELISK / 0.2", id="version")

    def on_mount(self) -> None:
        if not self.client_id:
            self.status("Set SPOTIFY_CLIENT_ID before login")
        else:
            asyncio.create_task(self.restore_session())
        self.set_interval(8, self.refresh_player)

    def status(self, message: str) -> None:
        self.query_one("#status", Static).update(message)

    async def on_unmount(self) -> None:
        await self.http.aclose()

    @staticmethod
    def _clock(milliseconds: int) -> str:
        seconds = max(0, milliseconds // 1000)
        return f"{seconds // 60}:{seconds % 60:02}"

    async def restore_session(self) -> None:
        try:
            if await self.session.restore():
                self.spotify = SpotifyClient(self.session, self.http)
                await self.refresh_player()
            else:
                self.status("Press L to authorize Spotify")
        except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
            self.status(f"Login needed: {exc}")

    async def refresh_player(self) -> None:
        if not self.spotify or self.busy:
            return
        try:
            playback = await self.spotify.playback()
            self.is_playing = playback.is_playing
            item = playback.item
            track = item.name if item else "Nothing playing"
            artists = ", ".join(artist.name for artist in item.artists) if item else ""
            album = item.album.name if item and item.album else ""
            device = playback.device.name if playback.device else "No active device"
            duration = item.duration_ms if item else 0
            position = min(playback.progress_ms or 0, duration)
            fraction = position / duration if duration else 0
            segments = 40
            filled = min(segments - 1, int(fraction * (segments - 1)))
            progress = "─" * filled + "●" + "─" * (segments - filled - 1)

            self.query_one("#track", Static).update(track)
            self.query_one("#artist", Static).update(artists or "No artist")
            self.query_one("#album", Static).update(album)
            self.query_one("#device", Static).update(device)
            self.query_one("#sidebar-device", Static).update(device)
            self.query_one("#progress", Static).update(progress)
            self.query_one("#elapsed", Static).update(self._clock(position))
            self.query_one("#duration", Static).update(self._clock(duration))
            self.query_one("#controls", Static).update(
                "← PREV           ❚❚ PAUSE          NEXT →"
                if self.is_playing
                else "← PREV           ▷ PLAY           NEXT →"
            )
            self.query_one("#connection", Static).update("● CONNECTED")
            self.status("Connected · playback via Spotify Desktop")
        except (AuthError, SpotifyError, httpx.HTTPError) as exc:
            self.status(str(exc))

    async def action_login(self) -> None:
        if not self.client_id:
            self.status("Missing SPOTIFY_CLIENT_ID")
            return
        if self.busy:
            return
        self.busy = True
        self.status("Opening Spotify login in browser...")
        try:
            await self.session.login(self.redirect_uri)
            self.spotify = SpotifyClient(self.session, self.http)
        except (
            httpx.HTTPError,
            OSError,
            RuntimeError,
            ValueError,
            TimeoutError,
        ) as exc:
            self.status(f"Login failed: {exc}")
        finally:
            self.busy = False
        if self.spotify:
            await self.refresh_player()

    async def _command(self, command: str) -> None:
        if not self.spotify or self.busy:
            self.status("Press L to log in first")
            return
        self.busy = True
        try:
            await getattr(self.spotify, command)()
        except (AuthError, SpotifyError, httpx.HTTPError) as exc:
            self.status(str(exc))
        finally:
            self.busy = False
        await asyncio.sleep(0.3)
        await self.refresh_player()

    async def action_toggle(self) -> None:
        await self._command("pause" if self.is_playing else "play")

    async def action_next_track(self) -> None:
        await self._command("next")

    async def action_previous_track(self) -> None:
        await self._command("previous")

    async def action_refresh(self) -> None:
        await self.refresh_player()


def main() -> None:
    ObeliskApp().run()


if __name__ == "__main__":
    main()

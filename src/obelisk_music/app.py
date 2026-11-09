"""Minimal keyboard-first Spotify Connect terminal UI."""

from __future__ import annotations

import asyncio
import os
from typing import ClassVar

import httpx
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.worker import Worker

from .auth import AuthError
from .session import SpotifySession
from .spotify import SpotifyClient, SpotifyError
from .widgets import FeedbackBar, NowPlaying, PlainText


class ObeliskApp(App):
    """A quiet, text-first interface: Spotify Connect handles all audio."""

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

    def __init__(self, *, http: httpx.AsyncClient | None = None) -> None:
        super().__init__()
        self.client_id = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
        self.redirect_uri = os.environ.get(
            "SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8765/callback"
        )
        self.http = http if http is not None else httpx.AsyncClient(timeout=15)
        self.session = SpotifySession(self.client_id, self.http)
        self.spotify = SpotifyClient(self.session, self.http)
        self.is_playing = False
        self.connection_state = "OFFLINE"
        self._revision = 0
        self._read_lock = asyncio.Lock()
        self._refresh_worker: Worker | None = None
        self._session_worker: Worker | None = None
        self._command_worker: Worker | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            with Horizontal(id="topbar"):
                yield PlainText("◆  OBELISK", id="brand")
                yield PlainText("M U S I C   /   S P O T I F Y", id="subtitle")
                yield PlainText("● OFFLINE", id="connection")
            yield PlainText(id="top-rule")
            with Horizontal(id="workspace"):
                with Vertical(id="sidebar"):
                    yield PlainText("LIBRARY", classes="section-label")
                    yield PlainText("▸  Now playing", id="nav-playing")
                    yield PlainText("Spotify Connect", classes="nav-item")
                    yield PlainText("", classes="sidebar-gap")
                    yield PlainText("SESSION", classes="section-label")
                    yield PlainText("No active device", id="sidebar-device")
                    yield PlainText("", classes="sidebar-gap")
                    yield PlainText("SHORTCUTS", classes="section-label")
                    yield PlainText("space   play / pause")
                    yield PlainText("n / p   next / prev")
                    yield PlainText("l       sign in")
                    yield PlainText("r       refresh")
                    yield PlainText("q       quit")
                yield NowPlaying(id="content")
            yield PlainText(id="bottom-rule")
            with Horizontal(id="bottom-bar"):
                yield FeedbackBar(id="status")
                yield PlainText("OBELISK / 0.2", id="version")

    def on_mount(self) -> None:
        self._layout(self.size.width, self.size.height)
        if self.client_id:
            self._start_session(interactive=False)
        else:
            self.status("Set SPOTIFY_CLIENT_ID before login")
        self.set_interval(8, self._poll)

    def on_resize(self, event: events.Resize) -> None:
        self._layout(event.size.width, event.size.height)

    def _layout(self, width: int, height: int) -> None:
        self.set_class(width < 90 or height < 26, "compact")
        self.set_class(width < 55 or height < 16, "tiny")

    async def on_unmount(self) -> None:
        self.workers.cancel_all()
        await self.workers.wait_for_complete()
        await self.http.aclose()

    def status(self, message: str, *, command: bool = False) -> None:
        self.query_one("#status", FeedbackBar).show_message(message, command=command)

    def _connection(self, state: str) -> None:
        self.connection_state = state
        self.query_one("#connection", PlainText).update(f"● {state}")

    def _report_error(self, exc: Exception, *, command: bool = False) -> None:
        if isinstance(exc, AuthError):
            state, message = "LOGIN NEEDED", str(exc)
        elif isinstance(exc, SpotifyError):
            state = {429: "RATE LIMITED", 403: "DENIED", 404: "NO DEVICE"}.get(
                exc.status_code, "ERROR"
            )
            message = str(exc)
        elif isinstance(exc, httpx.HTTPError):
            state, message = "OFFLINE", "Spotify connection failed; press R to retry"
        elif isinstance(exc, OSError):
            state, message = (
                "OFFLINE",
                "Login callback unavailable; check the redirect port",
            )
        else:
            state, message = (
                "LOGIN NEEDED",
                "Invalid login configuration; check SPOTIFY_REDIRECT_URI",
            )
        self._connection(state)
        self.status(message, command=command)

    @staticmethod
    def _active(worker: Worker | None) -> bool:
        return worker is not None and not worker.is_finished

    def _invalidate_playback(self) -> None:
        self._revision += 1
        if self._refresh_worker is not None:
            self._refresh_worker.cancel()

    def _start_session(self, *, interactive: bool) -> None:
        if not self.client_id:
            self.status("Missing SPOTIFY_CLIENT_ID", command=True)
            return
        if self._active(self._session_worker) and (
            not interactive or self._session_worker.name == "login"
        ):
            return
        if self._active(self._command_worker):
            self.status(
                "Playback command in progress; retry login shortly", command=True
            )
            return
        self._invalidate_playback()
        self._session_worker = self.run_worker(
            self._connect(interactive),
            name="login" if interactive else "restore",
            group="session",
            exclusive=True,
        )

    async def _connect(self, interactive: bool) -> None:
        self._connection("CONNECTING")
        self.status(
            "Opening Spotify login in browser..."
            if interactive
            else "Restoring Spotify session..."
        )
        try:
            if interactive:
                await self.session.login(self.redirect_uri)
            elif not await self.session.restore():
                self._connection("LOGIN NEEDED")
                self.status("Press L to authorize Spotify")
                return
            self._start_refresh()
        except (AuthError, SpotifyError, httpx.HTTPError, OSError, ValueError) as exc:
            self._report_error(exc)

    def _poll(self) -> None:
        if not self._active(self._session_worker) and not self._active(
            self._command_worker
        ):
            self._start_refresh()

    def _start_refresh(self) -> None:
        if not self.session.authorized or self._active(self._refresh_worker):
            return
        self._refresh_worker = self.run_worker(self.refresh_player(), group="playback")

    async def refresh_player(self) -> None:
        if not self.session.authorized or self._read_lock.locked():
            return
        async with self._read_lock:
            revision = self._revision
            try:
                playback = await self.spotify.playback()
                if revision != self._revision:
                    return
                self.is_playing = playback.is_playing
                self.query_one(NowPlaying).show_playback(playback)
                self.query_one("#sidebar-device", PlainText).update(
                    playback.device.name if playback.device else "No active device"
                )
                self._connection("CONNECTED" if playback.device else "NO DEVICE")
            except (AuthError, SpotifyError, httpx.HTTPError) as exc:
                if revision == self._revision:
                    self._report_error(exc)

    def action_login(self) -> None:
        self._start_session(interactive=True)

    def _start_command(self, command: str) -> None:
        if self._active(self._session_worker):
            self.status("Login in progress", command=True)
            return
        if not self.session.authorized:
            self.status("Press L to log in first", command=True)
            return
        if self._active(self._command_worker):
            self.status("Playback command in progress", command=True)
            return
        self._invalidate_playback()
        self._command_worker = self.run_worker(self._command(command), group="command")

    async def _command(self, command: str) -> None:
        try:
            await getattr(self.spotify, command)()
            self.status("Playback command sent", command=True)
        except (AuthError, SpotifyError, httpx.HTTPError) as exc:
            self._report_error(exc, command=True)
        await asyncio.sleep(0.3)
        self._start_refresh()

    def action_toggle(self) -> None:
        self._start_command("pause" if self.is_playing else "play")

    def action_next_track(self) -> None:
        self._start_command("next")

    def action_previous_track(self) -> None:
        self._start_command("previous")

    def action_refresh(self) -> None:
        if self._active(self._session_worker) or self._active(self._command_worker):
            return
        if self.session.authorized:
            self._start_refresh()
        else:
            self._start_session(interactive=False)


def main() -> None:
    ObeliskApp().run()


if __name__ == "__main__":
    main()

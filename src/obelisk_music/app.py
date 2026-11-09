"""Minimal keyboard-first Spotify Connect terminal UI."""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import ClassVar

import httpx
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Input
from textual.worker import Worker

from . import __version__
from .auth import AuthError
from .browser import LibraryBrowser
from .library import SpotifyLibrary
from .models import Device
from .screens import DeviceScreen, HelpScreen
from .session import SpotifySession
from .spotify import SpotifyClient, SpotifyError
from .widgets import FeedbackBar, NowPlaying, PlainText


class ObeliskApp(App):
    """A quiet, text-first interface: Spotify Connect handles all audio."""

    TITLE = "Obelisk Music"
    CSS_PATH = "obelisk.tcss"
    BINDINGS: ClassVar[list[Binding | tuple[str, str, str]]] = [
        ("space", "toggle", "Play / Pause"),
        ("n", "next_track", "Next"),
        ("p", "previous_track", "Previous"),
        ("l", "login", "Login"),
        ("r", "refresh", "Refresh"),
        ("q", "quit", "Quit"),
        Binding("tab", "switch_pane", "Switch pane", priority=True),
        Binding("shift+tab", "switch_pane", "Switch pane backward", priority=True),
        ("escape", "back", "Back"),
        ("d", "devices", "Devices"),
        ("question_mark", "help", "Help"),
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
        self.library = SpotifyLibrary(self.spotify)
        self.target_device_id: str | None = None
        self._library_worker: Worker | None = None
        self._library_key: tuple[str, str | None] | None = None
        self._library_revision = 0
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
                yield PlainText("◆ OBELISK MUSIC", id="brand")
                yield PlainText("● OFFLINE", id="connection")
            yield PlainText(id="top-rule")
            yield LibraryBrowser(id="workspace")
            yield PlainText(id="bottom-rule")
            yield NowPlaying(id="content")
            with Horizontal(id="bottom-bar"):
                yield FeedbackBar(id="status")
                yield PlainText(f"OBELISK / {__version__}", id="version")

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
        self.set_class(width < 70, "compact")
        self.set_class(width < 55 or height < 16, "tiny")

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in {
            binding.action if isinstance(binding, Binding) else binding[1]
            for binding in self.BINDINGS
        } and (len(self.screen_stack) > 1 or isinstance(self.focused, Input)):
            return False
        return super().check_action(action, parameters)

    async def on_unmount(self) -> None:
        self.workers.cancel_all()
        await self.workers.wait_for_complete()
        await self.http.aclose()

    def status(self, message: str, *, command: bool = False) -> None:
        self.screen_stack[0].query_one("#status", FeedbackBar).show_message(
            message, command=command
        )

    def _connection(self, state: str) -> None:
        self.connection_state = state
        self.screen_stack[0].query_one("#connection", PlainText).update(f"● {state}")

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
        self._library_revision += 1
        if self._library_worker is not None:
            self._library_worker.cancel()
        self._library_key = None
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
            self.library.clear()
            self.target_device_id = None
            browser = self.screen_stack[0].query_one(LibraryBrowser)
            browser.clear_library()
            browser.focus_pane("playlists")
            self.screen_stack[0].query_one(FeedbackBar).clear_message()
            self._start_refresh()
            self.call_later(self._start_library, "playlists")
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
                self.screen_stack[0].query_one(NowPlaying).show_playback(playback)
                self.screen_stack[0].query_one(LibraryBrowser).show_playing(
                    playback.item.uri if playback.item else None
                )
                self._connection("CONNECTED" if playback.device else "NO DEVICE")
            except (AuthError, SpotifyError, httpx.HTTPError) as exc:
                if revision == self._revision:
                    self._report_error(exc)

    def action_login(self) -> None:
        self._start_session(interactive=True)

    def _start_command(self, command: str, *args: object) -> None:
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
        target = self.target_device_id if command != "transfer" else None
        self._command_worker = self.run_worker(
            self._command(command, args, target), group="command"
        )

    async def _command(
        self, command: str, args: tuple[object, ...] = (), target: str | None = None
    ) -> None:
        try:
            await getattr(self.spotify, command)(
                *args, **({"device_id": target} if target else {})
            )
            if command == "transfer":
                self.target_device_id = str(args[0])
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
            browser = self.screen_stack[0].query_one(LibraryBrowser)
            pane = (
                self.focused.id
                if self.focused and self.focused.id in ("playlists", "tracks")
                else browser.active_pane
            )
            if not self._active(self._library_worker):
                if pane == "tracks" and browser.playlist:
                    self.library.clear_tracks(browser.playlist.id)
                    browser.show_tracks([], False)
                    self._start_library("tracks", browser.playlist.id)
                else:
                    self.library.clear()
                    browser.clear_library()
                    self._start_library("playlists")
        else:
            self._start_session(interactive=False)

    def _start_library(self, pane: str, playlist_id: str | None = None) -> None:
        if not self.session.authorized:
            self.status("Press l to authorize Spotify", command=True)
            return
        if self._active(self._session_worker):
            self.status("Login in progress", command=True)
            return
        key = (pane, playlist_id)
        if self._active(self._library_worker) and self._library_key == key:
            return
        self._library_revision += 1
        self._library_key = key
        self.screen_stack[0].query_one(LibraryBrowser).message(
            "Loading playlists…" if pane == "playlists" else "Loading tracks…"
        )
        self._library_worker = self.run_worker(
            self._load_library(pane, playlist_id, self._library_revision),
            group="library",
            exclusive=True,
        )

    async def _load_library(
        self, pane: str, playlist_id: str | None, revision: int
    ) -> None:
        browser = self.screen_stack[0].query_one(LibraryBrowser)
        try:
            if pane == "playlists":
                await self.library.load_playlists()
            else:
                await self.library.load_tracks(playlist_id)
            if revision != self._library_revision:
                return
            if pane == "playlists":
                collection = self.library.playlists
                browser.show_playlists(
                    collection.entries, collection.next_offset is not None
                )
            elif browser.playlist and browser.playlist.id == playlist_id:
                collection = self.library.tracks[playlist_id]
                browser.show_tracks(
                    collection.entries, collection.next_offset is not None
                )
        except (AuthError, SpotifyError, httpx.HTTPError) as exc:
            if revision != self._library_revision:
                return
            if isinstance(exc, SpotifyError) and exc.status_code in (403, 404):
                browser.message(
                    f"Playlist unavailable ({exc.status_code}) · r retry"
                    if pane == "tracks"
                    else f"Library denied ({exc.status_code}) · l permissions"
                )
            else:
                browser.message("Could not load this page; press r to retry")
                self._report_error(exc)

    def on_library_browser_open_playlist(
        self, event: LibraryBrowser.OpenPlaylist
    ) -> None:
        browser = self.screen_stack[0].query_one(LibraryBrowser)
        self._library_revision += 1
        if self._library_worker is not None:
            self._library_worker.cancel()
        self._library_key = None
        browser.open_playlist(event.playlist)
        collection = self.library.tracks.get(event.playlist.id)
        if collection and collection.pages:
            browser.show_tracks(collection.entries, collection.next_offset is not None)
        else:
            self._start_library("tracks", event.playlist.id)

    def on_library_browser_load_more(self, event: LibraryBrowser.LoadMore) -> None:
        browser = self.screen_stack[0].query_one(LibraryBrowser)
        if event.pane == "playlists":
            self._start_library("playlists")
        elif browser.playlist:
            self._start_library("tracks", browser.playlist.id)

    def on_library_browser_play_track(self, event: LibraryBrowser.PlayTrack) -> None:
        self._start_command("play_playlist", event.playlist.id, event.position)

    def action_switch_pane(self) -> None:
        self.screen_stack[0].query_one(LibraryBrowser).switch_pane()

    def action_back(self) -> None:
        self.screen_stack[0].query_one(LibraryBrowser).focus_pane("playlists")

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_devices(self) -> None:
        if not self.session.authorized:
            self.status("Press l to authorize Spotify", command=True)
            return
        self.push_screen(DeviceScreen(self.target_device_id), self._device_selected)

    def on_device_screen_load(self, event: DeviceScreen.Load) -> None:
        if event.overlay.is_mounted and self.screen is event.overlay:
            event.overlay.run_worker(
                self._load_devices(event.overlay), group="devices", exclusive=True
            )

    async def _load_devices(self, overlay: DeviceScreen) -> None:
        try:
            devices = await self.spotify.devices()
            if self.screen is overlay:
                overlay.show_devices(devices)
        except (AuthError, SpotifyError, httpx.HTTPError) as exc:
            if self.screen is overlay:
                overlay.query_one("#device-message", PlainText).update(
                    str(exc)
                    if isinstance(exc, (AuthError, SpotifyError))
                    else "Connection failed; r retries"
                )
                self._report_error(exc)

    def _device_selected(self, device: Device | None) -> None:
        if device and device.id and not device.is_restricted:
            self._start_command("transfer", device.id)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="obelisk", description="Keyboard-first Spotify Connect controller"
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.parse_args()
    ObeliskApp().run()


if __name__ == "__main__":
    main()

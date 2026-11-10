"""Minimal keyboard-first Spotify Connect terminal UI."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import ClassVar

import httpx
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.theme import Theme
from textual.widgets import Input
from textual.worker import Worker

from . import __version__
from .auth import AuthError
from .browser import LibraryBrowser
from .config import Settings, config_path, environment_settings
from .library import SpotifyLibrary
from .models import Album, Artist, Device, Track
from .screens import CatalogScreen, DeviceScreen, HelpScreen
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
        ("slash", "search", "Search"),
    ]

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        config_file: Path | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings if settings is not None else environment_settings()
        self.config_file = config_file if config_file is not None else config_path()
        super().__init__()
        self.register_theme(
            Theme(
                name="midnight",
                primary="#91b7ff",
                accent="#91b7ff",
                foreground="#dbe5f1",
                background="#0c1118",
                surface="#0c1118",
                panel="#172231",
                variables={
                    "muted": "#9aabbe",
                    "selection": "#294566",
                    "on-selection": "#edf4ff",
                },
            )
        )
        self.register_theme(
            Theme(
                name="mono",
                primary="#eeeeee",
                accent="#eeeeee",
                foreground="#dddddd",
                background="#101010",
                surface="#101010",
                panel="#242424",
                secondary="#eeeeee",
                success="#eeeeee",
                warning="#eeeeee",
                error="#eeeeee",
                variables={
                    "muted": "#aaaaaa",
                    "selection": "#383838",
                    "on-selection": "#ffffff",
                },
            )
        )
        self.theme = self.settings.appearance.theme
        self.client_id = self.settings.spotify.client_id
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
            yield LibraryBrowser(
                vim_keys=self.settings.interface.vim_keys, id="workspace"
            )
            yield PlainText(id="bottom-rule")
            yield NowPlaying(id="content")
            with Horizontal(id="bottom-bar"):
                yield FeedbackBar(
                    vim_keys=self.settings.interface.vim_keys, id="status"
                )
                yield PlainText(f"OBELISK / {__version__}", id="version")

    def on_mount(self) -> None:
        self._layout(self.size.width, self.size.height)
        if self.client_id:
            self._start_session(interactive=False)
        else:
            self._missing_client_id()
        self.set_interval(self.settings.spotify.refresh_interval, self._poll)

    def _missing_client_id(self, *, command: bool = False) -> None:
        self.status(
            "Run obelisk config init, then config edit (or SPOTIFY_CLIENT_ID)",
            persistent=True,
            command=command,
        )
        self.screen_stack[0].query_one(LibraryBrowser).message(
            f"Config: {self.config_file}"
        )

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
        # Worker.wait raises on cancellation; shutdown must still await every worker.
        await asyncio.gather(
            *(worker.wait() for worker in self.workers), return_exceptions=True
        )
        await self.http.aclose()

    def status(
        self, message: str, *, command: bool = False, persistent: bool = False
    ) -> None:
        self.screen_stack[0].query_one("#status", FeedbackBar).show_message(
            message, command=command, persistent=persistent
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
            self._missing_client_id(command=True)
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
                for screen in self.screen_stack[1:]:
                    if isinstance(screen, CatalogScreen):
                        screen.playing_uri = (
                            playback.item.uri if playback.item else None
                        )
                        screen.show_items(screen.entries, screen.more)
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
            if command in ("pause", "play"):
                self.is_playing = command == "play"
                self.screen_stack[0].query_one(NowPlaying).set_playing(self.is_playing)
            elif command != "transfer":
                self.screen_stack[0].query_one(NowPlaying).set_playing(False)
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
        self.push_screen(
            HelpScreen(
                vim_keys=self.settings.interface.vim_keys, config_file=self.config_file
            )
        )

    def action_devices(self) -> None:
        if not self.session.authorized:
            self.status("Press l to authorize Spotify", command=True)
            return
        self.push_screen(
            DeviceScreen(
                self.target_device_id, vim_keys=self.settings.interface.vim_keys
            ),
            self._device_selected,
        )

    def action_search(self) -> None:
        self._open_catalog(None)

    def _open_catalog(self, resource: Album | Artist | None) -> None:
        if self._active(self._session_worker):
            self.status("Login in progress", command=True)
            return
        if not self.session.authorized:
            self.status("Press l to authorize Spotify before searching", command=True)
            return
        screen = CatalogScreen(resource, vim_keys=self.settings.interface.vim_keys)
        screen.playing_uri = self.screen_stack[0].query_one(NowPlaying).playing_uri
        if isinstance(self.screen, CatalogScreen):
            self.screen.suspend_reads()
        self.push_screen(screen)

    def on_library_browser_inspect(self, event: LibraryBrowser.Inspect) -> None:
        self._open_catalog(event.resource)

    def on_library_browser_pane_changed(
        self, event: LibraryBrowser.PaneChanged
    ) -> None:
        if self.screen_stack:
            self.screen_stack[0].query_one(FeedbackBar).set_context(event.pane)

    async def on_catalog_screen_control(self, event: CatalogScreen.Control) -> None:
        if event.action == "quit":
            await self.action_quit()
        elif event.action in {
            "toggle",
            "next_track",
            "previous_track",
            "devices",
            "help",
            "search",
        }:
            getattr(self, "action_" + event.action)()

    def on_catalog_screen_open(self, event: CatalogScreen.Open) -> None:
        if isinstance(event.resource, (Album, Artist)):
            self._open_catalog(event.resource)
        elif isinstance(event.resource, Track) and event.resource.playable:
            if event.album and event.album.id:
                self._start_command("play_album", event.album.id, event.position)
            elif event.resource.album and event.resource.album.id:
                self._start_command(
                    "play_track", event.resource.uri, event.resource.album.id
                )
            else:
                self.status(
                    "Album details unavailable; press F2 to find and open its album",
                    command=True,
                )

    def on_catalog_screen_load(self, event: CatalogScreen.Load) -> None:
        overlay = event.overlay
        if overlay.is_mounted and overlay.revision == event.revision:
            overlay.read_worker = overlay.run_worker(
                self._load_catalog(overlay, event.revision, event.reset),
                group="catalog",
                exclusive=True,
            )

    async def _load_catalog(
        self, overlay: CatalogScreen, revision: int, reset: bool
    ) -> None:
        key = overlay.key
        if reset:
            self.library.clear_catalog(key)
        generation = self.library.revision

        def current() -> bool:
            return (
                overlay.is_mounted
                and overlay in self.screen_stack
                and overlay.revision == revision
                and generation == self.library.revision
            )

        try:
            if overlay.mode != "search":
                resource = await self.library.load_metadata(overlay.mode, key[1])
                if not current():
                    return
                overlay.resource = resource
                overlay.draw_metadata()
            collection = self.library.catalog.get(key)
            # Cached pages are reused when reopening a view. Only Load more advances.
            if reset or collection is None or not collection.pages or overlay.entries:
                collection = await self.library.load_catalog(key)
            if not current():
                return
            more = collection.next_offset is not None and (
                overlay.mode != "search" or collection.next_offset <= 1000
            )
            overlay.show_items(collection.entries, more)
            overlay.message(
                f"{len(collection.entries)} results · Enter open/play · r refresh"
                if collection.entries
                else "No results available · Try another search or Esc back"
            )
        except (AuthError, SpotifyError, httpx.HTTPError) as exc:
            if not current():
                return
            if isinstance(exc, SpotifyError) and exc.status_code in (403, 404):
                overlay.message(
                    f"Unavailable or restricted by Spotify ({exc.status_code}) · Esc back · r retry"
                )
            elif isinstance(exc, AuthError):
                overlay.message("Session unavailable · Esc then l to authorize Spotify")
                self._report_error(exc)
            elif isinstance(exc, SpotifyError) and exc.status_code == 429:
                overlay.message(f"{exc} · r retries after cooldown")
            else:
                overlay.message("Could not load Spotify catalog · r retry · Esc back")

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
    from .cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()

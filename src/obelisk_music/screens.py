"""Keyboard-only overlays; the app supplies device data and executes transfers."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import DataTable, Input
from textual.worker import Worker

from .browser import CatalogTable, NavigationTable
from .models import Album, Artist, CatalogItem, Device, Track
from .widgets import PlainText, spotify_url


class DeviceScreen(ModalScreen[Device | None]):
    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "close", "Close"),
        ("q", "close", "Close"),
        ("r", "reload", "Refresh"),
    ]

    class Load(Message):
        def __init__(self, overlay: DeviceScreen) -> None:
            super().__init__()
            self.overlay = overlay

    def __init__(
        self, target_device_id: str | None = None, *, vim_keys: bool = True
    ) -> None:
        super().__init__()
        self.devices: list[Device] = []
        self.target_device_id = target_device_id
        self.vim_keys = vim_keys

    def compose(self) -> ComposeResult:
        with Vertical(classes="overlay"):
            yield PlainText(
                "DEVICES · Enter transfer · r refresh · Esc back",
                classes="overlay-title",
            )
            yield NavigationTable(vim_keys=self.vim_keys, id="devices")
            yield PlainText("Loading devices…", id="device-message")

    def on_mount(self) -> None:
        self.query_one("#devices").focus()
        self.action_reload()

    def action_reload(self) -> None:
        self.devices = []
        self.query_one("#devices", NavigationTable).clear()
        self.query_one("#device-message", PlainText).update("Loading devices…")
        self.app.post_message(self.Load(self))

    def show_devices(self, devices: list[Device]) -> None:
        self.devices = devices
        table = self.query_one("#devices", NavigationTable)
        width = max(20, table.size.width)
        table.fill(
            [("", 2), ("Device", width - 14), ("Type", 12)],
            [
                (
                    str(index),
                    [
                        "●" if device.is_active else "",
                        device.name
                        + (" (target)" if device.id == self.target_device_id else "")
                        + (" (restricted)" if device.is_restricted else ""),
                        device.type,
                    ],
                )
                for index, device in enumerate(devices)
            ],
        )
        self.query_one("#device-message", PlainText).update(
            "Enter transfers playback; Esc cancels"
            if devices
            else "No devices. Open Spotify, then press r"
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        if event.cursor_row < len(self.devices):
            device = self.devices[event.cursor_row]
            if device.id and not device.is_restricted:
                self.dismiss(device)
            else:
                self.query_one("#device-message", PlainText).update(
                    "This device cannot accept Spotify Web API commands"
                )

    def action_close(self) -> None:
        self.dismiss(None)


class HelpScreen(ModalScreen):
    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "close", "Close"),
        ("q", "close", "Close"),
        ("j", "vim_down", "Down"),
        ("k", "vim_up", "Up"),
        ("down", "down", "Down"),
        ("up", "up", "Up"),
    ]

    def __init__(
        self, *, vim_keys: bool = True, config_file: Path | None = None
    ) -> None:
        super().__init__()
        self.vim_keys = vim_keys
        self.config_file = config_file

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in ("vim_down", "vim_up") and not self.vim_keys:
            return False
        return super().check_action(action, parameters)

    def action_vim_down(self) -> None:
        self.action_down()

    def action_vim_up(self) -> None:
        self.action_up()

    def compose(self) -> ComposeResult:
        with Vertical(classes="overlay"):
            yield PlainText("KEYBOARD · Esc back", classes="overlay-title")
            with VerticalScroll(id="help-scroll"):
                navigation = (
                    "j / ↓       Next item\nk / ↑       Previous item"
                    if self.vim_keys
                    else "↓           Next item\n↑           Previous item"
                )
                yield PlainText(
                    navigation
                    + "\nEnter       Open item / play track\nTab         Switch pane / search focus\nShift+Tab   Switch backward\nEscape      Back / close overlay\n/           Search Spotify\nF1/F2/F3    Search tracks/albums/artists\n↓ in search Focus results\na / A       Selected track's album / first artist\nSpace       Play / pause\nn / p       Next / previous track\nd           Spotify Connect devices\nl           Spotify login (library)\nr           Refresh current view\n?           Keyboard shortcuts\nq           Quit (outside text input)\n\nEnter on “Load more…” fetches a page.\nTyping never triggers playback shortcuts.\n♫ marks matching Spotify track URIs;\n▸ and the row highlight mark selection."
                    + (
                        f"\n\nConfiguration:\n{self.config_file}\nobelisk config init / edit / validate"
                        if self.config_file
                        else ""
                    ),
                    id="help-text",
                )

    def action_down(self) -> None:
        self.query_one("#help-scroll", VerticalScroll).scroll_down()

    def action_up(self) -> None:
        self.query_one("#help-scroll", VerticalScroll).scroll_up()

    def action_close(self) -> None:
        self.dismiss()


class CatalogScreen(ModalScreen):
    """Catalog views emit intents; no widget performs Spotify requests."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "close", "Back"),
        ("f1", "category('track')", "Tracks"),
        ("f2", "category('album')", "Albums"),
        ("f3", "category('artist')", "Artists"),
        ("r", "reload", "Refresh"),
        ("slash", "search", "Search"),
        ("a", "album", "Album"),
        ("A", "artist", "Artist"),
        ("space", "control('toggle')", "Play / Pause"),
        ("n", "control('next_track')", "Next"),
        ("p", "control('previous_track')", "Previous"),
        ("d", "control('devices')", "Devices"),
        ("question_mark", "control('help')", "Help"),
        ("q", "control('quit')", "Quit"),
    ]

    class Load(Message):
        def __init__(self, overlay: CatalogScreen, *, reset: bool = False) -> None:
            super().__init__()
            self.overlay, self.revision, self.reset = overlay, overlay.revision, reset

    class Open(Message):
        def __init__(
            self, resource: CatalogItem, position: int, album: Album | None
        ) -> None:
            super().__init__()
            self.resource, self.position, self.album = resource, position, album

    class Control(Message):
        def __init__(self, action: str) -> None:
            super().__init__()
            self.action = action

    def __init__(
        self, resource: Album | Artist | None = None, *, vim_keys: bool = True
    ) -> None:
        super().__init__()
        self.resource = resource
        self.mode = (
            "album"
            if isinstance(resource, Album)
            else "artist"
            if resource
            else "search"
        )
        self.category = "track"
        self.search_query = ""
        self.vim_keys = vim_keys
        self.revision = 0
        self.entries: list[tuple[int, CatalogItem]] = []
        self.more = False
        self.playing_uri: str | None = None
        self.read_worker: Worker | None = None
        self.debounce: Timer | None = None

    @property
    def key(self) -> tuple[str, ...]:
        return (
            ("search", self.search_query, self.category)
            if self.mode == "search"
            else (self.mode, self.resource.id)
        )

    def compose(self) -> ComposeResult:
        with Vertical(classes="catalog-overlay"):
            yield PlainText(
                "SEARCH SPOTIFY" if self.mode == "search" else self.mode.upper(),
                id="catalog-title",
                classes="pane-title",
            )
            if self.mode == "search":
                yield Input(
                    placeholder="Search Spotify…", max_length=256, id="search-input"
                )
            yield PlainText(id="catalog-metadata")
            yield CatalogTable(vim_keys=self.vim_keys, id="catalog-results")
            yield PlainText(
                "Type to search · F1 tracks · F2 albums · F3 artists"
                if self.mode == "search"
                else "Loading…",
                id="catalog-message",
            )
            yield PlainText(
                "Tab Focus · Enter Open/Play · Esc Back",
                id="catalog-hint",
            )

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if isinstance(self.focused, Input) and action not in ("close", "category"):
            return False
        return super().check_action(action, parameters)

    def on_mount(self) -> None:
        if self.mode == "search":
            self.query_one(Input).focus()
        else:
            self.query_one(CatalogTable).focus()
            self.request()
        self.draw_metadata()

    def invalidate(self) -> None:
        self.revision += 1
        if self.debounce is not None:
            self.debounce.stop()
            self.debounce = None
        if self.read_worker is not None:
            self.read_worker.cancel()

    def on_unmount(self) -> None:
        self.invalidate()

    def on_screen_resume(self) -> None:
        if not self.is_mounted:
            return
        if self.entries:
            self.message(f"{len(self.entries)} results · Enter open/play · r refresh")
        elif (self.mode != "search" or self.search_query) and (
            self.read_worker is None or self.read_worker.is_finished
        ):
            self.request()

    def request(self, *, reset: bool = False) -> None:
        self.invalidate()
        if self.mode == "search" and not self.search_query:
            self.message("Type to search · F1 tracks · F2 albums · F3 artists")
            return
        self.message("Loading…")
        self.post_message(self.Load(self, reset=reset))

    def on_input_changed(self, event: Input.Changed) -> None:
        self.invalidate()
        self.search_query = event.value.strip()
        self.show_items([], False)
        if self.search_query:
            self.message("Waiting for typing…")
            self.debounce = self.set_timer(0.3, self.request)
        else:
            self.message("Type to search · F1 tracks · F2 albums · F3 artists")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.query_one(CatalogTable).focus()
        if not self.entries:
            self.request()

    def on_key(self, event: events.Key) -> None:
        if isinstance(self.focused, Input) and event.key == "down":
            event.prevent_default()
            event.stop()
            self.query_one(CatalogTable).focus()

    def action_category(self, category: str) -> None:
        if self.mode != "search":
            return
        self.category = category
        self.query_one(CatalogTable).move_cursor(row=0)
        self.show_items([], False)
        self.draw_metadata()
        self.request()

    def action_reload(self) -> None:
        self.request(reset=True)

    def action_search(self) -> None:
        if self.mode == "search":
            self.query_one(Input).focus()
        else:
            self.action_control("search")

    def action_control(self, action: str) -> None:
        self.post_message(self.Control(action))

    def action_album(self) -> None:
        self.inspect(False)

    def action_artist(self) -> None:
        self.inspect(True)

    def inspect(self, artist: bool) -> None:
        table = self.query_one(CatalogTable)
        if table.cursor_row >= len(self.entries):
            return
        item = self.entries[table.cursor_row][1]
        resource = None
        if isinstance(item, Track):
            resource = (
                (item.artists[0] if item.artists else None)
                if artist
                else (
                    item.album
                    or (self.resource if isinstance(self.resource, Album) else None)
                )
            )
        elif isinstance(item, Album) and artist and item.artists:
            resource = item.artists[0]
        if resource and resource.id:
            self.post_message(self.Open(resource, 0, None))
        else:
            self.message("Spotify did not provide these catalog details")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        if event.row_key.value == "more":
            if self.read_worker is None or self.read_worker.is_finished:
                self.request()
            return
        if event.cursor_row < len(self.entries):
            position, item = self.entries[event.cursor_row]
            if (
                isinstance(item, Track)
                and item.playable
                or isinstance(item, (Album, Artist))
                and item.id
            ):
                self.post_message(
                    self.Open(
                        item,
                        position,
                        self.resource if isinstance(self.resource, Album) else None,
                    )
                )
            else:
                self.message("This item is unavailable or restricted; choose another")

    def draw_metadata(self) -> None:
        if self.mode == "search":
            self.query_one("#catalog-title", PlainText).update(
                f"SEARCH SPOTIFY · {self.category.upper()} ⌕"
            )
            text = "F1 Tracks · F2 Albums · F3 Artists"
        elif isinstance(self.resource, Album):
            album = self.resource
            text = f"{album.name} · {', '.join(a.name for a in album.artists)} · {album.release_date} · {album.total_tracks if album.total_tracks is not None else '—'} tracks"
            if album.restrictions:
                text += " · Restricted by Spotify"
        else:
            text = f"{self.resource.name} · Albums and singles on Spotify"
        link = spotify_url(self.resource) if self.resource else None
        self.query_one("#catalog-metadata", PlainText).update(
            Text(text, style=Style(link=link))
        )

    def message(self, text: str) -> None:
        self.query_one("#catalog-message", PlainText).update(text)

    def show_items(self, entries: list[tuple[int, CatalogItem]], more: bool) -> None:
        self.entries, self.more = entries, more
        self.redraw()

    def redraw(self) -> None:
        self.query_one(CatalogTable).show_items(
            self.entries, self.more, self.playing_uri
        )

    def on_resize(self) -> None:
        if self.is_mounted:
            self.call_after_refresh(self.redraw)

    def action_close(self) -> None:
        self.invalidate()
        self.dismiss()

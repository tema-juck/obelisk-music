"""Keyboard-only radio screens. All I/O is owned by RadioController."""

from __future__ import annotations

from typing import ClassVar

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Input

from .audio_pipeline import VisualizerState
from .browser import NavigationTable
from .config import VisualizerSettings
from .radio_directory import GENRES
from .radio_http import RadioError
from .radio_models import RadioPlayback, RadioStatus, Station
from .visualizer import Visualizer
from .widgets import PlainText


class CustomStationScreen(ModalScreen[Station | None]):
    BINDINGS: ClassVar = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="radio-dialog"):
            yield PlainText("ADD CUSTOM STATION", classes="overlay-title")
            yield Input(placeholder="Station name", max_length=400, id="custom-name")
            yield Input(
                placeholder="Direct HTTP(S) MP3/AAC URL",
                max_length=4096,
                id="custom-url",
            )
            yield PlainText("Enter save · Escape cancel", id="custom-message")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.input.id == "custom-name":
            self.query_one("#custom-url", Input).focus()
            return
        try:
            name = self.query_one("#custom-name", Input).value.strip()
            if not name:
                raise RadioError("Enter a station name")
            station = Station.from_custom(name, event.value.strip())
        except ValueError:
            self.query_one("#custom-message", PlainText).update(
                "Enter a name and public HTTP(S) audio URL; no credentials"
            )
        else:
            self.dismiss(station)


class RadioScreen(Screen):
    AUTO_FOCUS = "#radio-stations"
    BINDINGS: ClassVar = [
        ("escape", "back", "Back"),
        ("slash", "search", "Search"),
        ("g", "genres", "Genres"),
        ("f", "favorite", "Favorite"),
        ("v", "favorites", "Favorites"),
        ("c", "custom", "Custom"),
        ("a", "add", "Add station"),
        ("delete", "remove", "Remove custom"),
        ("r", "refresh", "Refresh"),
        ("space", "toggle", "Stop / Resume"),
        ("x", "stop", "Stop"),
        ("t", "visualizer_toggle", "Visualizer on / off"),
        ("z", "visualizer_cycle", "Visualizer preset"),
        ("plus", "louder", "Volume up"),
        ("equal", "louder", "Volume up"),
        ("minus", "quieter", "Volume down"),
        ("question_mark", "help", "Help"),
        ("q", "quit", "Quit"),
        ("1", "genre(0)", "Chill"),
        ("2", "genre(1)", "Electronic"),
        ("3", "genre(2)", "Rock"),
        ("4", "genre(3)", "Jazz"),
        ("5", "genre(4)", "Ambient"),
        ("6", "genre(5)", "Classical"),
    ]

    class Intent(Message):
        def __init__(
            self, screen: RadioScreen, action: str, station: Station | None = None
        ):
            super().__init__()
            self.overlay, self.action, self.station = screen, action, station

    def __init__(
        self, *, vim_keys: bool = True, visualizer: VisualizerState | None = None
    ):
        super().__init__()
        self.vim_keys = vim_keys
        self.visualizer = visualizer or VisualizerState(VisualizerSettings())
        self.entries: list[Station] = []
        self.more = False
        self.view = "favorites"
        self.tag = ""
        self.playback = RadioPlayback()
        self.favorites: set[str] = set()
        self._debounce = None
        self.revision = 0
        self.read_pending = False
        self.last_id: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="radio-shell"):
            yield PlainText("◆ OBELISK RADIO", classes="overlay-title")
            yield PlainText(
                "1 Chill · 2 Electronic · 3 Rock · 4 Jazz · 5 Ambient · 6 Classical",
                id="radio-genres",
            )
            with Horizontal(id="radio-filters"):
                yield Input(
                    placeholder="Name / search", max_length=200, id="radio-search"
                )
                yield Input(placeholder="Country", max_length=2, id="radio-country")
                yield Input(placeholder="Lang", max_length=80, id="radio-language")
                yield Input(placeholder="Tag", max_length=80, id="radio-tag")
            yield PlainText("FAVORITES", id="radio-view", classes="pane-title")
            yield NavigationTable(vim_keys=self.vim_keys, id="radio-stations")
            yield PlainText("Loading stations…", id="radio-message")
            yield PlainText("STOPPED · LIVE · Volume 70%", id="radio-state")
            yield PlainText("Choose a station · Enter play", id="radio-playing")
            yield PlainText("", id="radio-metadata")
            yield Visualizer(self.visualizer, id="radio-visualizer")
            yield PlainText(
                "Enter play · / search · f favorite · ? help\nSpace stop/resume · +/- volume · Esc back",
                id="radio-hint",
            )

    def on_mount(self) -> None:
        self.show_stations([])
        self.call_after_refresh(self.query_one(NavigationTable).focus)
        self.call_after_refresh(self._resize_rows)
        self.intent("load")

    def on_resize(self) -> None:
        self.call_after_refresh(self._resize_rows)

    def _resize_rows(self) -> None:
        if len(self.query("#radio-hint")):
            self.query_one("#radio-hint", PlainText).update(
                "Enter play · Space stop · +/- vol\n/ search · f save · ? help · Esc back"
                if self.size.width < 60
                else "Enter play · / search · f favorite · ? help\nSpace stop/resume · +/- volume · Esc back"
            )
        if len(self.query("#radio-stations")) and self.view != "genres":
            self.show_stations(self.entries, self.more)

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if isinstance(self.focused, Input) and action != "back":
            return False
        return super().check_action(action, parameters)

    def intent(self, action: str, station: Station | None = None) -> None:
        self.post_message(self.Intent(self, action, station))

    def selected(self) -> Station | None:
        row = self.query_one(NavigationTable).cursor_row
        return self.entries[row] if 0 <= row < len(self.entries) else None

    def filters(self) -> dict:
        return {
            "name": self.query_one("#radio-search", Input).value.strip(),
            "country": self.query_one("#radio-country", Input).value.strip(),
            "language": self.query_one("#radio-language", Input).value.strip(),
            "tag": self.query_one("#radio-tag", Input).value.strip(),
        }

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        self.view = "discovery"
        self.revision += 1
        self.intent("invalidate")
        self.show_stations([], reset=True)
        if self._debounce:
            self._debounce.stop()
        self._debounce = self.set_timer(0.3, self.action_refresh)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.query_one(NavigationTable).focus()
        self.action_refresh()

    def on_key(self, event: events.Key) -> None:
        if event.key == "down" and isinstance(self.focused, Input):
            self.query_one(NavigationTable).focus()
            event.stop()
            event.prevent_default()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        if self.read_pending:
            return
        station = self.selected()
        if self.view == "genres":
            self.action_genre(event.cursor_row)
        elif station:
            self.intent("play", station)
        elif self.more:
            self.intent("more")

    def show_stations(
        self, entries: list[Station], more: bool = False, *, reset: bool = False
    ) -> None:
        self.entries, self.more = entries, more
        table = self.query_one(NavigationTable)
        width = max(25, self.size.width - 4)
        rows = []
        for station in entries:
            marker = (
                "♫ "
                if self.playback.station
                and self.playback.station.id == station.id
                and self.playback.status
                in (
                    RadioStatus.PLAYING,
                    RadioStatus.BUFFERING,
                    RadioStatus.RECONNECTING,
                )
                else "  "
            )
            favorite = "★ " if station.id in self.favorites else ""
            details = f"{station.country or '—'} · {station.codec or 'auto'} {station.bitrate or '—'}k · {station.tags}"
            rows.append((station.id, [marker + favorite + station.name, details]))
        if more:
            rows.append(("load-more", ["Load more…", "Enter"]))
        if not rows:
            rows.append(("empty", ["No stations · / search", "g genres · a add"]))
        table.fill(
            [
                ("STATION", max(16, width * 3 // 5)),
                ("COUNTRY · CODEC · TAGS", max(8, width * 2 // 5)),
            ],
            rows,
        )
        if reset:
            index = next((i for i, s in enumerate(entries) if s.id == self.last_id), 0)
            table.move_cursor(row=index)
        self.query_one("#radio-view", PlainText).update(
            self.view.upper()
            + (f" · {self.tag}" if self.tag and self.view == "discovery" else "")
        )

    def message(self, text: str) -> None:
        self.query_one("#radio-message", PlainText).update(text)

    def show_playback(self, state: RadioPlayback) -> None:
        self.playback = state
        self.query_one("#radio-state", PlainText).update(
            f"{state.status.value} · LIVE · Volume {state.volume}%"
        )
        self.query_one("#radio-playing", PlainText).update(
            state.station.name if state.station else "Choose a station · Enter play"
        )
        self.query_one("#radio-metadata", PlainText).update(
            state.message or state.metadata
        )
        if self.view != "genres":
            self.show_stations(self.entries, self.more)

    def action_back(self) -> None:
        if isinstance(self.focused, Input):
            self.query_one(NavigationTable).focus()
        else:
            self.revision += 1
            self.view = "discovery"
            self.action_refresh()

    def action_search(self) -> None:
        self.query_one("#radio-search", Input).focus()

    def action_genres(self) -> None:
        if self._debounce:
            self._debounce.stop()
        self.revision += 1
        self.intent("invalidate")
        self.view = "genres"
        self.entries, self.more = [], False
        self.read_pending = False
        self.query_one(NavigationTable).fill(
            [("GENRES · Enter browse", max(26, self.size.width - 4))],
            [(str(i), [name]) for i, (name, _) in enumerate(GENRES)],
        )
        self.query_one("#radio-view", PlainText).update("GENRES")
        self.query_one(NavigationTable).move_cursor(row=0)
        self.message("Choose a genre · / searches · v favorites · c custom")
        self.query_one(NavigationTable).focus()

    def action_genre(self, index: int) -> None:
        if 0 <= index < len(GENRES):
            self.tag = GENRES[index][1]
            self.query_one("#radio-tag", Input).value = self.tag
            self.view = "discovery"
            self.action_refresh()
            self.call_after_refresh(self.query_one(NavigationTable).focus)

    def action_refresh(self) -> None:
        if self.view == "genres":
            self.action_genres()
            return
        if self._debounce:
            self._debounce.stop()
        self.revision += 1
        self.intent("load")

    def action_favorites(self) -> None:
        self.view = "favorites"
        self.action_refresh()

    def action_custom(self) -> None:
        self.view = "custom"
        self.action_refresh()

    def action_favorite(self) -> None:
        if station := self.selected():
            self.intent("favorite", station)

    def action_add(self) -> None:
        self.app.push_screen(CustomStationScreen(), self._added)

    def _added(self, station: Station | None) -> None:
        if station:
            self.intent("add", station)

    def action_remove(self) -> None:
        if self.view == "custom" and (station := self.selected()):
            self.intent("remove", station)

    def action_toggle(self) -> None:
        self.intent("toggle")

    def action_stop(self) -> None:
        self.intent("stop")

    def action_visualizer_toggle(self) -> None:
        self.intent("visualizer-toggle")

    def action_visualizer_cycle(self) -> None:
        self.intent("visualizer-cycle")

    def action_louder(self) -> None:
        self.intent("louder")

    def action_quieter(self) -> None:
        self.intent("quieter")

    def action_help(self) -> None:
        from .screens import HelpScreen

        self.app.push_screen(
            HelpScreen(vim_keys=self.vim_keys, config_file=self.app.config_file)
        )

    def action_quit(self) -> None:
        self.intent("quit")

    def on_unmount(self) -> None:
        self.revision += 1
        if self._debounce:
            self._debounce.stop()

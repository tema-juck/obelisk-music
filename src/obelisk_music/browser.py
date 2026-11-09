"""Keyboard browsing widgets; API requests belong to the application."""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import DataTable

from .models import Playlist, PlaylistEntry
from .widgets import NowPlaying, PlainText


class NavigationTable(DataTable):
    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("j", "cursor_down", "Next"),
        ("k", "cursor_up", "Previous"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(
            cursor_type="row", show_row_labels=False, cell_padding=0, **kwargs
        )

    def fill(
        self, columns: list[tuple[str, int]], rows: list[tuple[str, list[str]]]
    ) -> None:
        cursor = self.cursor_row
        self.clear(columns=True)
        for index, (label, width) in enumerate(columns):
            heading = Text(label)
            heading.truncate(max(1, width - 1), overflow="ellipsis")
            self.add_column(heading, width=width, key=str(index))
        for key, values in rows:
            cells = []
            for value, (_, width) in zip(values, columns, strict=True):
                text = Text(value)
                text.truncate(max(1, width - 1), overflow="ellipsis")
                cells.append(text)
            self.add_row(*cells, key=key)
        if rows:
            self.move_cursor(row=min(cursor, len(rows) - 1))


class LibraryBrowser(Vertical):
    class OpenPlaylist(Message):
        def __init__(self, playlist: Playlist) -> None:
            super().__init__()
            self.playlist = playlist

    class PlayTrack(Message):
        def __init__(self, playlist: Playlist, position: int) -> None:
            super().__init__()
            self.playlist, self.position = playlist, position

    class LoadMore(Message):
        def __init__(self, pane: str) -> None:
            super().__init__()
            self.pane = pane

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.playlist: Playlist | None = None
        self.playlists: list[tuple[int, Playlist | None]] = []
        self.tracks: list[tuple[int, PlaylistEntry | None]] = []
        self.playlists_more = False
        self.tracks_more = False
        self.active_pane = "playlists"
        self.playing_uri: str | None = None
        self._selected: dict[str, object] = {}

    def compose(self) -> ComposeResult:
        with Horizontal(id="panes"):
            with Vertical(id="playlists-pane"):
                yield PlainText("PLAYLISTS", classes="pane-title")
                yield NavigationTable(id="playlists")
            with Vertical(id="tracks-pane"):
                yield PlainText("TRACKS", id="tracks-title", classes="pane-title")
                yield NavigationTable(id="tracks")
        yield PlainText("Press l to sign in", id="library-message")

    def on_mount(self) -> None:
        self.call_after_refresh(self.render_tables)
        self.query_one("#playlists").focus()

    def on_resize(self) -> None:
        self.call_after_refresh(self.render_tables)

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        if event.widget.id in ("playlists", "tracks"):
            changed = self.active_pane != event.widget.id
            self.active_pane = event.widget.id
            self.set_class(self.active_pane == "tracks", "tracks-view")
            if changed:
                self.call_after_refresh(self.render_tables)

    def message(self, text: str) -> None:
        self.query_one("#library-message", PlainText).update(text)

    def focus_pane(self, pane: str) -> None:
        self.active_pane = pane
        self.set_class(pane == "tracks", "tracks-view")
        self.call_after_refresh(self.query_one(f"#{pane}").focus)
        self.call_after_refresh(self.render_tables)

    def switch_pane(self) -> None:
        current = self.app.focused
        pane = current.id if isinstance(current, NavigationTable) else self.active_pane
        self.focus_pane("tracks" if pane == "playlists" else "playlists")

    def show_playlists(
        self, entries: list[tuple[int, Playlist | None]], more: bool
    ) -> None:
        self.playlists, self.playlists_more = entries, more
        self.render_tables()
        self.message(
            f"{len(entries)} playlists · Enter open"
            if entries
            else "No playlists in your library"
        )

    def clear_library(self) -> None:
        self.playlist = None
        self.tracks = []
        self.tracks_more = False
        self.query_one("#tracks-title", PlainText).update("TRACKS")
        self.show_playlists([], False)

    def open_playlist(self, playlist: Playlist) -> None:
        self.playlist = playlist
        self.tracks = []
        self.tracks_more = False
        self.query_one("#tracks-title", PlainText).update(f"TRACKS · {playlist.name}")
        self.query_one("#tracks", NavigationTable).move_cursor(row=0)
        self.render_tables()
        self.focus_pane("tracks")
        self.message("Loading tracks…")

    def show_tracks(
        self, entries: list[tuple[int, PlaylistEntry | None]], more: bool
    ) -> None:
        self.tracks, self.tracks_more = entries, more
        self.render_tables()
        self.message(
            f"{len(entries)} items · Enter play · Esc playlists"
            if entries
            else "No tracks in this playlist"
        )

    def render_tables(self) -> None:
        if not self.is_mounted:
            return
        playlists = self.query_one("#playlists", NavigationTable)
        width = max(20, playlists.size.width)
        name_width = max(8, (width - 7) * 3 // 5)
        columns = [
            ("", 2),
            ("Playlist", name_width),
            ("Owner", width - 7 - name_width),
            ("#", 5),
        ]
        rows = []
        for position, playlist in self.playlists:
            owner = playlist.owner if playlist else None
            rows.append(
                (
                    str(position),
                    [
                        "",
                        playlist.name if playlist else "Unavailable playlist",
                        (owner.display_name or owner.id or "Unknown")
                        if owner
                        else "Unknown",
                        str(playlist.items.total)
                        if playlist and playlist.items
                        else "—",
                    ],
                )
            )
        if self.playlists_more:
            rows.append(("more", ["", "Load more…", "", ""]))
        playlists.fill(columns, rows)
        tracks = self.query_one("#tracks", NavigationTable)
        width = max(20, tracks.size.width)
        title_width = (width - 13) * 3 // 5
        columns = [
            ("", 2),
            ("", 2),
            ("No.", 4),
            ("Track", title_width),
            ("Artist", width - 13 - title_width),
            ("Time", 5),
        ]
        rows = []
        for position, entry in self.tracks:
            item = entry.item if entry else None
            title = item.name if item and item.name else "Unavailable track"
            artist = ", ".join(a.name for a in item.artists) if item else ""
            if entry and not entry.playable:
                title = f"× {title}"
            values = [
                "",
                "♫" if item and item.uri and item.uri == self.playing_uri else "",
                str(position + 1),
                title,
                artist or "—",
                NowPlaying._clock(item.duration_ms) if item else "—",
            ]
            rows.append((str(position), values))
        if self.tracks_more:
            rows.append(("more", ["", "", "", "Load more…", "", ""]))
        tracks.fill(columns, rows)
        self._selected.clear()
        for table in (playlists, tracks):
            if table.row_count:
                row_key = list(table.rows)[table.cursor_row]
                table.update_cell(row_key, "0", Text("▸"))
                self._selected[table.id] = row_key

    def show_playing(self, uri: str | None) -> None:
        self.playing_uri = uri
        table = self.query_one("#tracks", NavigationTable)
        for position, entry in self.tracks:
            item = entry.item if entry else None
            if str(position) in table.rows:
                table.update_cell(
                    str(position),
                    "1",
                    Text("♫" if item and uri and item.uri == uri else ""),
                )

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        table = event.data_table
        previous = self._selected.get(table.id)
        if previous in table.rows:
            table.update_cell(previous, "0", Text(""))
        if event.row_key in table.rows:
            table.update_cell(event.row_key, "0", Text("▸"))
            self._selected[table.id] = event.row_key

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        pane = event.data_table.id
        if event.row_key.value == "more":
            self.post_message(self.LoadMore(pane))
        elif pane == "playlists" and event.cursor_row < len(self.playlists):
            playlist = self.playlists[event.cursor_row][1]
            if playlist and playlist.id:
                self.post_message(self.OpenPlaylist(playlist))
            else:
                self.message("This playlist is unavailable or deleted")
        elif pane == "tracks" and self.playlist and event.cursor_row < len(self.tracks):
            position, entry = self.tracks[event.cursor_row]
            if entry and entry.playable:
                self.post_message(self.PlayTrack(self.playlist, position))
            else:
                self.message(
                    "This item is unavailable, local, or unsupported; choose another"
                )

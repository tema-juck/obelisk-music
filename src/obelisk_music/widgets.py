"""Plain-text playback widgets shared by the terminal layouts."""

from __future__ import annotations

import time
from urllib.parse import quote

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.timer import Timer
from textual.widgets import Static

from .models import Album, Artist, Playback, Track


def spotify_url(item: Album | Artist | Track) -> str | None:
    if isinstance(item, Track):
        if not item.uri or not item.uri.startswith("spotify:track:"):
            return None
        kind, identifier = "track", item.uri.removeprefix("spotify:track:")
    else:
        kind, identifier = ("album" if isinstance(item, Album) else "artist"), item.id
    return (
        f"https://open.spotify.com/{kind}/{quote(identifier, safe='')}"
        if identifier
        else None
    )


class PlainText(Static):
    def __init__(self, content: str = "", **kwargs) -> None:
        super().__init__(content, markup=False, **kwargs)


class FeedbackBar(PlainText):
    """Polling never replaces feedback from a user's command."""

    HINT = "j/k Move · Enter Open · / Search · d Devices · ? Help · q Quit"

    def __init__(self, *, vim_keys: bool = True, **kwargs) -> None:
        self.hint = self.HINT if vim_keys else self.HINT.replace("j/k", "↑/↓")
        super().__init__(self.hint, **kwargs)
        self._clear_timer: Timer | None = None
        self._command_feedback = False
        self._persistent = False
        self.vim_keys = vim_keys

    def set_context(self, pane: str) -> None:
        hint = (
            self.HINT
            if pane == "playlists"
            else "j/k Move · Enter Play · a/A Album/Artist · Esc Back · / Search · ? Help"
        )
        self.hint = hint if self.vim_keys else hint.replace("j/k", "↑/↓")
        if (
            self._clear_timer is None
            and not self._command_feedback
            and not self._persistent
        ):
            self.update(self.hint)

    def show_message(
        self, message: str, *, command: bool = False, persistent: bool = False
    ) -> None:
        if self._command_feedback and not command:
            return
        if self._clear_timer is not None:
            self._clear_timer.stop()
        self._command_feedback = command
        self._persistent = persistent
        self.update(message)
        self._clear_timer = (
            None if persistent else self.set_timer(8, self.clear_message)
        )

    def clear_message(self) -> None:
        if self._clear_timer is not None:
            self._clear_timer.stop()
        self._command_feedback = False
        self._persistent = False
        self._clear_timer = None
        self.update(self.hint)


class PlaybackProgress(PlainText):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._fraction = 0.0

    def set_position(self, position: int, duration: int) -> None:
        self._fraction = min(1, max(0, position / duration)) if duration else 0
        self._draw()

    def on_resize(self) -> None:
        self._draw()

    def _draw(self) -> None:
        width = max(1, self.content_size.width)
        filled = int(self._fraction * (width - 1))
        self.update("─" * filled + "●" + "─" * (width - filled - 1))


class NowPlaying(Vertical):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.playback = Playback()
        self._observed = time.monotonic()

    @property
    def playing_uri(self) -> str | None:
        return self.playback.item.uri if self.playback.item else None

    @property
    def position_ms(self) -> int:
        playback = self.playback
        duration = playback.item.duration_ms if playback.item else 0
        position = playback.progress_ms or 0
        if playback.is_playing and playback.progress_ms is not None and duration:
            position += int(max(0, time.monotonic() - self._observed) * 1000)
        return min(position, duration)

    def on_mount(self) -> None:
        self.set_interval(1, self._draw_time)

    def set_playing(self, playing: bool) -> None:
        position = self.position_ms if self.playback.progress_ms is not None else None
        self.playback = self.playback.model_copy(
            update={"progress_ms": position, "is_playing": playing}
        )
        self._observed = time.monotonic()
        self._draw_time()

    def compose(self) -> ComposeResult:
        with Horizontal(id="song-row"):
            yield PlainText("Nothing playing", id="track")
            yield PlainText("Open Spotify and press L", id="artist")
            yield PlainText("", id="album")
        with Horizontal(id="playback-row"):
            yield PlainText("space ▷", id="controls")
            yield PlainText("0:00", id="elapsed")
            yield PlainText(" / ", id="time-divider")
            yield PlainText("0:00", id="duration")
            yield PlainText("No active device", id="device")
        yield PlaybackProgress(id="progress")

    @staticmethod
    def _clock(milliseconds: int) -> str:
        seconds = max(0, milliseconds // 1000)
        return f"{seconds // 60}:{seconds % 60:02}"

    def show_playback(self, playback: Playback) -> None:
        self.playback = playback
        self._observed = time.monotonic()
        item = playback.item
        self.query_one("#track", PlainText).update(
            item.name if item else "Nothing playing"
        )
        self._draw_metadata()
        self.query_one("#device", PlainText).update(
            playback.device.name if playback.device else "No active device"
        )
        self._draw_time()

    def on_resize(self) -> None:
        if self.is_mounted:
            self._draw_metadata()

    def _draw_metadata(self) -> None:
        item = self.playback.item
        artists = ", ".join(artist.name for artist in item.artists) if item else ""
        album = item.album.name if item and item.album else ""
        compact = self.app.has_class("compact")
        self.query_one("#artist", PlainText).update(
            " · ".join(
                value
                for value in (artists or "No artist", album if compact else "")
                if value
            )
        )
        self.query_one("#album", PlainText).update(album)

    def _draw_time(self) -> None:
        if not self.is_mounted:
            return
        playback = self.playback
        duration = playback.item.duration_ms if playback.item else 0
        position = self.position_ms
        self.query_one("#progress", PlaybackProgress).set_position(position, duration)
        self.query_one("#elapsed", PlainText).update(self._clock(position))
        self.query_one("#duration", PlainText).update(self._clock(duration))
        self.query_one("#controls", PlainText).update(
            "space ❚❚" if playback.is_playing else "space ▷"
        )

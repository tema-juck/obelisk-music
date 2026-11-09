"""Plain-text playback widgets shared by the terminal layouts."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.timer import Timer
from textual.widgets import Static

from .models import Playback


class PlainText(Static):
    def __init__(self, content: str = "", **kwargs) -> None:
        super().__init__(content, markup=False, **kwargs)


class FeedbackBar(PlainText):
    """Polling never replaces feedback from a user's command."""

    HINT = "j/k Move · Enter Open/Play · d Devices · ? Help · q Quit"

    def __init__(self, *, vim_keys: bool = True, **kwargs) -> None:
        self.hint = self.HINT if vim_keys else self.HINT.replace("j/k", "↑/↓")
        super().__init__(self.hint, **kwargs)
        self._clear_timer: Timer | None = None
        self._command_feedback = False

    def show_message(
        self, message: str, *, command: bool = False, persistent: bool = False
    ) -> None:
        if self._command_feedback and not command:
            return
        if self._clear_timer is not None:
            self._clear_timer.stop()
        self._command_feedback = command
        self.update(message)
        self._clear_timer = (
            None if persistent else self.set_timer(8, self.clear_message)
        )

    def clear_message(self) -> None:
        if self._clear_timer is not None:
            self._clear_timer.stop()
        self._command_feedback = False
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
        item = playback.item
        self.query_one("#track", PlainText).update(
            item.name if item else "Nothing playing"
        )
        artists = ", ".join(artist.name for artist in item.artists) if item else ""
        self.query_one("#artist", PlainText).update(artists or "No artist")
        self.query_one("#album", PlainText).update(
            item.album.name if item and item.album else ""
        )
        self.query_one("#device", PlainText).update(
            playback.device.name if playback.device else "No active device"
        )
        duration = item.duration_ms if item else 0
        position = min(playback.progress_ms or 0, duration)
        self.query_one("#progress", PlaybackProgress).set_position(position, duration)
        self.query_one("#elapsed", PlainText).update(self._clock(position))
        self.query_one("#duration", PlainText).update(self._clock(duration))
        self.query_one("#controls", PlainText).update(
            "space ❚❚" if playback.is_playing else "space ▷"
        )

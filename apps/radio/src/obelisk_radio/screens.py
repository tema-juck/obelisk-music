"""Radio-only scrollable help overlay with native focus restoration."""

from typing import ClassVar

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import ModalScreen

from .widgets import PlainText


class HelpScreen(ModalScreen):
    BINDINGS: ClassVar = [
        ("escape", "close", "Close"),
        ("q", "close", "Close"),
        ("j", "down", "Down"),
        ("k", "up", "Up"),
    ]

    def __init__(self, *, vim_keys=True, config_file=None):
        super().__init__()
        self.vim_keys, self.config_file = vim_keys, config_file

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help-scroll"):
            yield PlainText(
                "RADIO · Esc back\n\nArrows / j/k Navigate stations\nEnter Play / load more\n/ Search name\nTab / Shift+Tab Cycle filters/results\nCountry: two-letter code (DE, US)\nLanguage: name (english)\nTag: genre (house, metal, blues)\ng Genres · 1–6 shortcuts\nf Toggle favorite · v Favorites\nc Custom · a Add URL · Delete Remove custom\nSpace Stop/resume at live edge\nx Stop · +/- Volume\nt Visualizer on/off · z Spectrum/Beat Pulse/Waveform/Off\nEnabling visualization restarts playback at the live edge\nr Refresh · Esc Discovery · q Quit\n? Help\n\nInstall mpv from mpv.io/installation\nDirect public MP3/AAC URLs only\nConfiguration: "
                + str(self.config_file),
                id="help-text",
            )

    def check_action(self, action, parameters):
        if action in ("up", "down") and not self.vim_keys:
            return False
        return super().check_action(action, parameters)

    def action_close(self):
        self.dismiss()

    def action_down(self):
        self.query_one(VerticalScroll).scroll_down()

    def action_up(self):
        self.query_one(VerticalScroll).scroll_up()

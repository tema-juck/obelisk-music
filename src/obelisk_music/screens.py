"""Keyboard-only overlays; the app supplies device data and executes transfers."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import DataTable

from .browser import NavigationTable
from .models import Device
from .widgets import PlainText


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

    def __init__(self, target_device_id: str | None = None) -> None:
        super().__init__()
        self.devices: list[Device] = []
        self.target_device_id = target_device_id

    def compose(self) -> ComposeResult:
        with Vertical(classes="overlay"):
            yield PlainText(
                "DEVICES · Enter transfer · r refresh · Esc back",
                classes="overlay-title",
            )
            yield NavigationTable(id="devices")
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
        ("j", "down", "Down"),
        ("k", "up", "Up"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(classes="overlay"):
            yield PlainText("KEYBOARD · Esc back", classes="overlay-title")
            with VerticalScroll(id="help-scroll"):
                yield PlainText(
                    "j / ↓       Next item\nk / ↑       Previous item\nEnter       Open playlist / play track\nTab         Switch pane\nShift+Tab   Switch pane backward\nEscape      Back / close overlay\nSpace       Play / pause\nn / p       Next / previous track\nd           Spotify Connect devices\nl           Spotify login\nr           Refresh current view\n?           Keyboard shortcuts\nq           Quit\n\nEnter on “Load more…” fetches a page.\n/ is reserved for P2 search.\n♫ marks matching Spotify track URIs;\n▸ and the row highlight mark selection.",
                    id="help-text",
                )

    def action_down(self) -> None:
        self.query_one("#help-scroll", VerticalScroll).scroll_down()

    def action_up(self) -> None:
        self.query_one("#help-scroll", VerticalScroll).scroll_up()

    def action_close(self) -> None:
        self.dismiss()

"""Radio-only Textual lifecycle. No authentication or cross-product controls."""

import asyncio
from pathlib import Path

from textual.app import App
from textual.theme import Theme

from .config import config_path, environment_settings
from .radio_controller import RadioController
from .radio_ui import RadioScreen


class RadioApp(App):
    TITLE = "Obelisk Radio"
    CSS_PATH = "radio.tcss"
    AUTO_FOCUS = "#radio-stations"

    def __init__(
        self,
        *,
        settings=None,
        config_file: Path | None = None,
        radio_backend=None,
        radio_directory=None,
        radio_store=None,
    ):
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
        self.radio = RadioController(
            self, backend=radio_backend, directory=radio_directory, store=radio_store
        )

    def get_default_screen(self):
        screen = RadioScreen(
            vim_keys=self.settings.interface.vim_keys, visualizer=self.radio.visualizer
        )
        self.radio.overlay = screen
        return screen

    def on_resize(self, event):
        self.set_class(event.size.width < 70, "compact")
        self.set_class(event.size.width < 55 or event.size.height < 16, "tiny")

    def on_radio_screen_intent(self, event: RadioScreen.Intent):
        self.radio.intent(event)

    async def on_unmount(self):
        self.workers.cancel_all()
        await asyncio.gather(
            *(worker.wait() for worker in self.workers), return_exceptions=True
        )
        await self.radio.close()


def main():
    from .cli import main as cli_main

    cli_main()

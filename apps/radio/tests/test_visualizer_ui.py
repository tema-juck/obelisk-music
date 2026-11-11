import asyncio
import time

import pytest
from test_audio_analysis import tone
from test_radio_ui import Backend, Directory, station
from textual.widgets import Input

from obelisk_radio.app import RadioApp
from obelisk_radio.audio_analysis import Analyzer
from obelisk_radio.browser import NavigationTable
from obelisk_radio.config import Settings
from obelisk_radio.radio_models import RadioStatus
from obelisk_radio.radio_store import RadioStore
from obelisk_radio.visualizer import Visualizer


@pytest.mark.parametrize("size", [(120, 35), (80, 24), (60, 20), (40, 12)])
@pytest.mark.parametrize("theme", ["midnight", "mono"])
def test_all_modes_responsive_keyboard_focus_missing_pcm_stop_and_resize(
    tmp_path, size, theme
):
    async def run():
        backend = Backend()
        app = RadioApp(
            settings=Settings.model_validate(
                {"appearance": {"theme": theme}, "visualizer": {"enabled": True}}
            ),
            radio_backend=backend,
            radio_directory=Directory(),
            radio_store=RadioStore(tmp_path / "radio.json"),
        )
        async with app.run_test(size=size) as pilot:
            view = app.radio.visualizer
            widget = app.screen.query_one(Visualizer)
            table = app.screen.query_one(NavigationTable)
            assert not widget.can_focus and app.focused is table
            assert "Audio unavailable" in widget.render().plain
            backend.update(station=station(), status=RadioStatus.PLAYING)
            for mode in ("spectrum", "beat-pulse", "waveform"):
                assert view.mode == mode
                view.frame = Analyzer().analyze(tone(93.75, 0.3), 0)
                view.updated = time.monotonic()
                widget.refresh_frame()
                await pilot.pause(0.01)
                rendered = widget.render().plain
                assert mode.upper() in rendered
                assert "Audio unavailable" not in rendered
                if mode == "waveform":
                    assert len(set("".join(rendered.splitlines()[1:]))) > 1
                assert all(
                    len(line) <= widget.size.width for line in rendered.splitlines()
                )
                assert table.region.height >= 2
                assert app.screen.query_one("#radio-metadata").display
                for selector in (
                    "#radio-state",
                    "#radio-playing",
                    "#radio-hint",
                    "#radio-visualizer",
                ):
                    assert app.screen.query_one(selector).region.bottom <= size[1]
                assert app.focused is table
                await pilot.press("z")
                await pilot.pause(0.05)
            assert not view.active and view.mode == "off" and not widget.display
            await pilot.press("t")
            await pilot.pause(0.05)
            assert view.active and view.mode == "spectrum" and widget.display
            await pilot.press("slash", "z", "t")
            assert isinstance(app.focused, Input)
            assert app.focused.value == "zt" and view.mode == "spectrum"
            await pilot.press("escape")
            backend.update(status=RadioStatus.STOPPED)
            assert view.current() is None
            assert "Audio unavailable" in widget.render().plain
            await pilot.resize_terminal(40, 12)
            await pilot.pause(0.05)
            assert widget.region.bottom <= 12
            await pilot.press("q")
        assert not backend.active

    asyncio.run(run())


def test_disabled_widget_has_paused_timer_and_no_focus_target(tmp_path):
    async def run():
        app = RadioApp(
            radio_backend=Backend(),
            radio_directory=Directory(),
            radio_store=RadioStore(tmp_path / "radio.json"),
        )
        async with app.run_test() as pilot:
            widget = app.screen.query_one(Visualizer)
            assert not widget.display and not widget.state.active
            await pilot.press("t")
            await pilot.pause(0.05)
            assert widget.display
            await pilot.press("t")
            await pilot.pause(0.05)
            assert not widget.display

    asyncio.run(run())

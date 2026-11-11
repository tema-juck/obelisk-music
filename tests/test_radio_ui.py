import asyncio

import httpx
import pytest
from textual.widgets import Input

from obelisk_music.app import ObeliskApp
from obelisk_music.browser import NavigationTable
from obelisk_music.config import Settings
from obelisk_music.radio_directory import StationPage
from obelisk_music.radio_models import RadioPlayback, RadioStatus, Station
from obelisk_music.radio_store import RadioStore
from obelisk_music.radio_ui import CustomStationScreen, RadioScreen
from obelisk_music.screens import HelpScreen
from obelisk_music.widgets import PlainText


def station(number=1):
    return Station.from_custom(
        f"[red]Station {number}[/red]", f"https://radio.example/{number}"
    )


class Backend:
    def __init__(self):
        self.snapshot = RadioPlayback()
        self.on_change = lambda snapshot: None
        self.calls = []
        self.stopped = asyncio.Event()
        self.active = False

    def update(self, **changes):
        self.snapshot = self.snapshot.model_copy(update=changes)
        self.on_change(self.snapshot)

    async def play(self, item):
        assert not self.active
        self.active = True
        self.stopped.clear()
        self.calls.append(("play", item.id))
        self.update(station=item, status=RadioStatus.PLAYING)
        try:
            await self.stopped.wait()
        finally:
            self.active = False

    async def stop(self):
        self.calls.append(("stop",))
        self.stopped.set()
        self.update(status=RadioStatus.STOPPED)
        await asyncio.sleep(0.01)

    async def set_volume(self, volume):
        self.calls.append(("volume", volume))
        self.update(volume=volume)


class Directory:
    def __init__(self):
        self.calls = []

    async def search(self, **filters):
        self.calls.append(filters)
        offset = filters.get("offset", 0)
        return StationPage(
            [station(i) for i in range(offset + 1, offset + 4)],
            offset + 3 if not offset else None,
        )


def make_app(tmp_path, *, directory=None, theme="midnight"):
    backend = Backend()
    app = ObeliskApp(
        settings=Settings.model_validate({"appearance": {"theme": theme}}),
        http=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: pytest.fail(f"Radio made a Spotify request: {r.url}")
            )
        ),
        radio_backend=backend,
        radio_directory=directory or Directory(),
        radio_store=RadioStore(tmp_path / "radio.json"),
    )
    return app, backend


@pytest.mark.parametrize("size", [(120, 35), (80, 24), (60, 20), (40, 12)])
@pytest.mark.parametrize("theme", ["midnight", "mono"])
def test_radio_without_spotify_responsive_navigation_and_source_isolation(
    tmp_path, size, theme
):
    async def run():
        app, backend = make_app(tmp_path, theme=theme)
        async with app.run_test(size=size) as pilot:
            await pilot.press("R")
            await pilot.pause(0.15)
            assert isinstance(app.screen, RadioScreen)
            screen = app.screen
            table = screen.query_one(NavigationTable)
            assert table.region.height >= 2 and table.region.width > 0
            for identifier in ("radio-state", "radio-playing", "radio-hint"):
                assert screen.query_one("#" + identifier).region.bottom <= size[1]
            assert app.source == "radio" and not app.session.authorized
            assert len(screen.entries) == 3
            await pilot.press("j", "enter")
            await pilot.pause(0.15)
            assert ("play", station(2).id) in backend.calls
            assert (
                screen.query_one("#radio-playing", PlainText).render().plain
                == station(2).name
            )
            app._start_command("next")
            app._poll()
            await pilot.press("plus")
            await pilot.pause(0.1)
            assert backend.snapshot.volume == 75
            await pilot.press("space")
            await pilot.pause(0.1)
            assert backend.snapshot.status == RadioStatus.STOPPED
            await pilot.press("space")
            await pilot.pause(0.1)
            assert backend.snapshot.status == RadioStatus.PLAYING
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert app.source == "spotify" and not backend.active
        assert not backend.active

    asyncio.run(run())


def test_radio_search_debounce_focus_help_and_pagination(tmp_path):
    async def run():
        directory = Directory()
        app, backend = make_app(tmp_path, directory=directory)
        async with app.run_test() as pilot:
            await pilot.press("R")
            await pilot.pause(0.2)
            screen = app.screen
            original = len(directory.calls)
            await pilot.press("slash")
            assert isinstance(app.focused, Input)
            screen.query_one("#radio-search", Input).value = "j"
            await pilot.pause(0.05)
            screen.query_one("#radio-search", Input).value = "jazz"
            await pilot.pause(0.4)
            assert len(directory.calls) == original + 1
            assert directory.calls[-1]["name"] == "jazz"
            assert not any(call[0] == "play" for call in backend.calls)
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert isinstance(app.focused, NavigationTable)
            assert not any(call[0] == "play" for call in backend.calls)
            table = screen.query_one(NavigationTable)
            table.move_cursor(row=3)
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert len(screen.entries) == 6
            assert table.cursor_row == 3
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert ("play", station(4).id) in backend.calls
            await pilot.press("question_mark")
            assert isinstance(app.screen, HelpScreen)
            await pilot.press("escape")
            assert app.focused is table
            await pilot.press("g")
            assert screen.view == "genres" and table.row_count == 6
            await pilot.press("enter")
            await pilot.pause(0.4)
            assert directory.calls[-1]["tag"] == "chill"

    asyncio.run(run())


def test_favorites_custom_persistence_and_last_station(tmp_path):
    async def run():
        app, _backend = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("R")
            await pilot.pause(0.2)
            await pilot.press("f")
            await pilot.pause(0.1)
            await pilot.press("v")
            await pilot.pause(0.1)
            assert len(app.screen.entries) == 1
            await pilot.press("a")
            assert isinstance(app.screen, CustomStationScreen)
            app.screen.query_one("#custom-name", Input).value = "Custom jazz"
            await pilot.press("enter")
            app.screen.query_one(
                "#custom-url", Input
            ).value = "https://radio.example/custom"
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert isinstance(app.screen, RadioScreen) and app.screen.view == "custom"
            assert app.screen.entries[0].name == "Custom jazz"
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert app.radio.state.last.name == "Custom jazz"
            await pilot.press("delete")
            await pilot.pause(0.1)
            assert not app.screen.entries
        store = RadioStore(tmp_path / "radio.json")
        data = store.load()
        assert len(data.favorites) == 1 and not data.custom and data.last is None

    asyncio.run(run())


def test_stale_directory_result_cannot_replace_favorites(tmp_path):
    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        class SlowDirectory:
            async def search(self, **filters):
                started.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    try:
                        await asyncio.wait_for(release.wait(), 0.5)
                    except TimeoutError:
                        pass
                return StationPage([station(99)], None)

        app, backend = make_app(tmp_path, directory=SlowDirectory())
        async with app.run_test() as pilot:
            await pilot.press("R")
            await asyncio.wait_for(started.wait(), 1)
            assert isinstance(app.focused, NavigationTable), repr(app.focused)
            await pilot.press("v")
            assert app.screen.view == "favorites", repr(app.focused)
            await pilot.pause(0.1)
            release.set()
            await pilot.pause(0.1)
            assert app.screen.view == "favorites" and app.screen.entries == []
            await pilot.press("q")
        assert not backend.active

    asyncio.run(run())


def test_playing_spotify_requires_explicit_pause_and_mutations_are_not_cancelled(
    tmp_path,
):
    async def run():
        app, backend = make_app(tmp_path)
        async with app.run_test() as pilot:
            release = asyncio.Event()
            app._command_worker = app.run_worker(release.wait(), group="command")
            await pilot.press("R")
            assert app.source == "spotify"
            assert not app._command_worker.is_cancelled
            release.set()
            await pilot.pause(0.1)
            app.is_playing = True
            await pilot.press("R")
            await pilot.pause(0.2)
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert not backend.active
            assert (
                "Pause Spotify"
                in app.screen.query_one("#radio-message", PlainText).render().plain
            )

    asyncio.run(run())


@pytest.mark.parametrize("broken", [False, True])
def test_empty_or_failed_directory_preserves_saved_and_custom_access(tmp_path, broken):
    from obelisk_music.radio_http import RadioError

    async def run():
        class EmptyDirectory:
            async def search(self, **filters):
                if broken:
                    raise RadioError("Directory offline · r retries")
                return StationPage([], None)

        app, _backend = make_app(tmp_path, directory=EmptyDirectory())
        async with app.run_test(size=(40, 12)) as pilot:
            await pilot.press("R")
            await pilot.pause(0.1)
            assert not app.screen.entries
            assert (
                "offline"
                in app.screen.query_one("#radio-message", PlainText).render().plain
                if broken
                else "No supported"
                in app.screen.query_one("#radio-message", PlainText).render().plain
            )
            await pilot.press("a")
            assert isinstance(app.screen, CustomStationScreen)
            app.screen.query_one("#custom-name", Input).value = "Local file"
            await pilot.press("enter")
            app.screen.query_one("#custom-url", Input).value = "file:///etc/passwd"
            await pilot.press("enter")
            assert isinstance(app.screen, CustomStationScreen)
            assert (
                "public"
                in app.screen.query_one("#custom-message", PlainText).render().plain
            )
            await pilot.press("escape")
            assert isinstance(app.screen, RadioScreen)
            assert isinstance(app.focused, NavigationTable)
            await pilot.press("v")
            await pilot.pause(0.1)
            assert app.screen.view == "favorites"

    asyncio.run(run())

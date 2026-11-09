import asyncio
import json

import httpx
import pytest
from textual.widgets import Input

from obelisk_music.app import ObeliskApp
from obelisk_music.browser import LibraryBrowser, NavigationTable
from obelisk_music.config import Settings, config_path
from obelisk_music.models import Token
from obelisk_music.screens import DeviceScreen, HelpScreen
from obelisk_music.widgets import PlainText


def playlist(identifier="one"):
    return {
        "id": identifier,
        "name": f"[red]{identifier}[/red]",
        "owner": {"display_name": "Owner"},
        "items": {"total": 53},
    }


def track(name="Track", uri="spotify:track:same"):
    return {
        "item": {
            "name": name,
            "uri": uri,
            "artists": [{"name": "Artist"}],
            "duration_ms": 125000,
        }
    }


class SpotifyStub:
    def __init__(self):
        self.calls = []
        self.playlists = [playlist(), playlist("two")]
        self.items = {
            "one": [track(), {"item": None}, track("Duplicate")],
            "two": [track("Second playlist")],
        }
        self.devices = [
            {"id": "desktop", "name": "Desktop", "type": "computer", "is_active": True},
            {"id": "speaker", "name": "Speaker", "type": "speaker"},
            {
                "id": "restricted",
                "name": "Restricted",
                "type": "speaker",
                "is_restricted": True,
            },
        ]
        self.failures = {}

    def __call__(self, request):
        self.calls.append(request)
        failure = self.failures.get((request.method, request.url.path))
        if isinstance(failure, Exception):
            raise failure
        if failure:
            return httpx.Response(failure, headers={"Retry-After": "60"})
        if request.method != "GET":
            return httpx.Response(204)
        if request.url.path == "/v1/me/player":
            return httpx.Response(
                200,
                json={
                    "is_playing": True,
                    "item": track()["item"],
                    "device": self.devices[0] if self.devices else None,
                },
            )
        if request.url.path == "/v1/me/player/devices":
            return httpx.Response(200, json={"devices": self.devices})
        rows = (
            self.playlists
            if request.url.path == "/v1/me/playlists"
            else self.items[request.url.path.split("/")[3]]
        )
        offset = int(request.url.params["offset"])
        return httpx.Response(
            200,
            json={
                "offset": offset,
                "limit": 50,
                "total": len(rows),
                "items": rows[offset : offset + 50],
            },
        )

    def requests(self, path, method=None):
        return [
            r
            for r in self.calls
            if r.url.path == path and (method is None or r.method == method)
        ]


def app_for(stub, **kwargs):
    app = ObeliskApp(
        http=httpx.AsyncClient(transport=httpx.MockTransport(stub)), **kwargs
    )
    app.session._install(Token(access_token="test-access", expires_in=3600))
    return app


@pytest.mark.parametrize("size", [(120, 35), (80, 24), (40, 12)])
def test_configured_navigation_without_vim_in_all_panes_and_overlays(size):
    async def run():
        app = app_for(SpotifyStub(), settings=Settings(interface={"vim_keys": False}))
        async with app.run_test(size=size) as pilot:
            await pilot.press("r")
            await settle(app)
            table = app.query_one("#playlists", NavigationTable)
            await pilot.press("j")
            assert table.cursor_row == 0
            await pilot.press("down")
            assert table.cursor_row == 1
            await pilot.press("k")
            assert table.cursor_row == 1
            await pilot.press("up", "enter")
            await settle(app)
            table = app.query_one("#tracks", NavigationTable)
            await pilot.press("j")
            assert table.cursor_row == 0
            await pilot.press("down")
            assert table.cursor_row == 1
            await pilot.press("d")
            await settle(app)
            devices = app.screen.query_one("#devices", NavigationTable)
            await pilot.press("j")
            assert devices.cursor_row == 0
            await pilot.press("down")
            assert devices.cursor_row == 1
            await pilot.press("escape", "question_mark")
            assert isinstance(app.screen, HelpScreen)
            assert app.screen.check_action("vim_down", ()) is False
            help_text = str(app.screen.query_one("#help-text", PlainText).render())
            assert "j /" not in help_text
            assert str(config_path()) in help_text
            from textual.containers import VerticalScroll

            scroll = app.screen.query_one(VerticalScroll)
            await pilot.press("j")
            assert scroll.scroll_y == 0
            await pilot.press("down")
            await pilot.pause(0.3)
            assert (scroll.scroll_y > 0) == (scroll.max_scroll_y > 0)
            await pilot.press("escape", "q")
            assert not app.is_running

    asyncio.run(run())


def test_default_vim_help_scrolling_remains_available():
    async def run():
        app = app_for(SpotifyStub())
        async with app.run_test(size=(40, 12)) as pilot:
            await pilot.press("question_mark", "j")
            await pilot.pause(0.3)
            assert app.screen.query_one("#help-scroll").scroll_y > 0
            await pilot.press("k")
            await pilot.pause(0.3)
            assert app.screen.query_one("#help-scroll").scroll_y == 0

    asyncio.run(run())


def test_configured_polling_interval_is_used_by_real_timer():
    async def run():
        stub = SpotifyStub()
        app = app_for(stub, settings=Settings(spotify={"refresh_interval": 1}))
        async with app.run_test():
            assert not stub.requests("/v1/me/player")
            async with asyncio.timeout(2):
                while not stub.requests("/v1/me/player"):
                    await asyncio.sleep(0.01)
            assert app.theme == "midnight"
            assert app.current_theme.background == "#0c1118"

    asyncio.run(run())


def test_file_settings_are_injected_before_ui_without_event_loop_file_io(monkeypatch):
    from unittest.mock import AsyncMock

    from obelisk_music import config

    path = config.config_path()
    config.init_config(path)
    path.write_text('[spotify]\nclient_id = "file-client-id"\nrefresh_interval = 21')
    settings = config.load_settings(path)
    monkeypatch.setattr(
        config, "_read", lambda *args, **kwargs: pytest.fail("Configuration I/O in UI")
    )

    async def run():
        app = ObeliskApp(
            settings=settings,
            config_file=path,
            http=httpx.AsyncClient(
                transport=httpx.MockTransport(lambda r: httpx.Response(204))
            ),
        )
        monkeypatch.setattr(app.session, "restore", AsyncMock(return_value=False))
        assert app.client_id == "file-client-id"
        async with app.run_test() as pilot:
            await settle(app)
            app.session.restore.assert_awaited_once()
            assert "Press L" in text(app, "#status")
            await pilot.press("q")

    asyncio.run(run())


def test_missing_client_id_displays_path_and_persistent_setup_hint():
    async def run():
        app = ObeliskApp(settings=Settings())
        async with app.run_test() as pilot:
            assert str(config_path()) in text(app, "#library-message")
            assert "config init" in text(app, "#status")
            from obelisk_music.widgets import FeedbackBar

            assert app.query_one(FeedbackBar)._clear_timer is None
            await pilot.press("space", "l")
            assert str(config_path()) in text(app, "#library-message")
            assert "config init" in text(app, "#status")

    asyncio.run(run())


async def settle(app):
    async with asyncio.timeout(3):
        while any(not w.is_finished for w in app.workers):
            await asyncio.sleep(0.01)
    await asyncio.sleep(0.02)


def text(app, selector):
    return str(app.screen_stack[0].query_one(selector, PlainText).render())


def test_navigation_panes_and_plain_metadata():
    async def run():
        stub = SpotifyStub()
        app = app_for(stub)
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.press("r")
            await settle(app)
            table = app.query_one("#playlists", NavigationTable)
            assert table.row_count == 2
            assert str(table.get_cell("0", "1")) == "[red]one[/red]"
            assert str(table.get_cell("0", "2")) == "Owner"
            assert str(table.get_cell("0", "3")) == "53"
            await pilot.press("j")
            assert table.cursor_row == 1
            await pilot.press("k", "down", "up", "enter")
            await settle(app)
            assert app.focused.id == "tracks"
            assert app.query_one(LibraryBrowser).playlist.id == "one"
            tracks = app.query_one("#tracks", NavigationTable)
            assert tracks.row_count == 3
            assert str(tracks.get_cell("0", "1")) == "♫"
            assert str(tracks.get_cell("2", "1")) == "♫"
            await pilot.press("j")
            assert tracks.cursor_row == 1
            assert str(tracks.get_cell("1", "0")) == "▸"
            assert str(tracks.get_cell("0", "1")) == "♫"
            await pilot.press("enter")
            await settle(app)
            assert "unavailable" in text(app, "#library-message")
            assert not stub.requests("/v1/me/player/play", "PUT")
            await pilot.press("tab")
            assert app.focused.id == "playlists"
            await pilot.press("shift+tab")
            assert app.focused.id == "tracks"
            await pilot.press("escape")
            assert app.focused.id == "playlists"

    asyncio.run(run())


def test_pagination_cache_and_playback_use_original_occurrence_offset():
    async def run():
        stub = SpotifyStub()
        stub.playlists = [playlist(str(i)) for i in range(51)]
        stub.items["0"] = [track() if i not in (1, 50) else None for i in range(53)]
        app = app_for(stub)
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.press("r")
            await settle(app)
            playlists = app.query_one("#playlists", NavigationTable)
            assert playlists.row_count == 51
            assert len(stub.requests("/v1/me/playlists")) == 1
            playlists.move_cursor(row=50)
            await pilot.press("enter")
            await settle(app)
            assert len(stub.requests("/v1/me/playlists")) == 2
            assert stub.requests("/v1/me/playlists")[-1].url.params["offset"] == "50"
            assert playlists.row_count == 51
            playlists.move_cursor(row=0)
            await pilot.press("enter")
            await settle(app)
            tracks = app.query_one("#tracks", NavigationTable)
            assert tracks.row_count == 51
            assert len(stub.requests("/v1/playlists/0/items")) == 1
            tracks.move_cursor(row=50)
            await pilot.press("enter")
            await settle(app)
            assert tracks.row_count == 53
            assert (
                stub.requests("/v1/playlists/0/items")[-1].url.params["offset"] == "50"
            )
            tracks.move_cursor(row=51)
            await pilot.press("enter")
            await settle(app)
            mutation = stub.requests("/v1/me/player/play", "PUT")
            assert len(mutation) == 1
            assert json.loads(mutation[0].content) == {
                "context_uri": "spotify:playlist:0",
                "offset": {"position": 51},
            }
            await pilot.press("escape", "enter")
            await settle(app)
            assert tracks.row_count == 53
            assert len(stub.requests("/v1/playlists/0/items")) == 2

    asyncio.run(run())


@pytest.mark.parametrize("status", [403, 404, 429])
def test_playlist_failures_show_retryable_state_without_reauthorization(status):
    async def run():
        stub = SpotifyStub()
        stub.failures[("GET", "/v1/playlists/one/items")] = status
        app = app_for(stub)
        async with app.run_test() as pilot:
            await pilot.press("r")
            await settle(app)
            await pilot.press("enter")
            await settle(app)
            assert app.session.authorized
            assert len(app.screen_stack) == 1
            if status in (403, 404):
                assert "Playlist unavailable" in text(app, "#library-message")
            else:
                assert app.connection_state == "RATE LIMITED"
            assert not stub.requests("/v1/me/player/play")

    asyncio.run(run())


def test_empty_and_deleted_playlist_rows():
    async def run():
        stub = SpotifyStub()
        stub.playlists = []
        app = app_for(stub)
        async with app.run_test() as pilot:
            await pilot.press("r")
            await settle(app)
            assert "No playlists" in text(app, "#library-message")
            stub.playlists = [None]
            await pilot.press("r")
            await settle(app)
            await pilot.press("enter")
            assert "unavailable" in text(app, "#library-message")

    asyncio.run(run())


def test_stale_track_response_cannot_overwrite_a_new_playlist():
    async def run():
        stub = SpotifyStub()
        started, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            if request.url.path == "/v1/playlists/one/items":
                started.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
            return stub(request)

        app = app_for(handler)
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.press("r")
            await settle(app)
            await pilot.press("enter")
            await started.wait()
            await pilot.press("escape", "j", "enter")
            release.set()
            await settle(app)
            browser = app.query_one(LibraryBrowser)
            assert browser.playlist.id == "two"
            assert browser.tracks[0][1].item.name == "Second playlist"
            assert "[red]two[/red]" in text(app, "#tracks-title")

    asyncio.run(run())


def test_device_selection_transfer_and_dialog_key_scoping():
    async def run():
        stub = SpotifyStub()
        app = app_for(stub)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("r")
            await settle(app)
            await pilot.press("d")
            await settle(app)
            assert isinstance(app.screen, DeviceScreen)
            devices = app.screen.query_one("#devices", NavigationTable)
            assert str(devices.get_cell("0", "0")) == "●"
            await pilot.press("space", "n", "p", "l", "d")
            assert not [r for r in stub.calls if r.method != "GET"]
            devices.move_cursor(row=2)
            await pilot.press("enter")
            assert isinstance(app.screen, DeviceScreen)
            assert "cannot accept" in str(
                app.screen.query_one("#device-message", PlainText).render()
            )
            devices.move_cursor(row=1)
            await pilot.press("enter")
            await settle(app)
            assert len(app.screen_stack) == 1
            assert app.target_device_id == "speaker"
            assert json.loads(stub.requests("/v1/me/player", "PUT")[0].content) == {
                "device_ids": ["speaker"],
                "play": False,
            }
            assert app.query_one(LibraryBrowser).playlist is None
            await pilot.press("space")
            await settle(app)
            assert (
                stub.requests("/v1/me/player/pause", "PUT")[-1].url.params["device_id"]
                == "speaker"
            )
            await pilot.press("question_mark")
            assert isinstance(app.screen, HelpScreen)
            await app.refresh_player()
            assert app.is_running
            await pilot.press("q")
            assert app.is_running and len(app.screen_stack) == 1

    asyncio.run(run())


@pytest.mark.parametrize("failure", [403, 404, 429, httpx.ReadTimeout("ambiguous")])
def test_failed_device_transfer_is_not_retried_and_keeps_previous_target(failure):
    async def run():
        stub = SpotifyStub()
        stub.failures[("PUT", "/v1/me/player")] = failure
        app = app_for(stub)
        app.target_device_id = "desktop"
        async with app.run_test() as pilot:
            await pilot.press("d")
            await settle(app)
            app.screen.query_one("#devices", NavigationTable).move_cursor(row=1)
            await pilot.press("enter")
            await settle(app)
            assert app.target_device_id == "desktop"
            assert len(stub.requests("/v1/me/player", "PUT")) == 1
            assert not stub.requests("/v1/me/player/play", "PUT")
            assert "Playback command sent" not in text(app, "#status")

    asyncio.run(run())


def test_no_devices_and_device_read_error_do_not_crash_overlay():
    async def run():
        stub = SpotifyStub()
        stub.devices = []
        app = app_for(stub)
        async with app.run_test() as pilot:
            await pilot.press("d")
            await settle(app)
            assert "No devices" in str(
                app.screen.query_one("#device-message", PlainText).render()
            )
            stub.failures[("GET", "/v1/me/player/devices")] = 500
            await pilot.press("r")
            await settle(app)
            assert "(500)" in str(
                app.screen.query_one("#device-message", PlainText).render()
            )
            await pilot.press("escape")
            assert app.is_running

    asyncio.run(run())


@pytest.mark.parametrize("size", [(120, 35), (80, 24), (40, 12)])
def test_compact_pane_switching_keeps_navigation_and_playback_visible(size):
    async def run():
        app = app_for(SpotifyStub())
        async with app.run_test(size=size) as pilot:
            await pilot.press("r")
            await settle(app)
            await pilot.press("enter")
            await settle(app)
            for selector in ("#tracks", "#track", "#device", "#progress", "#status"):
                widget = app.query_one(selector)
                assert widget.region.width > 0 and widget.region.height > 0
                assert (
                    widget.region.right <= size[0] and widget.region.bottom <= size[1]
                ), selector
            if size[0] == 40:
                assert not app.query_one("#playlists-pane").display
                assert app.query_one("#tracks-pane").display
                table = app.query_one("#tracks", NavigationTable)
                assert str(table.get_cell("0", "3")) == "Track"
                assert str(table.get_cell("0", "4")) == "Artist"
                assert str(table.get_cell("0", "5")) == "2:05"
            await pilot.press("escape")
            assert app.focused.id == "playlists"
            assert app.query_one("#playlists-pane").display
            await pilot.press("tab")
            assert app.focused.id == "tracks"

    asyncio.run(run())


def test_focused_text_input_does_not_trigger_application_commands():
    async def run():
        stub = SpotifyStub()
        app = app_for(stub)
        async with app.run_test() as pilot:
            field = Input()
            await app.screen.mount(field)
            field.focus()
            await pilot.press("q", "n", "p", "d", "l", "r", "space", "question_mark")
            assert field.value == "qnpdlr ?"
            assert app.is_running and len(app.screen_stack) == 1
            assert all(
                request.method == "GET" and request.url.path == "/v1/me/player"
                for request in stub.calls
            )

    asyncio.run(run())


@pytest.mark.parametrize("failure", [403, 404, 429, httpx.ReadTimeout("ambiguous")])
def test_rejected_context_playback_never_falls_back_or_retries(failure):
    async def run():
        stub = SpotifyStub()
        stub.failures[("PUT", "/v1/me/player/play")] = failure
        app = app_for(stub)
        async with app.run_test() as pilot:
            await pilot.press("r")
            await settle(app)
            await pilot.press("enter")
            await settle(app)
            await pilot.press("enter")
            await settle(app)
            calls = stub.requests("/v1/me/player/play", "PUT")
            assert len(calls) == 1
            assert json.loads(calls[0].content) == {
                "context_uri": "spotify:playlist:one",
                "offset": {"position": 0},
            }
            assert app.session.authorized
            assert "Playback command sent" not in text(app, "#status")

    asyncio.run(run())


def test_device_disappearing_does_not_silently_change_target():
    async def run():
        stub = SpotifyStub()
        app = app_for(stub)
        app.target_device_id = "speaker"
        stub.failures[("PUT", "/v1/me/player/play")] = 404
        async with app.run_test() as pilot:
            await pilot.press("space")
            await settle(app)
            calls = stub.requests("/v1/me/player/play", "PUT")
            assert len(calls) == 1
            assert calls[0].url.params["device_id"] == "speaker"
            assert app.target_device_id == "speaker"
            assert "(404)" in text(app, "#status")

    asyncio.run(run())


def test_closing_device_overlay_cancels_its_pending_read():
    async def run():
        stub = SpotifyStub()
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def handler(request):
            if request.url.path == "/v1/me/player/devices":
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            return stub(request)

        app = app_for(handler)
        async with app.run_test() as pilot:
            await pilot.press("d")
            await started.wait()
            await pilot.press("escape")
            await settle(app)
            assert cancelled.is_set() and app.is_running
            assert len(app.screen_stack) == 1

    asyncio.run(run())


def test_direct_focus_survives_resize_into_compact_layout():
    async def run():
        app = app_for(SpotifyStub())
        async with app.run_test(size=(120, 35)) as pilot:
            app.query_one("#tracks").focus()
            await pilot.pause()
            await pilot.resize_terminal(40, 12)
            await pilot.pause()
            assert app.query_one("#tracks-pane").display
            assert not app.query_one("#playlists-pane").display
            assert app.focused.id == "tracks"

    asyncio.run(run())


def test_reopening_same_playlist_during_read_restarts_loading():
    async def run():
        stub = SpotifyStub()
        started = asyncio.Event()
        attempts = 0

        async def handler(request):
            nonlocal attempts
            if request.url.path == "/v1/playlists/one/items":
                attempts += 1
                if attempts == 1:
                    started.set()
                    await asyncio.Event().wait()
            return stub(request)

        app = app_for(handler)
        async with app.run_test() as pilot:
            await pilot.press("r")
            await settle(app)
            await pilot.press("enter")
            await started.wait()
            await pilot.press("escape", "enter")
            await settle(app)
            assert attempts == 2
            assert app.query_one(LibraryBrowser).tracks

    asyncio.run(run())


def test_playlist_network_failure_can_retry_and_empty_tracks_are_visible():
    async def run():
        stub = SpotifyStub()
        stub.failures[("GET", "/v1/playlists/one/items")] = httpx.ReadTimeout("test")
        app = app_for(stub)
        async with app.run_test() as pilot:
            await pilot.press("r")
            await settle(app)
            await pilot.press("enter")
            await settle(app)
            assert app.connection_state == "OFFLINE"
            assert "retry" in text(app, "#library-message")
            del stub.failures[("GET", "/v1/playlists/one/items")]
            stub.items["one"] = []
            await pilot.press("r")
            await settle(app)
            assert "No tracks" in text(app, "#library-message")
            assert app.session.authorized

    asyncio.run(run())


def test_new_login_clears_library_cache_and_device_target(monkeypatch):
    from unittest.mock import AsyncMock

    async def run():
        monkeypatch.setenv("SPOTIFY_CLIENT_ID", "test-client")
        stub = SpotifyStub()
        app = app_for(stub)

        async def login(uri):
            app.session._install(Token(access_token="new-test-access", expires_in=3600))

        monkeypatch.setattr(app.session, "login", AsyncMock(side_effect=login))
        async with app.run_test() as pilot:
            await pilot.pause()
            await settle(app)
            assert app.query_one(LibraryBrowser).playlists
            await pilot.press("enter")
            await settle(app)
            assert "one" in app.library.tracks
            app.target_device_id = "speaker"
            stub.playlists = [playlist("new-account")]
            await pilot.press("l")
            await settle(app)
            browser = app.query_one(LibraryBrowser)
            assert browser.playlists[0][1].id == "new-account"
            assert browser.playlist is None and not browser.tracks
            assert not app.library.tracks
            assert app.target_device_id is None
            assert text(app, "#tracks-title") == "TRACKS"

    asyncio.run(run())

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from textual.widgets import Input

from obelisk_music.app import ObeliskApp
from obelisk_music.browser import CatalogTable
from obelisk_music.config import Settings
from obelisk_music.models import Album, Page, Token, Track
from obelisk_music.screens import CatalogScreen, DeviceScreen, HelpScreen
from obelisk_music.widgets import NowPlaying, PlainText


def song(name="[red]Track[/red]", **updates):
    return {
        "name": name,
        "uri": "spotify:track:one",
        "duration_ms": 120000,
        "artists": [{"name": "Artist", "id": "artist"}],
        "album": {"name": "Album", "id": "album"},
        **updates,
    }


class CatalogStub:
    def __init__(self):
        self.calls = []
        self.failure = None

    def __call__(self, request):
        self.calls.append(request)
        path = request.url.path
        if request.method != "GET":
            return httpx.Response(204)
        if path == "/v1/me/player":
            return httpx.Response(
                200,
                json={
                    "item": song(),
                    "progress_ms": 1000,
                    "is_playing": True,
                    "device": {"name": "Desktop"},
                },
            )
        if path == "/v1/me/player/devices":
            return httpx.Response(200, json={"devices": []})
        if self.failure and (path.startswith("/v1/albums") or path == "/v1/search"):
            if isinstance(self.failure, Exception):
                raise self.failure
            return httpx.Response(self.failure, headers={"Retry-After": "60"})
        if path == "/v1/albums/album":
            return httpx.Response(
                200,
                json={
                    "id": "album",
                    "name": "Album",
                    "release_date": "2026-01-01",
                    "total_tracks": 11,
                    "artists": [{"id": "artist", "name": "Artist"}],
                },
            )
        if path == "/v1/artists/artist":
            return httpx.Response(200, json={"id": "artist", "name": "Artist"})
        offset = int(request.url.params.get("offset", 0))
        limit = int(request.url.params.get("limit", 10))
        if path == "/v1/search":
            kind = request.url.params["type"]
            item = song() if kind == "track" else {"id": kind, "name": kind.title()}
            payload = {
                "offset": offset,
                "limit": limit,
                "total": 11,
                "items": [item] * min(limit, 11 - offset),
            }
            return httpx.Response(200, json={kind + "s": payload})
        if path == "/v1/artists/artist/albums":
            item = {"id": "album", "name": "Album"}
        elif path == "/v1/albums/album/tracks":
            item = song()
        else:
            return httpx.Response(
                200, json={"offset": 0, "limit": 50, "total": 0, "items": []}
            )
        return httpx.Response(
            200,
            json={
                "offset": offset,
                "limit": limit,
                "total": 11,
                "items": [item] * min(limit, 11 - offset),
            },
        )

    def searches(self):
        return [request for request in self.calls if request.url.path == "/v1/search"]


def make_app(stub, theme="midnight", vim=True):
    app = ObeliskApp(
        settings=Settings(appearance={"theme": theme}, interface={"vim_keys": vim}),
        http=httpx.AsyncClient(transport=httpx.MockTransport(stub)),
    )
    app.session._install(Token(access_token="test-access", expires_in=3600))
    return app


async def settle(app):
    await asyncio.sleep(0.03)
    async with asyncio.timeout(5):
        while any(not worker.is_finished for worker in app.workers):
            await asyncio.sleep(0.01)


async def search(app, pilot, query="music"):
    await pilot.press("slash")
    assert isinstance(app.screen, CatalogScreen)
    app.screen.query_one(Input).value = query
    await pilot.pause(0.4)
    await settle(app)
    return app.screen


def text(screen, selector):
    return str(screen.query_one(selector, PlainText).render())


def test_search_debounces_and_scopes_typing_shortcuts():
    async def run():
        stub, app = CatalogStub(), None
        app = make_app(stub)
        async with app.run_test() as pilot:
            await pilot.press("slash")
            entry = app.screen.query_one(Input)
            entry.value = "first"
            await pilot.pause(0.05)
            entry.value = "second"
            await pilot.pause(0.05)
            assert not stub.searches()
            entry.value = "final"
            await pilot.pause(0.4)
            await settle(app)
            assert [r.url.params["q"] for r in stub.searches()] == ["final"]
            await pilot.press(
                "q", "n", "p", "d", "r", "space", "question_mark", "a", "A"
            )
            assert app.is_running and app.focused is entry
            assert not any(r.method != "GET" for r in stub.calls)
            assert "qnpdr" in entry.value
            await pilot.press("escape")
            assert app.focused.id == "playlists"

    asyncio.run(run())


@pytest.mark.parametrize("size", [(120, 35), (80, 24), (60, 20), (40, 12)])
@pytest.mark.parametrize("theme", ["midnight", "mono"])
def test_search_responsive_navigation_pagination_and_focus(size, theme):
    async def run():
        stub = CatalogStub()
        app = make_app(stub, theme)
        async with app.run_test(size=size) as pilot:
            overlay = await search(app, pilot)
            table = overlay.query_one(CatalogTable)
            assert len(overlay.entries) == 10 and overlay.more
            assert app.theme == theme
            for selector in (
                "#search-input",
                "#catalog-title",
                "#catalog-results",
                "#catalog-message",
                "#catalog-hint",
            ):
                region = overlay.query_one(selector).region
                assert region.width > 0 and region.height > 0
                assert region.x >= 0 and region.right <= size[0]
                assert region.y >= 0 and region.bottom <= size[1] - 5
            assert table.region.height >= 2
            base = app.screen_stack[0]
            assert base.query_one(NowPlaying).region.y >= table.region.bottom
            await pilot.press("down")
            assert app.focused is table
            await pilot.press("j")
            assert table.cursor_row == 1
            await pilot.press("k")
            assert table.cursor_row == 0
            cell = table.get_cell("0", "1")
            assert cell.plain == "[red]Track[/red]" or cell.plain.startswith("[red]")
            assert all(span.style.color is None for span in cell.spans)
            assert any(
                span.style.link == "https://open.spotify.com/track/one"
                for span in cell.spans
            )
            table.move_cursor(row=10)
            await pilot.press("enter")
            await settle(app)
            assert len(overlay.entries) == 11 and not overlay.more
            assert stub.searches()[-1].url.params["offset"] == "10"
            await pilot.press("tab")
            assert isinstance(app.focused, Input)
            await pilot.press("shift+tab")
            assert app.focused is table
            await pilot.press("escape")
            assert app.focused.id == "playlists"

    asyncio.run(run())


def test_album_artist_flow_back_restores_selection_and_search_cache():
    async def run():
        stub = CatalogStub()
        app = make_app(stub)
        async with app.run_test() as pilot:
            overlay = await search(app, pilot)
            await pilot.press("f3", "down", "j", "enter")
            await settle(app)
            assert app.screen.mode == "artist"
            assert "Artist" in text(app.screen, "#catalog-metadata")
            assert app.screen.more, (
                text(app.screen, "#catalog-message"),
                [(str(r.url)) for r in stub.calls],
            )
            table = app.screen.query_one(CatalogTable)
            table.move_cursor(row=10)
            await pilot.press("enter")
            await settle(app)
            assert len(app.screen.entries) == 11
            table.move_cursor(row=0)
            await pilot.press("enter")
            await settle(app)
            assert app.screen.mode == "album"
            assert "2026-01-01" in text(app.screen, "#catalog-metadata")
            await pilot.press("escape")
            assert app.screen.mode == "artist" and app.focused is table
            await pilot.press("escape")
            assert (
                app.screen is overlay
                and overlay.query_one(CatalogTable).cursor_row == 1
            )
            count = len(stub.searches())
            await pilot.press("f1", "f3")
            await settle(app)
            assert len(stub.searches()) == count

    asyncio.run(run())


@pytest.mark.parametrize("album", [False, True])
def test_enter_plays_search_track_or_album_context_once(album):
    async def run():
        stub = CatalogStub()
        app = make_app(stub)
        async with app.run_test() as pilot:
            if album:
                app._open_catalog(Album(id="album", name="Album"))
                await pilot.pause()
                await settle(app)
            else:
                await search(app, pilot)
                await pilot.press("down")
            app.screen.query_one(CatalogTable).move_cursor(row=1)
            await pilot.press("enter")
            await settle(app)
            commands = [r for r in stub.calls if r.method == "PUT"]
            assert len(commands) == 1
            expected = (
                {"context_uri": "spotify:album:album", "offset": {"position": 1}}
                if album
                else {"uris": ["spotify:track:one"]}
            )
            assert json.loads(commands[0].content) == expected
            assert app.screen.mode == ("album" if album else "search")

    asyncio.run(run())


@pytest.mark.parametrize("failure", [403, 404, 429, 500, httpx.ReadTimeout("offline")])
def test_search_failure_is_actionable_and_does_not_force_reauthentication(failure):
    async def run():
        stub = CatalogStub()
        stub.failure = failure
        app = make_app(stub)
        async with app.run_test() as pilot:
            app._connection("CONNECTED")
            overlay = await search(app, pilot)
            message = text(overlay, "#catalog-message")
            assert "retry" in message or "retries" in message
            assert app.session.authorized
            if failure in (403, 404):
                assert app.connection_state == "CONNECTED"
                assert "restricted" in message
            if failure == 429:
                await pilot.press("down", "r")
                await settle(app)
                assert len(stub.searches()) == 1

    asyncio.run(run())


def test_superseded_search_and_closed_overlay_reject_stale_results(monkeypatch):
    async def run():
        app = make_app(CatalogStub())
        started, release = asyncio.Event(), asyncio.Event()

        async def delayed(query, *args):
            if query == "old":
                started.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
            return Page[Track | None](
                offset=0,
                limit=10,
                total=1,
                items=[Track(name=query, uri="spotify:track:" + query)],
            )

        monkeypatch.setattr(app.spotify, "search", delayed)
        async with app.run_test() as pilot:
            await pilot.press("slash")
            overlay = app.screen
            overlay.query_one(Input).value = "old"
            await asyncio.wait_for(started.wait(), 2)
            overlay.query_one(Input).value = "new"
            await pilot.pause(0.4)
            assert overlay.entries[0][1].name == "new"
            release.set()
            await settle(app)
            assert overlay.entries[0][1].name == "new"
            overlay.query_one(Input).value = "old"
            release.clear()
            await pilot.pause(0.4)
            await pilot.press("escape")
            release.set()
            await settle(app)
            assert app.focused.id == "playlists"

    asyncio.run(run())


def test_search_empty_and_unavailable_rows_do_not_play(monkeypatch):
    async def run():
        stub = CatalogStub()
        app = make_app(stub)
        monkeypatch.setattr(
            app.spotify,
            "search",
            AsyncMock(
                return_value=Page[Track | None](offset=0, limit=10, total=0, items=[])
            ),
        )
        async with app.run_test() as pilot:
            overlay = await search(app, pilot)
            assert "No results" in text(overlay, "#catalog-message")
            overlay.show_items(
                [
                    (0, None),
                    (
                        1,
                        Track(
                            name="Restricted", uri="spotify:track:x", is_playable=False
                        ),
                    ),
                ],
                False,
            )
            await pilot.press("down", "enter", "j", "enter")
            assert "unavailable" in text(overlay, "#catalog-message")
            assert not any(r.method != "GET" for r in stub.calls)

    asyncio.run(run())


def test_catalog_dialog_focus_restoration_and_vim_setting():
    async def run():
        app = make_app(CatalogStub(), vim=False)
        async with app.run_test() as pilot:
            overlay = await search(app, pilot)
            await pilot.press("down", "j")
            table = overlay.query_one(CatalogTable)
            assert table.cursor_row == 0
            await pilot.press("down", "question_mark")
            assert isinstance(app.screen, HelpScreen)
            await pilot.press("escape")
            assert app.focused is table and table.cursor_row == 1
            await pilot.press("d")
            await settle(app)
            assert isinstance(app.screen, DeviceScreen)
            await pilot.press("escape")
            assert app.screen is overlay and app.focused is table

    asyncio.run(run())


def test_selected_track_album_and_artist_navigation():
    async def run():
        app = make_app(CatalogStub())
        async with app.run_test() as pilot:
            await search(app, pilot)
            await pilot.press("down", "a")
            await settle(app)
            assert app.screen.mode == "album"
            await pilot.press("escape", "A")
            await settle(app)
            assert app.screen.mode == "artist"

    asyncio.run(run())


def test_browsing_and_closing_views_do_not_cancel_or_repeat_playback(monkeypatch):
    async def run():
        app = make_app(CatalogStub())
        started, release = asyncio.Event(), asyncio.Event()
        calls = 0
        cancelled = False

        async def delayed(*args, **kwargs):
            nonlocal calls, cancelled
            calls += 1
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled = True
                raise

        monkeypatch.setattr(app.spotify, "play_album", delayed)
        async with app.run_test(size=(40, 12)) as pilot:
            app._open_catalog(Album(id="album", name="Album"))
            await pilot.pause()
            await settle(app)
            await pilot.press("enter")
            await asyncio.wait_for(started.wait(), 1)
            await pilot.press("enter", "escape", "slash")
            assert isinstance(app.screen, CatalogScreen) and app.screen.mode == "search"
            assert not cancelled and calls == 1
            release.set()
            await settle(app)
            assert calls == 1

    asyncio.run(run())


def test_album_access_denied_and_metadata_missing_are_recoverable():
    async def run():
        stub = CatalogStub()
        stub.failure = 403
        app = make_app(stub)
        async with app.run_test() as pilot:
            app._open_catalog(Album(id="album", name="Album"))
            await pilot.pause()
            await settle(app)
            assert "restricted" in text(app.screen, "#catalog-message")
            assert app.session.authorized
            await pilot.press("escape")
            assert app.focused.id == "playlists"

    asyncio.run(run())


def test_theme_stylesheet_uses_only_semantic_colors():
    from importlib.resources import files

    stylesheet = files("obelisk_music").joinpath("obelisk.tcss").read_text()
    import re

    assert not re.search(r"#[0-9a-fA-F]{6}\b", stylesheet)

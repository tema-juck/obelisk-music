"""Exercise search-to-player behavior through mocked HTTP and real key events."""

import asyncio
import json

import httpx
import pytest
from textual.widgets import Input

from obelisk_music.app import ObeliskApp
from obelisk_music.browser import CatalogTable
from obelisk_music.config import Settings
from obelisk_music.models import Token
from obelisk_music.screens import CatalogScreen
from obelisk_music.widgets import NowPlaying, PlainText


class SearchPlayback:
    def __init__(self):
        self.calls = []
        self.playing = None
        self.failure = None
        self.tracks = [
            {
                "id": f"{index:022d}",
                "uri": f"spotify:track:{index:022d}",
                "name": f"Track {index}",
                "type": "track",
                "artists": [{"id": "artist", "name": "Artist"}],
                "album": {"id": "album", "name": "Album", "type": "album"},
                "duration_ms": 180000,
                "is_playable": True,
                "is_local": False,
                "track_number": index % 3 + 1,
                "disc_number": index // 3 + 1,
            }
            for index in range(12)
        ]
        self.album_tracks = list(reversed(self.tracks))

    def __call__(self, request):
        self.calls.append(request)
        path = request.url.path
        if request.method == "PUT":
            assert path == "/v1/me/player/play"
            if isinstance(self.failure, Exception):
                raise self.failure
            if self.failure:
                return httpx.Response(self.failure, headers={"Retry-After": "60"})
            body = json.loads(request.content)
            if "uris" in body:
                # Reported Spotify client regression: 204 without starting playback.
                self.playing = None
            else:
                assert body["context_uri"] == "spotify:album:album"
                offset = body["offset"]
                self.playing = (
                    next(t for t in self.album_tracks if t["uri"] == offset["uri"])
                    if "uri" in offset
                    else self.album_tracks[offset["position"]]
                )
            return httpx.Response(204)
        if path == "/v1/me/player":
            return httpx.Response(
                200,
                json={
                    "item": self.playing,
                    "is_playing": self.playing is not None,
                    "progress_ms": 0,
                    "device": {"id": "desktop", "name": "Desktop"},
                },
            )
        if path == "/v1/albums/album":
            return httpx.Response(200, json={"id": "album", "name": "Album"})
        if path == "/v1/artists/artist":
            return httpx.Response(200, json={"id": "artist", "name": "Artist"})
        offset = int(request.url.params.get("offset", 0))
        kind = request.url.params.get("type", "track")
        rows = self.tracks if kind == "track" else [{"id": kind, "name": kind.title()}]
        if path.endswith("/albums"):
            rows = [{"id": "album", "name": "Album"}]
        elif path.endswith("/tracks"):
            rows = self.album_tracks
        limit = int(request.url.params.get("limit", 10))
        payload = {
            "offset": offset,
            "limit": limit,
            "total": len(rows),
            "items": rows[offset : offset + limit],
        }
        return httpx.Response(
            200, json={kind + "s": payload} if path == "/v1/search" else payload
        )

    @property
    def mutations(self):
        return [r for r in self.calls if r.method != "GET"]


def app_for(api):
    app = ObeliskApp(
        settings=Settings(), http=httpx.AsyncClient(transport=httpx.MockTransport(api))
    )
    app.session._install(Token(access_token="mock-access", expires_in=3600))
    return app


async def settle(app):
    await asyncio.sleep(0.03)
    async with asyncio.timeout(5):
        while any(not worker.is_finished for worker in app.workers):
            await asyncio.sleep(0.01)


async def open_search(app, pilot):
    await pilot.press("slash")
    app.screen.query_one(Input).value = "track"
    await pilot.pause(0.4)
    await settle(app)
    return app.screen


@pytest.mark.parametrize("position", [0, 2, 11])
@pytest.mark.parametrize("device_id", [None, "speaker"])
def test_f1_enter_plays_selected_uri_and_updates_now_playing(position, device_id):
    async def run():
        api = SearchPlayback()
        app = app_for(api)
        app.target_device_id = device_id
        async with app.run_test() as pilot:
            overlay = await open_search(app, pilot)
            await pilot.press("f1", "down")
            await settle(app)
            table = overlay.query_one(CatalogTable)
            if position >= 10:
                table.move_cursor(row=10)
                await pilot.press("enter")
                await settle(app)
            table.move_cursor(row=position)
            await pilot.press("enter")
            await settle(app)
            assert len(api.mutations) == 1
            assert (
                app.screen_stack[0].query_one(NowPlaying).playing_uri
                == api.tracks[position]["uri"]
            )
            assert json.loads(api.mutations[0].content) == {
                "context_uri": "spotify:album:album",
                "offset": {"uri": api.tracks[position]["uri"]},
            }
            assert api.mutations[0].url.params.get("device_id") == device_id
            assert app.screen is overlay and app.focused is table
            assert table.cursor_row == position
            assert app.is_playing

    asyncio.run(run())


def test_search_input_enter_only_focuses_results_without_playback():
    async def run():
        api = SearchPlayback()
        app = app_for(api)
        async with app.run_test() as pilot:
            overlay = await open_search(app, pilot)
            assert isinstance(app.focused, Input)
            await pilot.press("enter")
            assert app.focused is overlay.query_one(CatalogTable)
            assert not api.mutations

    asyncio.run(run())


@pytest.mark.parametrize(
    "data",
    [{"is_playable": False}, {"uri": None}, {"restrictions": {"reason": "market"}}],
)
def test_unavailable_f1_results_do_not_dispatch_playback(data):
    async def run():
        api = SearchPlayback()
        api.tracks[2].update(data)
        app = app_for(api)
        async with app.run_test() as pilot:
            overlay = await open_search(app, pilot)
            await pilot.press("down")
            overlay.query_one(CatalogTable).move_cursor(row=2)
            await pilot.press("enter")
            assert "unavailable" in str(
                overlay.query_one("#catalog-message", PlainText).render()
            )
            assert not api.mutations

    asyncio.run(run())


@pytest.mark.parametrize("category", ["f2", "f3"])
def test_album_and_artist_search_retain_album_context_and_original_offset(category):
    async def run():
        api = SearchPlayback()
        app = app_for(api)
        async with app.run_test() as pilot:
            await open_search(app, pilot)
            await pilot.press(category, "down", "enter")
            await settle(app)
            if category == "f3":
                await pilot.press("enter")
                await settle(app)
            assert isinstance(app.screen, CatalogScreen) and app.screen.mode == "album"
            app.screen.query_one(CatalogTable).move_cursor(row=2)
            await pilot.press("enter")
            await settle(app)
            assert len(api.mutations) == 1
            assert json.loads(api.mutations[0].content) == {
                "context_uri": "spotify:album:album",
                "offset": {"position": 2},
            }

    asyncio.run(run())


@pytest.mark.parametrize("album", [None, {"name": "Missing ID"}])
def test_f1_missing_album_context_shows_guidance_without_guessing_a_track(album):
    async def run():
        api = SearchPlayback()
        api.tracks[2]["album"] = album
        app = app_for(api)
        async with app.run_test() as pilot:
            overlay = await open_search(app, pilot)
            await pilot.press("down")
            overlay.query_one(CatalogTable).move_cursor(row=2)
            await pilot.press("enter")
            await settle(app)
            assert not api.mutations
            assert "F2" in str(
                app.screen_stack[0].query_one("#status", PlainText).render()
            )
            assert app.screen is overlay

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure,hint",
    [
        (400, "refresh"),
        (403, "restrictions"),
        (404, "open Spotify"),
        (429, "retry"),
        (httpx.ReadTimeout("ambiguous mutation"), "connection failed"),
    ],
)
def test_f1_playback_rejection_or_ambiguity_has_feedback_and_never_falls_back(
    failure, hint
):
    async def run():
        api = SearchPlayback()
        api.failure = failure
        app = app_for(api)
        async with app.run_test() as pilot:
            overlay = await open_search(app, pilot)
            await pilot.press("down")
            table = overlay.query_one(CatalogTable)
            table.move_cursor(row=2)
            await pilot.press("enter")
            await settle(app)
            assert len(api.mutations) == 1
            assert json.loads(api.mutations[0].content) == {
                "context_uri": "spotify:album:album",
                "offset": {"uri": api.tracks[2]["uri"]},
            }
            feedback = str(app.screen_stack[0].query_one("#status", PlainText).render())
            assert hint in feedback
            assert app.session.authorized
            assert (
                app.screen is overlay and app.focused is table and table.cursor_row == 2
            )
            if failure == 429:
                await pilot.press("enter")
                await settle(app)
                assert len(api.mutations) == 1

    asyncio.run(run())

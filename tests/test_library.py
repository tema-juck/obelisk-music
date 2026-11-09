import asyncio
import json
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from obelisk_music import auth
from obelisk_music.library import SpotifyLibrary
from obelisk_music.models import Page, Playlist, PlaylistEntry, Token
from obelisk_music.session import SpotifySession
from obelisk_music.spotify import SpotifyClient, SpotifyError


def client(http):
    session = SpotifySession("test-client", http)
    session._install(Token(access_token="test-access", expires_in=3600))
    return SpotifyClient(session, http)


def page(items, offset=0, total=None):
    return {
        "offset": offset,
        "limit": 50,
        "total": len(items) + offset if total is None else total,
        "items": items,
    }


def track(name="Track", **kwargs):
    return {
        "item": {
            "name": name,
            "uri": "spotify:track:same",
            "duration_ms": 1000,
            **kwargs,
        }
    }


@pytest.mark.parametrize("count_field", ["items", "tracks"])
def test_playlist_offset_is_independent_and_counts_are_optional(count_field):
    async def run():
        def handler(request):
            assert request.url.path == "/v1/me/playlists"
            assert request.url.params["offset"] == "7"
            assert request.url.params["limit"] == "50"
            return httpx.Response(
                200,
                json=page(
                    [
                        {
                            "id": "one",
                            "name": "One",
                            "owner": {"display_name": None, "id": "owner"},
                            count_field: {"total": 9},
                        },
                        None,
                        {"id": "two", "name": "Two"},
                    ],
                    offset=7,
                ),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await client(http).playlists(7)
            assert result.items[0].items.total == 9
            assert result.items[0].owner.display_name is None
            assert result.items[1] is None
            assert result.items[2].items is None

    asyncio.run(run())


def test_track_pages_keep_null_entries_duplicates_and_original_positions():
    async def run():
        calls = []

        def handler(request):
            calls.append(request)
            assert request.url.path == "/v1/playlists/one/items"
            offset = int(request.url.params["offset"])
            items = (
                [track(), {"item": None}]
                if offset == 0
                else [None, track(), track(is_playable=False), track(is_local=True)]
            )
            return httpx.Response(200, json=page(items, offset, total=6))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            library = SpotifyLibrary(client(http))
            await library.load_tracks("one")
            collection = library.tracks["one"]
            assert len(calls) == 1 and collection.next_offset == 2
            await library.load_tracks("one")
            assert [position for position, _ in collection.entries] == list(range(6))
            assert (
                collection.entries[0][1].item.uri == collection.entries[3][1].item.uri
            )
            assert collection.entries[2][1] is None
            assert collection.entries[0][1].playable
            assert not collection.entries[1][1].playable
            assert not collection.entries[4][1].playable
            assert not collection.entries[5][1].playable
            await library.load_tracks("one")
            assert len(calls) == 2 and collection.next_offset is None

    asyncio.run(run())


def test_playlist_pages_are_cached_until_refresh_and_empty_library_stops():
    async def run():
        calls = []

        def handler(request):
            calls.append(request)
            offset = int(request.url.params["offset"])
            return httpx.Response(
                200,
                json=page([{"id": str(offset), "name": str(offset)}], offset, total=2),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            library = SpotifyLibrary(client(http))
            await library.load_playlists()
            await library.load_playlists()
            await library.load_playlists()
            assert len(calls) == 2 and len(library.playlists.entries) == 2
            library.clear()
            await library.load_playlists()
            assert len(calls) == 3 and len(library.playlists.entries) == 1
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=page([])))
        ) as http:
            library = SpotifyLibrary(client(http))
            await library.load_playlists()
            assert (
                library.playlists.entries == []
                and library.playlists.next_offset is None
            )

    asyncio.run(run())


def test_cache_clear_discards_inflight_results():
    async def run():
        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed(offset):
            started.set()
            await release.wait()
            return Page[Playlist | None].model_validate(
                page([{"id": "old", "name": "Old"}])
            )

        api = Mock()
        api.playlists = AsyncMock(side_effect=delayed)
        library = SpotifyLibrary(api)
        task = asyncio.create_task(library.load_playlists())
        await started.wait()
        library.clear()
        release.set()
        await task
        assert library.playlists.entries == []

    asyncio.run(run())


@pytest.mark.parametrize("status", [403, 404, 429])
def test_playlist_errors_remain_recoverable_and_rate_limits_are_shared(status):
    async def run():
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(status, headers={"Retry-After": "60"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            api = client(http)
            with pytest.raises(SpotifyError) as exc:
                await api.playlist_items("one")
            assert exc.value.status_code == status and api.session.authorized
            if status == 404:
                assert "resource" in str(exc.value)
            if status == 429:
                with pytest.raises(SpotifyError):
                    await api.devices()
                assert len(calls) == 1

    asyncio.run(run())


def test_playlist_401_recovers_through_existing_session(monkeypatch):
    async def run():
        monkeypatch.setattr(
            auth.keyring, "get_password", Mock(return_value="test-refresh")
        )
        calls = []

        def handler(request):
            calls.append(request)
            if request.url == auth.TOKEN_URL:
                return httpx.Response(
                    200, json={"access_token": "new-test-access", "expires_in": 3600}
                )
            if request.headers["Authorization"] == "Bearer test-access":
                return httpx.Response(401)
            return httpx.Response(200, json=page([]))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            assert (await client(http).playlists()).items == []
            assert len(calls) == 3

    asyncio.run(run())


def test_context_playback_and_transfer_have_no_fallback_or_network_retry():
    async def run():
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(204)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            api = client(http)
            await api.play_playlist("one", 51, "speaker")
            assert calls[-1].url.params["device_id"] == "speaker"
            assert json.loads(calls[-1].content) == {
                "context_uri": "spotify:playlist:one",
                "offset": {"position": 51},
            }
            await api.transfer("speaker")
            assert json.loads(calls[-1].content) == {
                "device_ids": ["speaker"],
                "play": False,
            }
            await api.pause("speaker")
            await api.play("speaker")
            await api.next("speaker")
            await api.previous("speaker")
            assert all(
                r.url.params.get("device_id") == "speaker"
                for r in calls
                if r.url.path != "/v1/me/player"
            )
        calls.clear()

        def failed(request):
            calls.append(request)
            raise httpx.ReadTimeout("ambiguous", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(failed)) as http:
            with pytest.raises(httpx.ReadTimeout):
                await client(http).play_playlist("one", 3)
            assert len(calls) == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "payload",
    [
        {"offset": 1, "limit": 50, "total": 1, "items": []},
        {
            "offset": 0,
            "limit": 50,
            "total": 1,
            "items": [{"item": {"duration_ms": -1}}],
        },
    ],
)
def test_invalid_or_misaligned_track_pages_are_rejected(payload):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
        ) as http:
            with pytest.raises(SpotifyError):
                await client(http).playlist_items("one")

    asyncio.run(run())


def test_playlist_permissions_and_legacy_item_shape():
    scopes = parse_qs(
        urlparse(
            auth.authorization_url(
                "id", "http://127.0.0.1:8765/callback", "verifier", "state"
            )
        ).query
    )["scope"][0].split()
    assert {"playlist-read-private", "playlist-read-collaborative"} <= set(scopes)
    assert PlaylistEntry.model_validate(
        {"track": {"uri": "spotify:track:one"}}
    ).playable
    assert not PlaylistEntry.model_validate(track(type="episode")).playable

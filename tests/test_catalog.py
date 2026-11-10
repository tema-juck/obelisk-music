import asyncio
import json
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from obelisk_music import auth
from obelisk_music.library import SpotifyLibrary
from obelisk_music.models import Album, Artist, Page, Token, Track
from obelisk_music.session import SpotifySession
from obelisk_music.spotify import SpotifyClient, SpotifyError


def client(http):
    session = SpotifySession("test-client", http)
    session._install(Token(access_token="test-access", expires_in=3600))
    return SpotifyClient(session, http)


def page(items, offset=0, total=None, limit=10):
    return {
        "offset": offset,
        "limit": limit,
        "total": total if total is not None else offset + len(items),
        "items": items,
    }


@pytest.mark.parametrize(
    "kind,model", [("track", Track), ("album", Album), ("artist", Artist)]
)
def test_search_uses_supported_limits_and_offsets(kind, model):
    async def run():
        def handler(request):
            assert request.url.path == "/v1/search"
            assert dict(request.url.params) == {
                "q": "a & б",
                "type": kind,
                "offset": "10",
                "limit": "10",
            }
            return httpx.Response(
                200, json={kind + "s": page([{"name": "[red]Plain"}, None], 10)}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await client(http).search("a & б", kind, 10)
            assert isinstance(result.items[0], model)
            assert result.items[1] is None

    asyncio.run(run())


@pytest.mark.parametrize(
    "key", [("album", "one"), ("artist", "one"), ("search", "query", "track")]
)
def test_catalog_pages_cache_positions_nulls_duplicates_and_stop(key):
    async def run():
        calls = []

        def handler(request):
            calls.append(request)
            offset = int(request.url.params["offset"])
            rows = (
                [{"name": "Same", "uri": "spotify:track:same"}, None]
                if offset == 0
                else [{"name": "Same", "uri": "spotify:track:same"}]
            )
            payload = page(rows, offset, total=3)
            return httpx.Response(
                200, json={"tracks": payload} if key[0] == "search" else payload
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            library = SpotifyLibrary(client(http))
            collection = await library.load_catalog(key)
            assert collection.next_offset == 2 and len(calls) == 1
            await library.load_catalog(key)
            await library.load_catalog(key)
            assert [position for position, _ in collection.entries] == [0, 1, 2]
            assert collection.entries[1][1] is None
            assert len(calls) == 2 and collection.next_offset is None
            library.clear()
            assert not library.catalog

    asyncio.run(run())


@pytest.mark.parametrize("mode,model", [("album", Album), ("artist", Artist)])
def test_metadata_optional_fields_and_session_cache(mode, model):
    async def run():
        calls = []

        def handler(request):
            calls.append(request)
            assert request.url.path == f"/v1/{mode}s/one"
            return httpx.Response(200, json={"id": "one", "name": "Name"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            library = SpotifyLibrary(client(http))
            assert isinstance(await library.load_metadata(mode, "one"), model)
            await library.load_metadata(mode, "one")
            assert len(calls) == 1

    asyncio.run(run())


@pytest.mark.parametrize("status", [403, 404, 429, 500])
def test_catalog_api_errors_and_shared_cooldown(status):
    async def run():
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(status, headers={"Retry-After": "60"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            api = client(http)
            with pytest.raises(SpotifyError) as exc:
                await api.search("query")
            assert exc.value.status_code == status
            assert api.session.authorized
            if status == 429:
                with pytest.raises(SpotifyError):
                    await api.album_tracks("one")
                assert len(calls) == 1

    asyncio.run(run())


def test_search_401_recovers_without_reauthorization(monkeypatch):
    async def run():
        monkeypatch.setattr(
            auth.keyring, "get_password", Mock(return_value="test-refresh")
        )

        def handler(request):
            if request.url == auth.TOKEN_URL:
                return httpx.Response(
                    200, json={"access_token": "new", "expires_in": 3600}
                )
            if request.headers["Authorization"] == "Bearer test-access":
                return httpx.Response(401)
            return httpx.Response(200, json={"tracks": page([])})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            assert (await client(http).search("query")).items == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"tracks": {"offset": 1}},
        {"tracks": page([], 1)},
        {"tracks": page([{}] * 11)},
    ],
)
def test_invalid_catalog_pages_are_rejected(payload):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        ) as http:
            with pytest.raises(SpotifyError):
                await client(http).search("query")

    asyncio.run(run())


def test_cache_clear_rejects_inflight_catalog_and_metadata():
    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        async def slow(*args):
            started.set()
            await release.wait()
            return Page[Track | None].model_validate(page([{"name": "Old"}]))

        api = Mock(search=AsyncMock(side_effect=slow))
        library = SpotifyLibrary(api)
        task = asyncio.create_task(library.load_catalog(("search", "query", "track")))
        await started.wait()
        library.clear()
        release.set()
        await task
        assert not library.catalog

    asyncio.run(run())


@pytest.mark.parametrize(
    "command,args,body",
    [
        ("play_track", ("spotify:track:one",), {"uris": ["spotify:track:one"]}),
        (
            "play_album",
            ("one", 51),
            {"context_uri": "spotify:album:one", "offset": {"position": 51}},
        ),
    ],
)
def test_catalog_playback_has_no_fallback_or_ambiguous_network_retry(
    command, args, body
):
    async def run():
        calls = []

        def handler(request):
            calls.append(request)
            assert (
                request.method == "PUT" and request.url.params["device_id"] == "speaker"
            )
            assert json.loads(request.content) == body
            raise httpx.ReadTimeout("Ambiguous mutation")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(httpx.ReadTimeout):
                await getattr(client(http), command)(*args, device_id="speaker")
            assert len(calls) == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "data",
    [
        {"is_playable": False},
        {"is_local": True},
        {"restrictions": {"reason": "market"}},
        {"type": "episode"},
        {"uri": None},
    ],
)
def test_unavailable_catalog_tracks_cannot_be_played(data):
    assert not Track.model_validate({"uri": "spotify:track:one", **data}).playable

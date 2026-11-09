import asyncio
from unittest.mock import Mock

import httpx
import pytest

from obelisk_music import auth, spotify
from obelisk_music.auth import AuthError
from obelisk_music.models import Token
from obelisk_music.session import SpotifySession
from obelisk_music.spotify import SpotifyClient, SpotifyError


def api(http):
    manager = SpotifySession("test-client", http)
    manager._install(Token(access_token="old-test-access", expires_in=3600))
    return SpotifyClient(manager, http)


def test_401_refreshes_once_without_reauthorization(monkeypatch):
    async def run():
        calls = []
        monkeypatch.setattr(
            auth.keyring, "get_password", Mock(return_value="test-refresh")
        )

        def handler(request):
            calls.append(request)
            if request.url == auth.TOKEN_URL:
                assert "Authorization" not in request.headers
                return httpx.Response(
                    200, json={"access_token": "new-test-access", "expires_in": 3600}
                )
            if request.headers["Authorization"] == "Bearer old-test-access":
                return httpx.Response(401)
            return httpx.Response(
                200,
                json={
                    "is_playing": True,
                    "item": {"name": "Track", "duration_ms": 1000},
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = api(http)
            playback = await client.playback()
            assert playback.is_playing and playback.item.name == "Track"
            await client.pause()
        assert len(calls) == 4
        assert sum(r.url == auth.TOKEN_URL for r in calls) == 1

    asyncio.run(run())


def test_repeated_401_is_bounded(monkeypatch):
    async def run():
        calls = []
        monkeypatch.setattr(
            auth.keyring, "get_password", Mock(return_value="test-refresh")
        )

        def handler(request):
            calls.append(request)
            if request.url == auth.TOKEN_URL:
                return httpx.Response(
                    200, json={"access_token": "new-test-access", "expires_in": 3600}
                )
            return httpx.Response(401)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(AuthError, match="refreshed session"):
                await api(http).next()
        assert len(calls) == 3

    asyncio.run(run())


def test_shared_cooldown_blocks_queued_commands_and_polling(monkeypatch):
    async def run():
        now = [100.0]
        monkeypatch.setattr(spotify.time, "monotonic", lambda: now[0])
        calls = []

        async def handler(request):
            calls.append(request)
            await asyncio.sleep(0)
            return (
                httpx.Response(429, headers={"Retry-After": "20"})
                if len(calls) == 1
                else httpx.Response(204)
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = api(http)
            results = await asyncio.gather(
                client.playback(),
                client.next(),
                client.devices(),
                return_exceptions=True,
            )
            assert len(calls) == 1
            assert all(
                isinstance(exc, SpotifyError)
                and exc.status_code == 429
                and exc.retry_after == 20
                for exc in results
            )
            now[0] += 19
            with pytest.raises(SpotifyError, match="1s"):
                await client.play()
            now[0] += 1
            assert not (await client.playback()).is_playing
            assert len(calls) == 2

    asyncio.run(run())


@pytest.mark.parametrize(
    ("header", "expected"),
    [("5", 5), ("0", 0), ("-2", 0), (None, 30), ("bad", 30), ("NaN", 30), ("inf", 30)],
)
def test_retry_after_validation(header, expected):
    assert SpotifyClient._retry_after(header) == expected


def test_retry_after_http_date(monkeypatch):
    monkeypatch.setattr(spotify.time, "time", lambda: 0)
    assert SpotifyClient._retry_after("Thu, 01 Jan 1970 00:01:00 GMT") == 60


@pytest.mark.parametrize("status", [403, 404, 500])
def test_api_errors_do_not_include_raw_response(status):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(status, text="sensitive-response")
            )
        ) as http:
            with pytest.raises(SpotifyError) as exc:
                await api(http).playback()
            assert exc.value.status_code == status
            assert "sensitive-response" not in str(exc.value)

    asyncio.run(run())


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not-json"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"is_playing": "yes"}),
        httpx.Response(200, json={"item": {"duration_ms": -1}}),
    ],
)
def test_invalid_playback_responses_are_safe(response):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: response)
        ) as http:
            with pytest.raises(SpotifyError, match="invalid"):
                await api(http).playback()

    asyncio.run(run())


def test_empty_playback_and_devices():
    async def run():
        def handler(request):
            return (
                httpx.Response(
                    200, json={"devices": [{"name": "Desktop", "id": "test-device"}]}
                )
                if request.url.path.endswith("/devices")
                else httpx.Response(204)
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = api(http)
            playback = await client.playback()
            assert (
                not playback.is_playing
                and playback.item is None
                and playback.device is None
            )
            assert (await client.devices())[0].name == "Desktop"
            await client.pause()
            await client.play()
            await client.next()
            await client.previous()

    asyncio.run(run())


def test_network_failure_does_not_retry_playback_mutation():
    async def run():
        calls = []

        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout("test timeout", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(httpx.ReadTimeout):
                await api(http).next()
        assert len(calls) == 1

    asyncio.run(run())

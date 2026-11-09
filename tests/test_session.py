import asyncio
from unittest.mock import Mock

import httpx
import pytest

from obelisk_music import auth, session
from obelisk_music.auth import AuthError
from obelisk_music.models import Token
from obelisk_music.session import SpotifySession


def test_expiration_and_concurrent_refresh(monkeypatch):
    async def run():
        now = [100.0]
        monkeypatch.setattr(session.time, "monotonic", lambda: now[0])
        monkeypatch.setattr(
            auth.keyring, "get_password", Mock(return_value="test-refresh")
        )
        calls = []

        async def handler(request):
            calls.append(request)
            await asyncio.sleep(0)
            return httpx.Response(
                200,
                json={"access_token": f"test-access-{len(calls)}", "expires_in": 3600},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            manager = SpotifySession("test-client", http)
            tokens = await asyncio.gather(*(manager.access_token() for _ in range(5)))
            assert len(calls) == 1
            assert tokens == [("test-access-1", 1)] * 5
            now[0] += 3600
            tokens = await asyncio.gather(*(manager.access_token() for _ in range(5)))
            assert len(calls) == 2
            assert tokens == [("test-access-2", 2)] * 5

    asyncio.run(run())


def test_concurrent_401_recovery_uses_generation_even_if_token_is_unchanged(
    monkeypatch,
):
    async def run():
        monkeypatch.setattr(
            auth.keyring, "get_password", Mock(return_value="test-refresh")
        )
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(
                200, json={"access_token": "same-test-access", "expires_in": 3600}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            manager = SpotifySession("test-client", http)
            manager._install(Token(access_token="same-test-access", expires_in=3600))
            tokens = await asyncio.gather(
                *(manager.access_token(rejected_generation=1) for _ in range(5))
            )
            assert len(calls) == 1
            assert tokens == [("same-test-access", 2)] * 5

    asyncio.run(run())


def test_restore_no_credentials_and_rejected_credentials(monkeypatch):
    async def run():
        monkeypatch.setattr(auth.keyring, "get_password", Mock(return_value=None))
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(400))
        ) as http:
            manager = SpotifySession("test-client", http)
            assert not await manager.restore()
            with pytest.raises(AuthError, match="authorize"):
                await manager.access_token()
            monkeypatch.setattr(
                auth.keyring, "get_password", Mock(return_value="test-refresh")
            )
            with pytest.raises(AuthError, match="400"):
                await manager.restore()
            assert not manager.authorized

    asyncio.run(run())


def test_login_installs_expiring_token(monkeypatch):
    async def run():
        from unittest.mock import AsyncMock

        monkeypatch.setattr(
            session,
            "interactive_login",
            AsyncMock(return_value=Token(access_token="test-login", expires_in=60)),
        )
        async with httpx.AsyncClient() as http:
            manager = SpotifySession("test-client", http)
            await manager.login("http://127.0.0.1:8765/callback")
            assert manager.authorized
            assert await manager.access_token() == ("test-login", 1)
            assert await manager.restore()

    asyncio.run(run())


@pytest.mark.parametrize("status", [400, 500])
def test_invalid_grant_stops_polling_but_transient_failure_can_retry(
    monkeypatch, status
):
    async def run():
        monkeypatch.setattr(
            auth.keyring, "get_password", Mock(return_value="test-refresh")
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(status))
        ) as http:
            manager = SpotifySession("test-client", http)
            manager._install(Token(access_token="test-access", expires_in=3600))
            expected = AuthError if status == 400 else httpx.HTTPStatusError
            with pytest.raises(expected):
                await manager.access_token(rejected_generation=manager.generation)
            assert manager.authorized is (status == 500)

    asyncio.run(run())


def test_concurrent_refresh_failure_is_shared_until_explicit_restore(monkeypatch):
    async def run():
        monkeypatch.setattr(
            auth.keyring, "get_password", Mock(return_value="test-refresh")
        )
        calls = []

        async def handler(request):
            calls.append(request)
            await asyncio.sleep(0)
            if len(calls) == 1:
                return httpx.Response(400)
            return httpx.Response(
                200, json={"access_token": "new-test-access", "expires_in": 3600}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            manager = SpotifySession("test-client", http)
            results = await asyncio.gather(
                *(manager.access_token() for _ in range(5)), return_exceptions=True
            )
            assert all(isinstance(exc, AuthError) for exc in results)
            assert len(calls) == 1
            assert not manager.authorized
            assert await manager.restore()
            assert await manager.access_token() == ("new-test-access", 2)
            assert len(calls) == 2

    asyncio.run(run())

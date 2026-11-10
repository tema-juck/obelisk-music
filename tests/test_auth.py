import asyncio
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from keyring.errors import (
    InitError,
    KeyringError,
    KeyringLocked,
    NoKeyringError,
    PasswordSetError,
)

from obelisk_music import auth


def test_pkce_known_vector_and_verifier():
    assert (
        auth.make_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )
    verifier = auth.make_verifier()
    assert 43 <= len(verifier) <= 128
    assert len(auth.make_challenge(verifier)) == 43
    assert "=" not in auth.make_challenge(verifier)


def test_auth_url_contains_required_parameters():
    url = auth.authorization_url(
        "abc", "http://127.0.0.1:8765/callback", "verifier", "state123"
    )
    qs = parse_qs(urlparse(url).query)
    assert qs["client_id"] == ["abc"]
    assert qs["state"] == ["state123"]
    assert qs["redirect_uri"] == ["http://127.0.0.1:8765/callback"]
    assert qs["response_type"] == ["code"]
    assert qs["code_challenge_method"] == ["S256"]
    assert "user-modify-playback-state" in qs["scope"][0]


def test_refresh_rotation_and_optional_refresh_token(monkeypatch):
    async def run():
        monkeypatch.setattr(
            auth.keyring, "get_password", Mock(return_value="stored-test-refresh")
        )
        save = Mock()
        monkeypatch.setattr(auth.keyring, "set_password", save)

        def handler(request):
            assert request.url == auth.TOKEN_URL
            assert b"grant_type=refresh_token" in request.content
            assert b"client_id=test-client" in request.content
            return httpx.Response(
                200,
                json={
                    "access_token": "test-access",
                    "refresh_token": "rotated-test-refresh",
                    "expires_in": 3600,
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            token = await auth.refresh_access_token("test-client", http)
        assert token.expires_in == 3600
        assert "test-access" not in repr(token)
        save.assert_called_once_with(
            "obelisk-music", "test-client", "rotated-test-refresh"
        )
        save.reset_mock()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200, json={"access_token": "test-access", "expires_in": 3600}
                )
            )
        ) as http:
            await auth.refresh_access_token("test-client", http)
        save.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize(
    "error", [KeyringError, InitError, KeyringLocked, NoKeyringError, PasswordSetError]
)
def test_keychain_errors_are_actionable_and_redacted(monkeypatch, error):
    async def run():
        monkeypatch.setattr(
            auth.keyring,
            "get_password",
            Mock(side_effect=error("sensitive-backend-detail")),
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(500))
        ) as http:
            with pytest.raises(auth.AuthError, match="OS keychain") as exc:
                await auth.refresh_access_token("test-client", http)
            assert "sensitive-backend-detail" not in str(exc.value)
            monkeypatch.setattr(
                auth.keyring, "get_password", Mock(return_value="stored-test-refresh")
            )
            monkeypatch.setattr(
                auth.keyring,
                "set_password",
                Mock(side_effect=error("sensitive-backend-detail")),
            )
            monkeypatch.setattr(
                auth, "_authorization_code", AsyncMock(return_value="test-code")
            )
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda r: httpx.Response(
                        200,
                        json={
                            "access_token": "test-access",
                            "refresh_token": "test-refresh",
                            "expires_in": 3600,
                        },
                    )
                )
            ) as token_http:
                with pytest.raises(auth.AuthError, match="OS keychain"):
                    await auth.interactive_login(
                        "test-client", "http://127.0.0.1:8765/callback", token_http
                    )

    asyncio.run(run())


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"access_token": "sensitive-value", "expires_in": 0},
        {"access_token": "sensitive-value", "expires_in": "3600"},
    ],
)
def test_invalid_token_response_is_redacted(monkeypatch, payload):
    async def run():
        monkeypatch.setattr(
            auth.keyring, "get_password", Mock(return_value="test-refresh")
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
        ) as http:
            with pytest.raises(auth.AuthError, match="invalid token response") as exc:
                await auth.refresh_access_token("test-client", http)
            assert "sensitive-value" not in str(exc.value)

    asyncio.run(run())


def test_login_exchange_requires_refresh_token(monkeypatch):
    async def run():
        monkeypatch.setattr(
            auth, "_authorization_code", AsyncMock(return_value="test-code")
        )
        save = Mock()
        monkeypatch.setattr(auth.keyring, "set_password", save)

        def handler(request):
            data = parse_qs(request.content.decode())
            assert data["grant_type"] == ["authorization_code"]
            assert data["code"] == ["test-code"]
            assert len(data["code_verifier"][0]) >= 43
            return httpx.Response(
                200, json={"access_token": "test-access", "expires_in": 3600}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(auth.AuthError, match="refresh token"):
                await auth.interactive_login(
                    "test-client", "http://127.0.0.1:8765/callback", http
                )
        save.assert_not_called()

    asyncio.run(run())


class CallbackServer:
    def __init__(self):
        self.callback = None
        self.closed = False

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


class CallbackWriter:
    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, data):
        self.data += data

    async def drain(self):
        pass

    def close(self):
        self.closed = True


def install_callback_server(monkeypatch):
    server = CallbackServer()

    async def start(callback, host, port, **kwargs):
        assert host == "127.0.0.1"
        assert kwargs["limit"] == 8192
        server.callback = callback
        return server

    monkeypatch.setattr(auth.asyncio, "start_server", start)
    return server


@pytest.mark.parametrize("ending", ["code=test-code", "error=access_denied"])
def test_callback_validates_state_path_and_denial(monkeypatch, ending):
    async def run():
        server = install_callback_server(monkeypatch)
        loop = asyncio.get_running_loop()
        writers = []

        async def send(url):
            state = parse_qs(urlparse(url).query)["state"][0]
            for target in [
                f"/wrong?state={state}&code=bad",
                "/callback?state=wrong&code=bad",
                f"/callback?state={state}&code=a&code=b",
                f"/callback?state={state}&{ending}",
            ]:
                reader = asyncio.StreamReader()
                reader.feed_data(
                    f"GET {target} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode()
                )
                writer = CallbackWriter()
                writers.append(writer)
                await server.callback(reader, writer)

        def browser(url):
            asyncio.run_coroutine_threadsafe(send(url), loop)
            return True

        monkeypatch.setattr(auth.webbrowser, "open", browser)
        if ending.startswith("error"):
            with pytest.raises(auth.AuthError, match="denied"):
                await auth._authorization_code(
                    "test-client",
                    "http://127.0.0.1:8765/callback",
                    "test-verifier",
                    timeout=1,
                )
        else:
            assert (
                await auth._authorization_code(
                    "test-client",
                    "http://127.0.0.1:8765/callback",
                    "test-verifier",
                    timeout=1,
                )
                == "test-code"
            )
        assert server.closed
        assert all(w.closed for w in writers)
        assert all(b"400 Bad Request" in w.data for w in writers[:3])
        assert b"200 OK" in writers[3].data

    asyncio.run(run())


@pytest.mark.parametrize("cancel", [False, True])
def test_callback_deadline_and_cancellation_close_slow_connections(monkeypatch, cancel):
    async def run():
        server = install_callback_server(monkeypatch)
        opened = asyncio.Event()
        loop = asyncio.get_running_loop()

        def browser(url):
            loop.call_soon_threadsafe(opened.set)
            return True

        monkeypatch.setattr(auth.webbrowser, "open", browser)
        login = asyncio.create_task(
            auth._authorization_code(
                "test-client",
                "http://127.0.0.1:8765/callback",
                "test-verifier",
                timeout=0.05 if not cancel else 5,
            )
        )
        await opened.wait()
        writer = CallbackWriter()
        connection = asyncio.create_task(
            server.callback(asyncio.StreamReader(), writer)
        )
        await asyncio.sleep(0)
        if cancel:
            login.cancel()
            with pytest.raises(asyncio.CancelledError):
                await login
        else:
            with pytest.raises(auth.AuthError, match="timed out"):
                await login
        assert server.closed and writer.closed and connection.done()

    asyncio.run(run())


def test_browser_failure_closes_callback_server(monkeypatch):
    async def run():
        server = install_callback_server(monkeypatch)
        monkeypatch.setattr(auth.webbrowser, "open", lambda url: False)
        with pytest.raises(auth.AuthError, match="browser"):
            await auth._authorization_code(
                "test-client",
                "http://127.0.0.1:8765/callback",
                "test-verifier",
                timeout=1,
            )
        assert server.closed

    asyncio.run(run())


@pytest.mark.parametrize(
    "uri",
    [
        "https://127.0.0.1:8765/callback",
        "http://0.0.0.0:8765/callback",
        "http://localhost:8765/callback",
        "http://127.0.0.1/callback",
        "http://127.0.0.1:8765/callback?x=1",
    ],
)
def test_redirect_must_be_explicit_loopback(monkeypatch, uri):
    async def run():
        with pytest.raises(ValueError, match="Redirect URI"):
            await auth._authorization_code(
                "test-client", uri, "test-verifier", timeout=1
            )

    asyncio.run(run())


def test_cancelling_rotation_waits_for_keychain_write(monkeypatch):
    import threading

    async def run():
        started = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()

        def save(*args):
            loop.call_soon_threadsafe(started.set)
            assert release.wait(2)

        monkeypatch.setattr(auth.keyring, "set_password", save)
        task = asyncio.create_task(
            auth._save_refresh_token(
                "test-client",
                auth.Token(
                    access_token="test-access",
                    refresh_token="test-refresh",
                    expires_in=3600,
                ),
            )
        )
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())

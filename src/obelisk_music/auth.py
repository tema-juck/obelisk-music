"""Spotify PKCE authorization and OS-keychain credential persistence."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import webbrowser
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import keyring
from keyring.errors import KeyringError

from .models import Token

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
SCOPES = (
    "user-read-playback-state user-modify-playback-state user-read-currently-playing"
)
SERVICE = "obelisk-music"


class AuthError(RuntimeError):
    """An actionable authentication failure without credential details."""


def make_verifier() -> str:
    return secrets.token_urlsafe(64)


def make_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorization_url(
    client_id: str, redirect_uri: str, verifier: str, state: str
) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
            "state": state,
            "code_challenge_method": "S256",
            "code_challenge": make_challenge(verifier),
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


async def _authorization_code(
    client_id: str, redirect_uri: str, verifier: str, *, timeout: float
) -> str:
    parsed = urlparse(redirect_uri)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or not parsed.port
        or not parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Redirect URI must be http://127.0.0.1:PORT/path")
    state = secrets.token_urlsafe(24)
    result: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    connections: set[asyncio.Task] = set()
    closing = False

    async def callback(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if closing:
            writer.close()
            return
        task = asyncio.current_task()
        connections.add(task)
        try:
            # Bound accepted connections as well as the overall authorization wait.
            request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 2)
            method, target, _ = request.split(b"\r\n", 1)[0].decode("ascii").split()
            path = urlparse(target)
            params = parse_qs(path.query)
            valid = (
                method == "GET"
                and path.path == parsed.path
                and len(params.get("state", [])) == 1
                and secrets.compare_digest(params["state"][0].encode(), state.encode())
                and (
                    (len(params.get("code", [])) == 1)
                    != (len(params.get("error", [])) == 1)
                )
            )
            if valid and not result.done():
                if "error" in params:
                    result.set_exception(
                        AuthError("Spotify authorization denied; press L to retry")
                    )
                else:
                    result.set_result(params["code"][0])
            body = (
                b"Obelisk Music: return to the terminal."
                if valid
                else b"Invalid callback"
            )
            status = b"200 OK" if valid else b"400 Bad Request"
            writer.write(
                b"HTTP/1.1 "
                + status
                + b"\r\nContent-Type: text/plain; charset=utf-8\r\n"
                + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            await asyncio.wait_for(writer.drain(), 2)
        except (
            ValueError,
            UnicodeError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            OSError,
            TimeoutError,
        ):
            pass
        finally:
            writer.close()
            connections.discard(task)

    server = await asyncio.start_server(callback, "127.0.0.1", parsed.port, limit=8192)
    try:
        async with asyncio.timeout(timeout):
            opened = await asyncio.to_thread(
                webbrowser.open,
                authorization_url(client_id, redirect_uri, verifier, state),
            )
            if not opened:
                raise AuthError(
                    "Could not open a browser; check your default browser and press L"
                )
            return await result
    except TimeoutError:
        raise AuthError("Spotify login timed out; press L to retry") from None
    finally:
        closing = True
        server.close()
        await server.wait_closed()
        for task in tuple(connections):
            task.cancel()
        await asyncio.gather(*connections, return_exceptions=True)
        if not result.done():
            result.cancel()
        elif not result.cancelled():
            result.exception()


async def _token_request(http: httpx.AsyncClient, data: dict[str, str]) -> Token:
    response = await http.post(TOKEN_URL, data=data)
    if response.status_code in (400, 401):
        raise AuthError(
            f"Spotify token request failed ({response.status_code}); press L to authorize"
        )
    response.raise_for_status()
    try:
        return Token.model_validate(response.json())
    except ValueError:
        raise AuthError("Spotify returned an invalid token response") from None


async def _save_refresh_token(client_id: str, token: Token) -> None:
    if token.refresh_token is not None:
        try:
            write = asyncio.create_task(
                asyncio.to_thread(
                    keyring.set_password,
                    SERVICE,
                    client_id,
                    token.refresh_token.get_secret_value(),
                )
            )
            cancelled = False
            # Finish credential rotation before the session lock is released.
            while True:
                try:
                    await asyncio.shield(write)
                    break
                except asyncio.CancelledError:
                    cancelled = True
                except KeyringError:
                    if cancelled:
                        raise asyncio.CancelledError from None
                    raise
            if cancelled:
                raise asyncio.CancelledError
        except KeyringError:
            raise AuthError(
                "Could not save credentials; unlock or configure your OS keychain"
            ) from None


async def interactive_login(
    client_id: str, redirect_uri: str, http: httpx.AsyncClient, *, timeout: float = 180
) -> Token:
    verifier = make_verifier()
    code = await _authorization_code(client_id, redirect_uri, verifier, timeout=timeout)
    token = await _token_request(
        http,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    if token.refresh_token is None:
        raise AuthError("Spotify did not return a refresh token")
    await _save_refresh_token(client_id, token)
    return token


async def refresh_access_token(client_id: str, http: httpx.AsyncClient) -> Token | None:
    try:
        refresh_token = await asyncio.to_thread(
            keyring.get_password, SERVICE, client_id
        )
    except KeyringError:
        raise AuthError(
            "Could not read credentials; unlock or configure your OS keychain"
        ) from None
    if not refresh_token:
        return None
    token = await _token_request(
        http,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        },
    )
    await _save_refresh_token(client_id, token)
    return token

"""Spotify Authorization Code with PKCE for a desktop client."""
from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import keyring

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing"
SERVICE = "obelisk-music"


def make_verifier() -> str:
    return secrets.token_urlsafe(64)


def make_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorization_url(client_id: str, redirect_uri: str, verifier: str, state: str) -> str:
    query = urlencode({
        "response_type": "code", "client_id": client_id,
        "redirect_uri": redirect_uri, "scope": SCOPES, "state": state,
        "code_challenge_method": "S256", "code_challenge": make_challenge(verifier),
    })
    return f"{AUTHORIZE_URL}?{query}"


def interactive_login(client_id: str, redirect_uri: str) -> str:
    """Obtain refresh token with local loopback callback; store in OS keychain."""
    parsed = urlparse(redirect_uri)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
        raise ValueError("Redirect URI must be http://127.0.0.1:PORT/path")
    verifier, state = make_verifier(), secrets.token_urlsafe(24)
    event = threading.Event()
    result: dict[str, str] = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path)
            params = parse_qs(path.query)
            if path.path != parsed.path or params.get("state", [""])[0] != state:
                self.send_error(400, "Invalid callback")
                return
            result["code"] = params.get("code", [""])[0]
            result["error"] = params.get("error", [""])[0]
            body = b"Obelisk Music: authorization received. You can close this tab."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            event.set()

        def log_message(self, fmt: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", parsed.port), Callback)
    server.timeout = 1
    try:
        webbrowser.open(authorization_url(client_id, redirect_uri, verifier, state))
        # Run a bounded server loop: don't hang forever if OAuth is cancelled.
        for _ in range(180):
            if event.is_set():
                break
            server.handle_request()
    finally:
        server.server_close()
    if not event.is_set():
        raise TimeoutError("Spotify login timed out")
    if result.get("error") or not result.get("code"):
        raise RuntimeError(f"Spotify authorization failed: {result.get('error', 'missing code')}")
    with httpx.Client(timeout=15) as client:
        response = client.post(TOKEN_URL, data={
            "grant_type": "authorization_code", "code": result["code"],
            "redirect_uri": redirect_uri, "client_id": client_id,
            "code_verifier": verifier,
        })
        response.raise_for_status()
        payload = response.json()
    refresh_token = payload.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("Spotify did not return a refresh token")
    keyring.set_password(SERVICE, client_id, refresh_token)
    return payload["access_token"]


def refresh_access_token(client_id: str) -> str | None:
    refresh_token = keyring.get_password(SERVICE, client_id)
    if not refresh_token:
        return None
    with httpx.Client(timeout=15) as client:
        response = client.post(TOKEN_URL, data={
            "grant_type": "refresh_token", "refresh_token": refresh_token,
            "client_id": client_id,
        })
        response.raise_for_status()
        payload = response.json()
    if new_token := payload.get("refresh_token"):
        keyring.set_password(SERVICE, client_id, new_token)
    return payload["access_token"]

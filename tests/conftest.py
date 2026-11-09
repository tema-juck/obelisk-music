"""Tests must explicitly mock external services and never use real credentials."""

import os
import socket
import webbrowser

import keyring
import pytest


@pytest.fixture(autouse=True)
def isolate_external_services(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path.resolve() / "config-home"))
    for name in tuple(os.environ):
        if name.startswith(("SPOTIFY_", "OBELISK_")):
            monkeypatch.delenv(name)

    def blocked_network(*args, **kwargs):
        raise AssertionError("Tests must mock network access")

    def blocked_credentials(*args, **kwargs):
        raise AssertionError("Tests must mock keychain and browser access")

    monkeypatch.setattr(socket, "getaddrinfo", blocked_network)
    monkeypatch.setattr(socket.socket, "connect", blocked_network)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked_network)
    monkeypatch.setattr(keyring, "get_password", blocked_credentials)
    monkeypatch.setattr(keyring, "set_password", blocked_credentials)
    monkeypatch.setattr(webbrowser, "open", blocked_credentials)

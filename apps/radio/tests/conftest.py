import os
import socket
import webbrowser

import pytest


@pytest.fixture(autouse=True)
def isolate_external_services(monkeypatch, tmp_path):
    for name in tuple(os.environ):
        if name.startswith(("OBELISK_", "SPOTIFY_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path.resolve() / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path.resolve() / "data"))

    def blocked(*args, **kwargs):
        raise AssertionError("Tests must mock network/browser access")

    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(webbrowser, "open", blocked)

import os
import socket
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import keyring
import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_tests_cannot_access_network_or_credentials():
    with socket.socket() as connection, pytest.raises(AssertionError, match="network"):
        connection.connect(("127.0.0.1", 1))
    with pytest.raises(AssertionError, match="network"):
        socket.getaddrinfo("api.spotify.com", 443)
    with pytest.raises(AssertionError, match="keychain"):
        keyring.get_password("obelisk-music", "test-client")


@pytest.mark.parametrize(
    "missing", ["obelisk.tcss", "models.py", "library.py", "browser.py", "screens.py"]
)
def test_smoke_rejects_missing_runtime_resource_before_install(tmp_path, missing):
    wheel = tmp_path / "broken.whl"
    with ZipFile(wheel, "w") as archive:
        for name in [
            "__init__.py",
            "app.py",
            "auth.py",
            "models.py",
            "library.py",
            "browser.py",
            "screens.py",
            "session.py",
            "spotify.py",
            "widgets.py",
            "obelisk.tcss",
        ]:
            if name != missing:
                archive.writestr(f"obelisk_music/{name}", "test fixture")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "smoke_wheel.py"), str(wheel)],
        cwd=tmp_path,
        env={**os.environ, "PATH": ""},
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode != 0
    assert f"Wheel is missing runtime files: obelisk_music/{missing}" in result.stderr

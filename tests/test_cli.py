import subprocess
import sys
import tomllib
from importlib.metadata import version
from pathlib import Path
from unittest.mock import Mock

import pytest
from packaging.version import Version

from obelisk_music import __version__, app


def test_version_has_one_authority():
    project = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    )
    assert __version__ == version("obelisk-music") == project["project"]["version"]


def test_current_package_metadata_passes_real_release_validator(tmp_path):
    parsed = Version(__version__)
    tag = f"v{parsed.base_version}"
    if parsed.pre:
        stage, number = parsed.pre
        stage = {"a": "alpha", "b": "beta", "rc": "rc"}[stage]
        tag += f"-{stage}.{number}"
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "scripts/release.py"), "verify-tag", "--tag", tag],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    assert f"{tag} matches package version {__version__}" in result.stdout


@pytest.mark.parametrize("option", ["--help", "--version"])
def test_information_flags_do_not_start_tui(monkeypatch, capsys, option):
    constructor = Mock(
        side_effect=AssertionError("Information flags must not start the app")
    )
    monkeypatch.setattr(app, "ObeliskApp", constructor)
    monkeypatch.setattr(sys, "argv", ["obelisk", option])
    with pytest.raises(SystemExit) as exc:
        app.main()
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert (
        f"obelisk {__version__}" in output
        if option == "--version"
        else "--version" in output
        and "usage: obelisk" in output
        and "Obelisk Player" in output
    )
    constructor.assert_not_called()


def test_default_cli_starts_tui(monkeypatch):
    constructor = Mock()
    monkeypatch.setattr(app, "ObeliskApp", constructor)
    monkeypatch.setattr(sys, "argv", ["obelisk"])
    app.main()
    constructor.return_value.run.assert_called_once_with()

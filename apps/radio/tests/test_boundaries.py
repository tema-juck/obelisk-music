"""Both packages must remain installable and runnable without the other product."""

import ast
import asyncio
import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from obelisk_radio.app import RadioApp
from obelisk_radio.config import (
    ConfigError,
    Settings,
    config_path,
    init_config,
    load_settings,
)
from obelisk_radio.radio_ui import RadioScreen

ROOT = Path(__file__).resolve().parents[1]


def test_no_player_or_keyring_in_runtime_dependency_closure():
    assert importlib.util.find_spec("keyring") is None
    assert importlib.util.find_spec("obelisk_music") is None
    for path in (ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(("obelisk_music", "keyring"))
            if isinstance(node, ast.Import):
                assert not any(
                    n.name.startswith(("obelisk_music", "keyring")) for n in node.names
                )
        assert "api.spotify.com" not in path.read_text()
        assert "accounts.spotify.com" not in path.read_text()
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    assert not {"obelisk-music", "keyring"} & {p["name"] for p in lock["package"]}


def test_original_engine_and_all_seven_commits_have_provenance():
    provenance = json.loads((ROOT / "PROVENANCE.json").read_text())
    assert len(provenance["original_commits"]) == 7
    assert provenance["source_commit"] == provenance["original_commits"][-1]
    original = provenance["original_files_sha256"]["src/obelisk_music/radio_audio.py"]
    assert (
        hashlib.sha256(
            (ROOT / "src/obelisk_radio/radio_audio.py").read_bytes()
        ).hexdigest()
        == original
    )
    assert "Obelisk Music contributors" in (ROOT / "LICENSE").read_text()


def test_offline_startup_ignores_player_settings_and_never_reads_credentials(
    monkeypatch, tmp_path
):
    player = Path(os.environ["XDG_CONFIG_HOME"]) / "obelisk/config.toml"
    player.parent.mkdir(parents=True)
    player.write_text("This is deliberately invalid TOML; Radio must not read it")
    original = player.read_bytes()
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "invalid!!")
    monkeypatch.setenv("OBELISK_THEME", "invalid-player-theme")

    async def run():
        app = RadioApp(settings=load_settings(config_path()))
        async with app.run_test(size=(40, 12)) as pilot:
            assert isinstance(app.screen, RadioScreen)
            assert app.screen.view == "favorites"
            assert not hasattr(app, "spotify") and not hasattr(app, "session")
            await pilot.press("q")
        assert app.radio.http.is_closed

    asyncio.run(run())
    assert player.read_bytes() == original


def test_configuration_is_separate_private_and_non_overwriting(tmp_path):
    path = config_path()
    assert path.parent.name == "obelisk-radio" and not path.parent.exists()
    assert load_settings(path) == Settings()
    init_config(path)
    original = path.read_bytes()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    with pytest.raises(ConfigError, match="already exists"):
        init_config(path)
    assert path.read_bytes() == original
    path.unlink()
    victim = tmp_path / "victim"
    victim.write_text("keep")
    path.symlink_to(victim)
    with pytest.raises(ConfigError):
        init_config(path)
    with pytest.raises(ConfigError):
        load_settings(path)
    assert victim.read_text() == "keep"


@pytest.mark.parametrize(
    "content",
    [
        "[broken",
        '[appearance]\ntheme="neon"',
        '[interface]\nvim_keys="yes"',
        '[spotify]\nclient_id="not-supported"',
    ],
)
def test_invalid_config_fails_closed(content):
    path = config_path()
    path.parent.mkdir(parents=True)
    path.write_text(content)
    with pytest.raises(ConfigError):
        load_settings(path)


def test_radio_environment_precedence_and_invalid_explicit_values(monkeypatch):
    path = config_path()
    init_config(path)
    monkeypatch.setenv("OBELISK_RADIO_THEME", "mono")
    monkeypatch.setenv("OBELISK_RADIO_VIM_KEYS", "false")
    assert load_settings(path).appearance.theme == "mono"
    assert load_settings(path).interface.vim_keys is False
    for variable in ("OBELISK_RADIO_THEME", "OBELISK_RADIO_VIM_KEYS"):
        monkeypatch.setenv(variable, "")
        with pytest.raises(ConfigError, match="must not be empty"):
            load_settings(path)
        monkeypatch.delenv(variable)


@pytest.mark.parametrize("action", ["path", "init", "validate"])
def test_config_cli_runs_without_player_or_keyring(action):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    if action == "validate":
        init_config(config_path())
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from obelisk_radio.cli import main; main()",
            "config",
            action,
        ],
        env=env,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "obelisk-radio" in result.stdout


def test_config_xdg_rejects_traversal(monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "../unsafe")
    with pytest.raises(ConfigError):
        config_path()

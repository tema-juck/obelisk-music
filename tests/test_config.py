import os
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from obelisk_music import app, config


@pytest.fixture
def path():
    return config.config_path()


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_default_and_xdg_discovery_do_not_create_directories(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME")
    home = tmp_path.resolve() / "fake-home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    assert config.config_path() == home / ".config/obelisk/config.toml"
    assert not home.exists()
    base = tmp_path.resolve() / "custom config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(base))
    assert config.config_path() == base / "obelisk/config.toml"
    assert not base.exists()


@pytest.mark.parametrize("base", ["relative", "~/config", "/tmp/../elsewhere"])
def test_invalid_explicit_xdg_path_is_rejected(monkeypatch, base):
    monkeypatch.setenv("XDG_CONFIG_HOME", base)
    with pytest.raises(config.ConfigError, match="absolute path"):
        config.config_path()


def test_missing_file_uses_defaults_without_creating_anything(path):
    settings = config.load_settings(path)
    assert settings.spotify.client_id == ""
    assert settings.spotify.refresh_interval == 8
    assert settings.appearance.theme == "midnight"
    assert settings.interface.vim_keys is True
    assert not path.parent.exists()
    with pytest.raises(config.ConfigError, match="config init"):
        config.load_settings(path, required=True)


@pytest.mark.parametrize("theme", ["midnight", "mono"])
def test_supported_theme_file_and_environment_override(path, monkeypatch, theme):
    write(path, f'[appearance]\ntheme = "{theme}"')
    assert config.load_settings(path).appearance.theme == theme
    monkeypatch.setenv("OBELISK_THEME", "mono")
    assert config.load_settings(path).appearance.theme == "mono"


def test_default_discovery_supports_an_os_symlink_for_the_home_directory(
    monkeypatch, tmp_path
):
    real = tmp_path.resolve() / "real-home"
    real.mkdir()
    home = tmp_path.resolve() / "home-alias"
    home.symlink_to(real, target_is_directory=True)
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    path = config.config_path()
    assert path == real / ".config/obelisk/config.toml"
    config.init_config(path)
    assert config.load_settings(path) == config.Settings()


def test_init_is_documented_private_and_never_overwrites(path):
    config.init_config(path)
    assert path.read_text() == config.EXAMPLE
    assert "Never store" in path.read_text()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.parent.parent.stat().st_mode) == 0o700
    before = path.read_bytes()
    with pytest.raises(config.ConfigError, match="nothing overwritten"):
        config.init_config(path)
    assert path.read_bytes() == before
    assert config.load_settings(path) == config.Settings()


@pytest.mark.parametrize(
    "text",
    [
        '[spotify]\nclient_id = "DO_NOT_LEAK',
        '[spotify]\nclient_id = "DO_NOT_LEAK"\nclient_id = "again"',
        "[DO_NOT_LEAK]\n[DO_NOT_LEAK]",
    ],
)
def test_invalid_toml_does_not_echo_source_values(path, text):
    write(path, text)
    with pytest.raises(config.ConfigError) as exc:
        config.load_settings(path)
    assert "TOML" in str(exc.value) and "config edit" in str(exc.value)
    assert "DO_NOT_LEAK" not in str(exc.value)


@pytest.mark.parametrize(
    "text",
    [
        "[spotify]\nclient_id = 123",
        '[spotify]\nclient_id = "DO_NOT_LEAK!!"',
        "[spotify]\nrefresh_interval = 0",
        "[spotify]\nrefresh_interval = 3601",
        "[spotify]\nrefresh_interval = 8.0",
        "[spotify]\nrefresh_interval = true",
        '[spotify]\nrefresh_interval = "8"',
        '[interface]\nvim_keys = "false"',
        "[interface]\nvim_keys = 0",
        '[appearance]\ntheme = "DO_NOT_LEAK"',
        '[spotify]\naccess_token = "DO_NOT_LEAK"',
        '[spotify]\nrefresh_token = "DO_NOT_LEAK"',
        '[spotify]\nclient_secret = "DO_NOT_LEAK"',
        '[unknown]\nvalue = "DO_NOT_LEAK"',
        'spotify = "DO_NOT_LEAK"',
    ],
)
def test_strict_schema_rejects_unsupported_settings_without_values(path, text):
    write(path, text)
    with pytest.raises(config.ConfigError) as exc:
        config.load_settings(path)
    assert "Invalid settings" in str(exc.value)
    assert "DO_NOT_LEAK" not in str(exc.value)


def test_file_settings_and_environment_precedence(path, monkeypatch):
    write(
        path,
        '[spotify]\nclient_id = "file-id"\nrefresh_interval = 15\n[interface]\nvim_keys = false',
    )
    settings = config.load_settings(path)
    assert settings.spotify.client_id == "file-id"
    assert settings.spotify.refresh_interval == 15 and not settings.interface.vim_keys
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "  environment-id  ")
    monkeypatch.setenv("OBELISK_REFRESH_INTERVAL", "20")
    monkeypatch.setenv("OBELISK_VIM_KEYS", "TRUE")
    monkeypatch.setenv("OBELISK_THEME", "midnight")
    overridden = config.load_settings(path)
    assert overridden.spotify.client_id == "environment-id"
    assert overridden.spotify.refresh_interval == 20
    assert overridden.interface.vim_keys is True
    assert path.read_text().find("environment-id") == -1
    assert "environment-id" not in repr(overridden)


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("SPOTIFY_CLIENT_ID", ""),
        ("SPOTIFY_CLIENT_ID", "   "),
        ("SPOTIFY_CLIENT_ID", "DO_NOT_LEAK!!"),
        ("OBELISK_REFRESH_INTERVAL", ""),
        ("OBELISK_REFRESH_INTERVAL", "0"),
        ("OBELISK_REFRESH_INTERVAL", "9999"),
        ("OBELISK_REFRESH_INTERVAL", "8.0"),
        ("OBELISK_VIM_KEYS", "1"),
        ("OBELISK_VIM_KEYS", ""),
        ("OBELISK_THEME", "DO_NOT_LEAK"),
    ],
)
def test_invalid_environment_never_falls_back_to_valid_file(
    path, monkeypatch, variable, value
):
    config.init_config(path)
    monkeypatch.setenv(variable, value)
    with pytest.raises(config.ConfigError) as exc:
        config.load_settings(path)
    assert "DO_NOT_LEAK" not in str(exc.value)


def test_invalid_file_is_not_hidden_by_environment(path, monkeypatch):
    write(path, "[spotify]\nclient_id = 123")
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "environment-id")
    with pytest.raises(config.ConfigError):
        config.load_settings(path)


@pytest.mark.parametrize(
    "location", ["file", "app-directory", "xdg-directory", "ancestor"]
)
def test_symlinks_are_refused_for_read_creation_and_edit(
    path, tmp_path, location, monkeypatch
):
    real = tmp_path.resolve() / "real"
    real.mkdir()
    if location == "file":
        write(path, "")
        path.unlink()
        path.symlink_to(real / "untouched")
    elif location == "app-directory":
        path.parent.parent.mkdir(parents=True)
        path.parent.symlink_to(real, target_is_directory=True)
    elif location == "xdg-directory":
        path.parent.parent.symlink_to(real, target_is_directory=True)
    else:
        link = tmp_path.resolve() / "linked-ancestor"
        link.symlink_to(real, target_is_directory=True)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(link / "nested"))
        path = config.config_path()
    editor = Mock(side_effect=AssertionError("Editor must not open a symlink"))
    monkeypatch.setattr(config.subprocess, "run", editor)
    for operation in (config.load_settings, config.init_config, config.edit_config):
        with pytest.raises(config.ConfigError):
            operation(path)
    assert not list(real.iterdir())


def test_path_traversal_cannot_create_a_file(tmp_path):
    with pytest.raises(config.ConfigError, match="without '..'"):
        config.init_config(tmp_path.resolve() / "nested/../config.toml")


def test_nonregular_large_and_non_utf8_files_fail_closed(path):
    path.parent.mkdir(parents=True)
    os.mkfifo(path, 0o600)
    with pytest.raises(config.ConfigError, match="regular file"):
        config.load_settings(path)
    path.unlink()
    path.write_bytes(b"x" * 65_537)
    with pytest.raises(config.ConfigError, match="64 KiB"):
        config.load_settings(path)
    path.write_bytes(b"\xff")
    with pytest.raises(config.ConfigError, match="UTF-8"):
        config.load_settings(path)


def test_read_failure_is_actionable_and_redacted(path, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("DO_NOT_LEAK")

    monkeypatch.setattr(config.os, "open", denied)
    with pytest.raises(config.ConfigError) as exc:
        config.load_settings(path)
    assert "permissions" in str(exc.value) and "DO_NOT_LEAK" not in str(exc.value)


@pytest.mark.parametrize("variable", ["VISUAL", "EDITOR", None])
def test_editor_precedence_arguments_and_missing_file_init(path, monkeypatch, variable):
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    if variable:
        monkeypatch.setenv(variable, '"my editor" --wait')
    if variable == "VISUAL":
        monkeypatch.setenv("EDITOR", "must-not-run")
    editor = Mock()
    monkeypatch.setattr(config.subprocess, "run", editor)
    config.edit_config(path)
    expected = ["my editor", "--wait", str(path)] if variable else ["vi", str(path)]
    editor.assert_called_once_with(expected, check=True, env=dict(os.environ))
    assert path.read_text() == config.EXAMPLE


def test_editor_can_repair_invalid_toml_without_loading_it(path, monkeypatch):
    write(path, 'invalid = "')
    monkeypatch.setenv("VISUAL", "editor")
    monkeypatch.setattr(
        config.subprocess,
        "run",
        lambda *args, **kwargs: path.write_text(config.EXAMPLE),
    )
    config.edit_config(path)
    assert config.load_settings(path) == config.Settings()


@pytest.mark.parametrize("original", [None, "/user/libraries"])
def test_frozen_linux_editor_uses_system_libraries_without_changing_parent_env(
    path, monkeypatch, original
):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle/libraries")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    if original is not None:
        monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", original)
    editor = Mock()
    monkeypatch.setattr(config.subprocess, "run", editor)
    config.edit_config(path)
    assert editor.call_args.kwargs["env"].get("LD_LIBRARY_PATH") == original
    assert os.environ["LD_LIBRARY_PATH"] == "/bundle/libraries"


@pytest.mark.parametrize(
    "error", [FileNotFoundError(), subprocess.CalledProcessError(1, "editor")]
)
def test_editor_failure_is_actionable(path, monkeypatch, error):
    monkeypatch.setenv("VISUAL", "unavailable")
    monkeypatch.setattr(config.subprocess, "run", Mock(side_effect=error))
    with pytest.raises(config.ConfigError, match="VISUAL or EDITOR"):
        config.edit_config(path)


@pytest.mark.parametrize("action", ["path", "init", "validate", "edit"])
def test_cli_config_commands_do_not_create_textual_app(
    path, monkeypatch, capsys, action
):
    if action == "validate":
        config.init_config(path)
    monkeypatch.setattr(config.subprocess, "run", Mock())
    monkeypatch.setattr(
        app, "ObeliskApp", Mock(side_effect=AssertionError("TUI created"))
    )
    monkeypatch.setattr(sys, "argv", ["obelisk", "config", action])
    app.main()
    assert str(path) in capsys.readouterr().out
    if action == "path":
        assert not path.parent.exists()


def test_cli_failure_hides_client_id_and_never_creates_app(path, monkeypatch, capsys):
    write(path, '[spotify]\nclient_id = "DO_NOT_LEAK!!"')
    monkeypatch.setattr(
        app, "ObeliskApp", Mock(side_effect=AssertionError("TUI created"))
    )
    for argv in (["obelisk"], ["obelisk", "config", "validate"]):
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit) as exc:
            app.main()
        assert exc.value.code == 2
        output = capsys.readouterr()
        assert "DO_NOT_LEAK" not in output.err + output.out
        assert "spotify.client_id" in output.err


def test_cli_loads_before_creating_app_and_injects_validated_settings(
    path, monkeypatch
):
    write(path, '[spotify]\nclient_id = "file-id"\nrefresh_interval = 23')
    constructor = Mock()
    monkeypatch.setattr(app, "ObeliskApp", constructor)
    monkeypatch.setattr(sys, "argv", ["obelisk"])
    app.main()
    kwargs = constructor.call_args.kwargs
    assert kwargs["settings"].spotify.client_id == "file-id"
    assert kwargs["settings"].spotify.refresh_interval == 23
    assert kwargs["config_file"] == path
    constructor.return_value.run.assert_called_once_with()


def test_packaging_config_smoke_uses_the_real_cli(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / "scripts"))
    from smoke_config import check_config

    directory = tmp_path.resolve()
    env = {
        **os.environ,
        "XDG_CONFIG_HOME": str(directory / "isolated-config"),
        "PYTHONPATH": str(root / "src"),
    }
    check_config([sys.executable, "-m", "obelisk_music.app"], directory, env)

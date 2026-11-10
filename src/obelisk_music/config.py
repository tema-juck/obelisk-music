"""Typed settings and safe XDG configuration I/O, used before starting Textual."""

from __future__ import annotations

import os
import re
import shlex
import stat
import subprocess
import sys
import tomllib
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

EXAMPLE = """# Obelisk Music configuration. Environment variables take precedence.
# Obtain a public Client ID at https://developer.spotify.com/dashboard
# Register http://127.0.0.1:8765/callback and allow your Spotify account.
# Never store client secrets, access tokens, or refresh tokens here.
[spotify]
client_id = "" # Or set SPOTIFY_CLIENT_ID. Empty means login is not configured.
refresh_interval = 8 # Playback polling in seconds: integer from 1 to 3600.

[appearance]
theme = "midnight" # midnight or mono. Restart Obelisk after changing themes.

[interface]
vim_keys = true # Enable j/k in browsing, devices, and help; arrows always work.
"""


class ConfigError(ValueError):
    """An actionable configuration error with no input values or credentials."""


class Section(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, hide_input_in_errors=True
    )


class SpotifySettings(Section):
    client_id: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, max_length=256, pattern=r"^[A-Za-z0-9_-]*$"
        ),
    ] = Field(default="", repr=False)
    refresh_interval: int = Field(default=8, ge=1, le=3600)


class AppearanceSettings(Section):
    theme: Literal["midnight", "mono"] = "midnight"


class InterfaceSettings(Section):
    vim_keys: bool = True


class Settings(Section):
    spotify: SpotifySettings = Field(default_factory=SpotifySettings)
    appearance: AppearanceSettings = Field(default_factory=AppearanceSettings)
    interface: InterfaceSettings = Field(default_factory=InterfaceSettings)


def config_path() -> Path:
    raw = os.environ.get("XDG_CONFIG_HOME")
    base = Path(raw) if raw else Path.home().resolve() / ".config"
    if not base.is_absolute() or ".." in base.parts:
        raise ConfigError("XDG_CONFIG_HOME must be an absolute path without '..'")
    return base / "obelisk" / "config.toml"


@contextmanager
def _parent(path: Path, *, create: bool = False):
    """Use directory descriptors so a symlink swap cannot redirect creation."""
    if not path.is_absolute() or ".." in path.parts:
        raise ConfigError("Configuration path must be absolute without '..'")
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parent.parts[1:]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            try:
                child = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=descriptor,
                )
            except FileNotFoundError:
                if create:
                    raise
                yield None
                return
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def _read(path: Path, *, required: bool = False) -> bytes | None:
    try:
        with _parent(path) as parent:
            if parent is None:
                raise FileNotFoundError
            descriptor = os.open(
                path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
            )
            with os.fdopen(descriptor, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ConfigError(f"Configuration must be a regular file: {path}")
                content = stream.read(65_537)
                if len(content) > 65_536:
                    raise ConfigError(f"Configuration exceeds 64 KiB: {path}")
                return content
    except FileNotFoundError:
        if required:
            raise ConfigError(
                f"No configuration at {path}; run 'obelisk config init'"
            ) from None
        return None
    except OSError:
        raise ConfigError(
            f"Cannot read {path}; check permissions and use a canonical path without symlinks"
        ) from None


def _validate(data: dict, source: str) -> Settings:
    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        messages = [
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(
                include_input=False, include_url=False, include_context=False
            )
        ]
        raise ConfigError(
            f"Invalid settings in {source}: " + "; ".join(messages)
        ) from None


def load_settings(path: Path, *, required: bool = False) -> Settings:
    content = _read(path, required=required)
    data = {}
    if content is not None:
        try:
            data = tomllib.loads(content.decode("utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeError):
            raise ConfigError(
                f"Invalid UTF-8 TOML in {path}; check quoting, duplicate keys, and table names with 'obelisk config edit'"
            ) from None
    settings = _validate(data, str(path))
    return environment_settings(settings)


def environment_settings(defaults: Settings | None = None) -> Settings:
    """Also supports embedding ObeliskApp without performing file I/O."""
    data = (defaults or Settings()).model_dump()
    for variable, section, key in (
        ("SPOTIFY_CLIENT_ID", "spotify", "client_id"),
        ("OBELISK_REFRESH_INTERVAL", "spotify", "refresh_interval"),
        ("OBELISK_THEME", "appearance", "theme"),
        ("OBELISK_VIM_KEYS", "interface", "vim_keys"),
    ):
        if variable not in os.environ:
            continue
        value = os.environ[variable].strip()
        if not value:
            raise ConfigError(
                f"{variable} must not be empty; unset it to use the configuration file"
            )
        if key == "refresh_interval":
            if not re.fullmatch(r"[0-9]{1,4}", value):
                raise ConfigError(f"{variable} must be an integer from 1 to 3600")
            value = int(value)
        elif key == "vim_keys":
            if value.lower() not in ("true", "false"):
                raise ConfigError(f"{variable} must be true or false")
            value = value.lower() == "true"
        data[section][key] = value
    return _validate(data, "environment overrides")


def init_config(path: Path) -> None:
    try:
        with _parent(path, create=True) as parent:
            descriptor = os.open(
                path.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(EXAMPLE)
    except FileExistsError:
        raise ConfigError(
            f"Configuration already exists at {path}; use 'obelisk config edit' (nothing overwritten)"
        ) from None
    except OSError:
        raise ConfigError(
            f"Cannot create {path}; check permissions and use a canonical path without symlinks"
        ) from None


def edit_config(path: Path) -> None:
    if _read(path) is None:
        init_config(path)
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    try:
        command = shlex.split(editor)
        if not command:
            raise ValueError
        editor_env = os.environ.copy()
        if getattr(sys, "frozen", False) and sys.platform == "linux":
            # A system editor must not load PyInstaller's bundled shared libraries.
            original = editor_env.get("LD_LIBRARY_PATH_ORIG")
            if original is None:
                editor_env.pop("LD_LIBRARY_PATH", None)
            else:
                editor_env["LD_LIBRARY_PATH"] = original
        subprocess.run([*command, str(path)], check=True, env=editor_env)
    except (OSError, ValueError, subprocess.CalledProcessError):
        raise ConfigError(
            "Editor failed; set VISUAL or EDITOR to an available editor (arguments are allowed)"
        ) from None

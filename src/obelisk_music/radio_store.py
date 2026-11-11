"""Private, atomic radio preferences; no Spotify secrets or playback snapshots."""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

from pydantic import ValidationError

from .config import ConfigError, _parent, _read
from .radio_http import RadioError
from .radio_models import RadioState


def radio_state_path() -> Path:
    raw = os.environ.get("XDG_DATA_HOME")
    base = Path(raw) if raw else Path.home().resolve() / ".local" / "share"
    if not base.is_absolute() or ".." in base.parts:
        raise RadioError("XDG_DATA_HOME must be an absolute path without '..'")
    return base / "obelisk" / "radio.json"


class RadioStore:
    def __init__(self, path: Path | None = None):
        self.path = path if path is not None else radio_state_path()
        self.writable = False

    def load(self) -> RadioState:
        self.writable = False
        try:
            content = _read(self.path)
            state = (
                RadioState.model_validate_json(content)
                if content is not None
                else RadioState()
            )
        except (ConfigError, ValidationError, ValueError):
            raise RadioError(
                f"Cannot read radio data at {self.path}; repair or move it aside before saving favorites"
            ) from None
        self.writable = True
        return state

    def save(self, state: RadioState) -> None:
        if not self.writable:
            raise RadioError(
                "Radio data is read-only after a load failure; repair it and restart Obelisk"
            )
        content = state.model_dump_json().encode()
        if len(content) > 65_536:
            raise RadioError(
                "Radio favorites exceed 64 KiB; remove some saved stations"
            )
        temporary = ".radio-" + secrets.token_hex(12)
        try:
            with _parent(self.path, create=True) as parent:
                try:
                    existing = os.stat(
                        self.path.name, dir_fd=parent, follow_symlinks=False
                    )
                    if (
                        not stat.S_ISREG(existing.st_mode)
                        or existing.st_uid != os.getuid()
                    ):
                        raise RadioError(
                            "Radio data must be your own regular file, without symlinks"
                        )
                except FileNotFoundError:
                    pass
                try:
                    fd = os.open(
                        temporary,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=parent,
                    )
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(
                        temporary, self.path.name, src_dir_fd=parent, dst_dir_fd=parent
                    )
                    os.fsync(parent)
                finally:
                    try:
                        os.unlink(temporary, dir_fd=parent)
                    except FileNotFoundError:
                        pass
        except (OSError, ConfigError):
            raise RadioError(
                f"Cannot save radio data at {self.path}; check permissions and avoid symlinks"
            ) from None

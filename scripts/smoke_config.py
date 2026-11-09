"""Exercise configuration through an installed CLI, without Spotify credentials."""

import shlex
import stat
import subprocess
from pathlib import Path


def check_config(command: list[str], root: Path, env: dict[str, str]) -> None:
    path = Path(env["XDG_CONFIG_HOME"]) / "obelisk" / "config.toml"

    def run(*args: str, success: bool = True, overrides: dict | None = None):
        result = subprocess.run(
            [*command, *args],
            cwd=root,
            env={**env, **(overrides or {})},
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if (result.returncode == 0) != success:
            raise ValueError(
                f"Configuration CLI smoke failed for {args}: {result.stderr[-2000:]}"
            )
        return result

    assert run("config", "path").stdout.strip() == str(path)
    assert not path.parent.exists(), "config path created directories"
    run("config", "validate", success=False)
    run("config", "init")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    original = path.read_bytes()
    run("config", "init", success=False)
    assert path.read_bytes() == original
    run("config", "validate")
    editor = root / "smoke editor.sh"
    editor.write_text('#!/bin/sh\nprintf "\\n# smoke editor\\n" >> "$1"\n')
    editor.chmod(0o700)
    run("config", "edit", overrides={"VISUAL": shlex.quote(str(editor))})
    assert "# smoke editor" in path.read_text()
    run(
        "config", "validate", success=False, overrides={"OBELISK_REFRESH_INTERVAL": "0"}
    )
    path.write_text('[spotify]\nclient_id = "DO_NOT_LEAK!!"\n')
    result = run("config", "validate", success=False)
    assert "DO_NOT_LEAK" not in result.stdout + result.stderr
    assert "config" in run("--help").stdout
    path.write_text("[spotify]\nrefresh_interval = 11\n[interface]\nvim_keys = false\n")
    run("config", "validate")
    print("Installed configuration CLI smoke passed")

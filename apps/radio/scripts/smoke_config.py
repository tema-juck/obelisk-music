"""Exercise configuration through an installed CLI, without Spotify credentials."""

import shlex
import stat
import subprocess
from pathlib import Path


def check_config(command: list[str], root: Path, env: dict[str, str]) -> None:
    path = Path(env["XDG_CONFIG_HOME"]) / "obelisk-radio" / "config.toml"

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
    path.write_text('[spotify]\nclient_id = "DO_NOT_LEAK!!"\n')
    result = run("config", "validate", success=False)
    assert "DO_NOT_LEAK" not in result.stdout + result.stderr
    assert "config" in run("--help").stdout
    for theme in ("midnight", "mono"):
        path.write_text(
            f'[interface]\nvim_keys = false\n[appearance]\ntheme = "{theme}"\n'
        )
        run("config", "validate")
    run(
        "config",
        "validate",
        success=False,
        overrides={"OBELISK_RADIO_THEME": "unsupported"},
    )
    for mode in ("spectrum", "beat-pulse", "waveform", "off"):
        path.write_text(
            f'[visualizer]\nenabled=true\nmode="{mode}"\nfps=30\nsensitivity=1.0\n'
        )
        run("config", "validate")
    for setting in ('mode="invalid"', "fps=61", "sensitivity=0.1", 'enabled="yes"'):
        path.write_text(f"[visualizer]\n{setting}\n")
        run("config", "validate", success=False)
    path.write_bytes(original)
    run("config", "validate")
    print("Installed configuration CLI smoke passed")

"""Verify wheel resources and start a clean installation outside the checkout."""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from zipfile import ZipFile

RUNTIME_FILES = {
    "obelisk_radio/audio_analysis.py",
    "obelisk_radio/audio_pipeline.py",
    "obelisk_radio/visualizer.py",
    "obelisk_radio/radio_http.py",
    "obelisk_radio/radio_models.py",
    "obelisk_radio/radio_audio.py",
    "obelisk_radio/widgets.py",
    "obelisk_radio/app.py",
    "obelisk_radio/screens.py",
    "obelisk_radio/cli.py",
    "obelisk_radio/radio_controller.py",
    "obelisk_radio/radio.tcss",
    "obelisk_radio/__init__.py",
    "obelisk_radio/radio_ui.py",
    "obelisk_radio/browser.py",
    "obelisk_radio/config.py",
    "obelisk_radio/radio_store.py",
    "obelisk_radio/radio_directory.py",
}


def check_wheel(wheel: Path) -> None:
    with ZipFile(wheel) as archive:
        if any("obelisk_music/" in name for name in archive.namelist()):
            raise SystemExit("Radio wheel contains the other product")
        missing = RUNTIME_FILES - set(archive.namelist())
        if missing:
            raise SystemExit(
                f"Wheel is missing runtime files: {', '.join(sorted(missing))}"
            )
        if not archive.read("obelisk_radio/radio.tcss").strip():
            raise SystemExit("Wheel stylesheet is empty")

    sdist = wheel.with_name(wheel.name.split("-py3-none-any.whl")[0] + ".tar.gz")
    if sdist.exists():
        with tarfile.open(sdist) as source:
            if any(
                "obelisk_music/" in name or "/apps/" in name
                for name in source.getnames()
            ):
                raise SystemExit("Radio sdist contains the other product")


async def check_installed() -> None:
    from importlib.metadata import distribution
    from importlib.resources import files
    from importlib.util import find_spec
    from unittest.mock import patch

    import obelisk_radio
    from obelisk_radio.app import RadioApp, main
    from obelisk_radio.audio_analysis import WINDOW, Analyzer
    from obelisk_radio.radio_ui import RadioScreen

    assert (
        Path(obelisk_radio.__file__)
        .resolve()
        .is_relative_to(Path(sys.prefix).resolve())
    )
    assert find_spec("obelisk_music") is None
    assert find_spec("keyring") is None
    assert Analyzer().analyze(bytes(WINDOW * 4), 0).rms == 0
    assert files("obelisk_radio").joinpath("radio.tcss").read_text().strip()
    package = distribution("obelisk-radio")
    entry = next(e for e in package.entry_points if e.name == "obelisk-radio")
    assert entry.load() is main
    assert not any(
        "keyring" in dep.lower() or "obelisk-music" in dep.lower()
        for dep in package.requires or []
    )

    def blocked(*args, **kwargs):
        raise AssertionError("Radio startup attempted network access")

    with patch("socket.getaddrinfo", blocked), patch("socket.socket.connect", blocked):
        app = RadioApp()
        async with app.run_test(size=(40, 12)) as pilot:
            assert isinstance(app.screen, RadioScreen)
            assert app.screen.view == "favorites"
            assert not hasattr(app, "session")
            await pilot.press("q")
        assert app.radio.http.is_closed
    print(
        f"Installed Radio wheel smoke passed: Python {sys.version.split()[0]}, {package.version}; Player/keyring absent"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", nargs="?", type=Path)
    parser.add_argument("--installed", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.installed:
        asyncio.run(check_installed())
        return
    if args.wheel is None:
        parser.error("provide exactly one freshly built wheel from dist/")
    wheel = args.wheel.resolve(strict=True)
    check_wheel(wheel)
    from smoke_config import check_config

    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv must be available on PATH")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("SPOTIFY_")
        and key
        not in {
            "PYTHONPATH",
            "PYTHONHOME",
            "VIRTUAL_ENV",
            "UV_PROJECT_ENVIRONMENT",
            "UV_PYTHON",
        }
    }
    with tempfile.TemporaryDirectory(prefix="obelisk-wheel-") as directory:
        root = Path(directory).resolve()
        env["XDG_CONFIG_HOME"] = str(root / "config-home")
        env["XDG_DATA_HOME"] = str(root / "data-home")
        for name in tuple(env):
            if name.startswith("OBELISK_"):
                env.pop(name)
        python = root / "venv" / "bin" / "python"
        requirements = root / "requirements.txt"
        subprocess.run(
            [
                uv,
                "export",
                "--frozen",
                "--no-dev",
                "--no-emit-project",
                "--output-file",
                str(requirements),
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            [uv, "venv", "--python", sys.executable, str(root / "venv")],
            env=env,
            check=True,
        )
        subprocess.run(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                "--require-hashes",
                "-r",
                str(requirements),
            ],
            cwd=root,
            env=env,
            check=True,
        )
        subprocess.run(
            [uv, "pip", "install", "--python", str(python), "--no-deps", str(wheel)],
            cwd=root,
            env=env,
            check=True,
        )
        subprocess.run(
            [uv, "pip", "check", "--python", str(python)], cwd=root, env=env, check=True
        )
        check_config([str(root / "venv" / "bin" / "obelisk-radio")], root, env)
        probe = root / "smoke_wheel.py"
        shutil.copyfile(__file__, probe)
        subprocess.run(
            [str(python), "-I", str(probe), "--installed"],
            cwd=root,
            env=env,
            check=True,
            timeout=30,
        )


if __name__ == "__main__":
    main()

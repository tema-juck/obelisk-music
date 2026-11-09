"""Verify wheel resources and start a clean installation outside the checkout."""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile

RUNTIME_FILES = {
    "obelisk_music/__init__.py",
    "obelisk_music/app.py",
    "obelisk_music/auth.py",
    "obelisk_music/models.py",
    "obelisk_music/library.py",
    "obelisk_music/browser.py",
    "obelisk_music/screens.py",
    "obelisk_music/session.py",
    "obelisk_music/spotify.py",
    "obelisk_music/widgets.py",
    "obelisk_music/obelisk.tcss",
}


def check_wheel(wheel: Path) -> None:
    with ZipFile(wheel) as archive:
        missing = RUNTIME_FILES - set(archive.namelist())
        if missing:
            raise SystemExit(
                f"Wheel is missing runtime files: {', '.join(sorted(missing))}"
            )
        if not archive.read("obelisk_music/obelisk.tcss").strip():
            raise SystemExit("Wheel stylesheet is empty")


async def check_installed() -> None:
    from importlib.metadata import distribution
    from importlib.resources import files
    from unittest.mock import patch

    import obelisk_music
    from obelisk_music.app import ObeliskApp, main

    assert (
        Path(obelisk_music.__file__)
        .resolve()
        .is_relative_to(Path(sys.prefix).resolve())
    ), "Imported source checkout instead of the installed wheel"
    assert files("obelisk_music").joinpath("obelisk.tcss").read_text().strip()
    package = distribution("obelisk-music")
    entry = next(
        ep
        for ep in package.entry_points
        if ep.group == "console_scripts" and ep.name == "obelisk"
    )
    assert entry.load() is main
    with (
        patch(
            "socket.getaddrinfo",
            side_effect=AssertionError("Smoke test attempted network access"),
        ),
        patch(
            "socket.socket.connect",
            side_effect=AssertionError("Smoke test attempted network access"),
        ),
        patch(
            "socket.socket.connect_ex",
            side_effect=AssertionError("Smoke test attempted network access"),
        ),
        patch(
            "keyring.get_password",
            side_effect=AssertionError("Smoke test attempted keychain access"),
        ),
        patch(
            "keyring.set_password",
            side_effect=AssertionError("Smoke test attempted keychain access"),
        ),
        patch(
            "webbrowser.open",
            side_effect=AssertionError("Smoke test attempted browser access"),
        ),
    ):
        app = ObeliskApp()
        async with app.run_test(size=(80, 24)) as pilot:
            assert app.query_one("#track").region.width > 0
            assert app.query_one("#device").region.bottom <= 24
            await pilot.press("q")
            assert not app.is_running
        assert app.http.is_closed
    print(
        f"Installed wheel smoke passed: Python {sys.version.split()[0]}, obelisk-music {package.version}"
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
        root = Path(directory)
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

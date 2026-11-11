"""Verify wheel resources and start a clean installation outside the checkout."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from zipfile import ZipFile

RUNTIME_FILES = {
    "obelisk_music/__init__.py",
    "obelisk_music/app.py",
    "obelisk_music/config.py",
    "obelisk_music/cli.py",
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
        if any("obelisk_radio/" in name for name in archive.namelist()):
            raise SystemExit("Player wheel contains the other product")
        missing = RUNTIME_FILES - set(archive.namelist())
        if missing:
            raise SystemExit(
                f"Wheel is missing runtime files: {', '.join(sorted(missing))}"
            )
        if not archive.read("obelisk_music/obelisk.tcss").strip():
            raise SystemExit("Wheel stylesheet is empty")

    sdist = wheel.with_name(wheel.name.split("-py3-none-any.whl")[0] + ".tar.gz")
    if sdist.exists():
        with tarfile.open(sdist) as source:
            if any(
                "obelisk_radio/" in name or "/apps/" in name
                for name in source.getnames()
            ):
                raise SystemExit("Player sdist contains the other product")


async def check_installed() -> None:
    from importlib.metadata import distribution
    from importlib.resources import files
    from unittest.mock import patch

    import httpx
    from textual.widgets import Input

    import obelisk_music
    from obelisk_music.app import ObeliskApp, main
    from obelisk_music.browser import CatalogTable
    from obelisk_music.config import config_path, load_settings
    from obelisk_music.models import Token
    from obelisk_music.screens import CatalogScreen
    from obelisk_music.widgets import NowPlaying

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
        path = config_path()
        track = {
            "name": "[red]Smoke track[/red]",
            "uri": "spotify:track:smoke",
            "album": {"id": "smoke-album"},
        }
        playing = False

        def catalog(request):
            nonlocal playing
            if request.method == "PUT":
                assert request.url.path == "/v1/me/player/play"
                assert json.loads(request.content) == {
                    "context_uri": "spotify:album:smoke-album",
                    "offset": {"uri": track["uri"]},
                }
                playing = True
                return httpx.Response(204)
            if request.url.path == "/v1/me/player":
                return httpx.Response(
                    200,
                    json={
                        "item": track if playing else None,
                        "is_playing": playing,
                        "device": {"name": "Mock device"},
                    },
                )
            assert request.url.path == "/v1/search"
            return httpx.Response(
                200,
                json={
                    "tracks": {
                        "offset": 0,
                        "limit": 10,
                        "total": 1,
                        "items": [track],
                    }
                },
            )

        app = ObeliskApp(
            settings=load_settings(path),
            config_file=path,
            http=httpx.AsyncClient(transport=httpx.MockTransport(catalog)),
        )
        async with app.run_test(size=(80, 24)) as pilot:
            assert app.query_one("#track").region.width > 0
            assert app.query_one("#device").region.bottom <= 24
            assert app.theme == "mono"
            app.session._install(
                Token(access_token="mock-smoke-access", expires_in=3600)
            )
            await pilot.press("slash")
            assert isinstance(app.screen, CatalogScreen)
            app.screen.query_one(Input).value = "smoke"
            await pilot.pause(0.4)
            await app.workers.wait_for_complete()
            assert app.screen.query_one(CatalogTable).row_count == 1
            assert app.screen.entries[0][1].name == "[red]Smoke track[/red]"
            await pilot.press("down", "enter")
            await app.workers.wait_for_complete()
            assert (
                playing
                and app.screen_stack[0].query_one(NowPlaying).playing_uri
                == track["uri"]
            )
            await pilot.press("escape")
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
        check_config([str(root / "venv" / "bin" / "obelisk")], root, env)
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

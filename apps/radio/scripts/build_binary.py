"""Build, smoke-test and archive a native PyInstaller executable."""

from __future__ import annotations

import argparse
import importlib.metadata
import os
import subprocess
import sys
from pathlib import Path

from release import ROOT, create_archive, native_target, project_version, verify_tag
from smoke_binary import smoke


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "release")
    args = parser.parse_args()
    verify_tag(args.tag)
    if args.target != native_target():
        parser.error(
            "Build on the target OS and architecture; cross-compilation is not supported"
        )
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=10
    ).strip()
    epoch = int(
        subprocess.check_output(
            ["git", "show", "-s", "--format=%ct", "HEAD"],
            cwd=ROOT,
            text=True,
            timeout=10,
        )
    )
    work = ROOT / "build" / "native" / args.target
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("SPOTIFY_", "OBELISK_"))
        and key not in ("PYTHONPATH", "PYTHONHOME")
    }
    env.update(
        PYTHONHASHSEED="0",
        SOURCE_DATE_EPOCH=str(epoch),
        PYINSTALLER_CONFIG_DIR=str(work / "cache"),
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            "--noupx",
            "--name",
            "obelisk-radio",
            "--paths",
            str(ROOT / "src"),
            "--collect-data",
            "textual",
            "--collect-data",
            "obelisk_radio",
            "--recursive-copy-metadata",
            "obelisk-radio",
            "--exclude-module",
            "pytest",
            "--exclude-module",
            "PyInstaller",
            "--exclude-module",
            "hatchling",
            "--specpath",
            str(work),
            "--workpath",
            str(work / "work"),
            "--distpath",
            str(work / "dist"),
            str(ROOT / "packaging" / "entrypoint.py"),
        ],
        cwd=ROOT,
        env=env,
        check=True,
        timeout=900,
    )
    binary = work / "dist" / "obelisk-radio"
    smoke(binary, project_version())
    archive = create_archive(
        binary,
        args.output,
        args.tag,
        commit,
        epoch,
        importlib.metadata.version("pyinstaller"),
    )
    print(f"Native release archive: {archive}")


if __name__ == "__main__":
    main()

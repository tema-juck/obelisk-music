"""Test the real executable outside the checkout, including terminal startup/quit."""

from __future__ import annotations

import argparse
import errno
import fcntl
import os
import pty
import select
import shutil
import signal
import struct
import subprocess
import tempfile
import termios
import time
from pathlib import Path

from release import binary_target, native_target, project_version
from smoke_config import check_config


def smoke(binary: Path, version: str) -> None:
    binary = binary.resolve(strict=True)
    with binary.open("rb") as stream:
        if binary_target(stream.read(64)) != native_target():
            raise ValueError("Smoke tests must run on the executable target platform")
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(
            (
                "SPOTIFY_",
                "OBELISK_",
                "PYTHON",
                "UV_",
                "_PYI",
                "PYINSTALLER",
                "DYLD_",
                "LD_",
            )
        )
    }
    env["TERM"] = "xterm-256color"
    with tempfile.TemporaryDirectory(prefix="obelisk-binary-smoke-") as directory:
        root = Path(directory).resolve()
        env["XDG_CONFIG_HOME"] = str(root / "config-home")
        executable = root / "obelisk"
        shutil.copyfile(binary, executable)
        executable.chmod(0o755)
        for option in ("--help", "--version"):
            result = subprocess.run(
                [str(executable), option],
                cwd=root,
                env=env,
                check=False,
                text=True,
                capture_output=True,
                timeout=30,
            )
            if result.returncode:
                raise ValueError(
                    f"Executable {option} failed: {(result.stdout + result.stderr)[-4000:]}"
                )
            if option == "--version" and result.stdout.strip() != f"obelisk {version}":
                raise ValueError("Executable version does not match package metadata")
            if option == "--help" and not all(
                text in result.stdout
                for text in ("usage: obelisk", "--help", "--version")
            ):
                raise ValueError("Executable help is incomplete")
        check_config([str(executable)], root, env)
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        process = None
        output = bytearray()
        sent_quit = False
        try:
            process = subprocess.Popen(
                [str(executable)],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=root,
                env=env,
                start_new_session=True,
            )
            os.close(slave)
            slave = None
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.1)
                if ready:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError as exc:
                        if exc.errno != errno.EIO:
                            raise
                        chunk = b""
                    output.extend(chunk)
                if len(output) > 2 * 1024 * 1024:
                    raise ValueError("Executable produced excessive terminal output")
                if not sent_quit and all(
                    marker in output
                    for marker in (b"OBELISK", b"PLAYLISTS", b"SPOTIFY_CLIENT_ID")
                ):
                    os.write(master, b"q")
                    sent_quit = True
                if process.poll() is not None:
                    break
            else:
                raise TimeoutError("Executable startup/quit exceeded 45 seconds")
            if process.returncode != 0 or not sent_quit or b"Traceback" in output:
                raise ValueError(
                    f"Executable terminal smoke failed: {output.decode(errors='replace')[-4000:]}"
                )
        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            os.close(master)
            if slave is not None:
                os.close(slave)
    print(f"Native executable smoke passed: {native_target()}, obelisk {version}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    args = parser.parse_args()
    smoke(args.binary, project_version())


if __name__ == "__main__":
    main()

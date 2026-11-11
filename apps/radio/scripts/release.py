"""Validate release inputs and archives before any GitHub release write."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import platform
import re
import tarfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ("darwin-arm64", "darwin-amd64", "linux-amd64")
MAX_ARCHIVE = 256 * 1024 * 1024
TAG_PATTERN = r"radio-v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(alpha|beta|rc)\.(0|[1-9]\d*))?"


def project_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def verify_tag(tag: str, version: str | None = None) -> bool:
    match = re.fullmatch(TAG_PATTERN, tag)
    if not match:
        raise ValueError("Use radio-vX.Y.Z or radio-vX.Y.Z-alpha.N / beta.N / rc.N")
    major, minor, patch, stage, number = match.groups()
    normalized = f"{major}.{minor}.{patch}"
    if stage:
        normalized += {"alpha": "a", "beta": "b", "rc": "rc"}[stage] + number
    if normalized != (project_version() if version is None else version):
        raise ValueError("Git tag does not match pyproject.toml version")
    return stage is not None


def native_target() -> str:
    target = f"{platform.system().lower()}-" + {
        "arm64": "arm64",
        "aarch64": "arm64",
        "x86_64": "amd64",
        "AMD64": "amd64",
    }.get(platform.machine(), platform.machine())
    if target not in TARGETS:
        raise ValueError(f"Unsupported native build platform: {target}")
    return target


def binary_target(header: bytes) -> str:
    if header[:4] == b"\xcf\xfa\xed\xfe" and len(header) >= 8:
        cpu = int.from_bytes(header[4:8], "little")
        if cpu in (0x1000007, 0x100000C):
            return "darwin-" + ("amd64" if cpu == 0x1000007 else "arm64")
    if (
        len(header) >= 20
        and header[:6] == b"\x7fELF\x02\x01"
        and int.from_bytes(header[18:20], "little") == 62
    ):
        return "linux-amd64"
    raise ValueError("Unsupported executable architecture or file format")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def archive_name(tag: str, target: str) -> str:
    verify_tag(tag)
    if target not in TARGETS:
        raise ValueError("Unexpected release platform")
    return f"obelisk-radio-{tag.removeprefix('radio-')}-{target}.tar.gz"


def create_archive(
    binary: Path, output: Path, tag: str, commit: str, epoch: int, pyinstaller: str
) -> Path:
    target = native_target()
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or epoch < 0:
        raise ValueError("A full source commit and nonnegative timestamp are required")
    with binary.open("rb") as stream:
        if binary_target(stream.read(64)) != target:
            raise ValueError(
                "Executable architecture does not match the native builder"
            )
    metadata = {
        "tag": tag,
        "version": project_version(),
        "target": target,
        "commit": commit,
        "python": platform.python_version(),
        "pyinstaller": pyinstaller,
        "binary_sha256": sha256(binary),
    }
    output.mkdir(parents=True, exist_ok=True)
    archive = output / archive_name(tag, target)
    with (
        archive.open("wb") as destination,
        gzip.GzipFile(
            fileobj=destination, mode="wb", filename="", mtime=0
        ) as compressed,
        tarfile.open(
            fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT
        ) as bundle,
    ):
        for name, content, mode in [
            ("obelisk-radio", binary.read_bytes(), 0o755),
            ("LICENSE", (ROOT / "LICENSE").read_bytes(), 0o644),
            (
                "build-info.json",
                (json.dumps(metadata, sort_keys=True) + "\n").encode(),
                0o644,
            ),
        ]:
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(content), mode, epoch
            bundle.addfile(info, io.BytesIO(content))
    return archive


def validate_archive(path: Path, tag: str, target: str, commit: str) -> None:
    if (
        path.name != archive_name(tag, target)
        or path.is_symlink()
        or path.stat().st_size > MAX_ARCHIVE
    ):
        raise ValueError("Unexpected archive name, link, or size")
    with gzip.open(path, "rb") as stream:
        raw = stream.read(MAX_ARCHIVE + 1)
    if len(raw) > MAX_ARCHIVE:
        raise ValueError("Archive exceeds the uncompressed size limit")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as bundle:
        members = {}
        for member in bundle:
            if (
                member.name not in {"obelisk-radio", "LICENSE", "build-info.json"}
                or member.name in members
                or not member.isfile()
            ):
                raise ValueError(
                    "Archive contains an unexpected member, duplicate, or link"
                )
            if member.size < 0 or member.size > MAX_ARCHIVE:
                raise ValueError("Invalid member size")
            members[member.name] = member
        if set(members) != {"obelisk-radio", "LICENSE", "build-info.json"}:
            raise ValueError("Archive is missing required members")
        if (
            members["build-info.json"].size > 16_384
            or members["LICENSE"].size > 65_536
            or members["obelisk-radio"].mode != 0o755
        ):
            raise ValueError("Invalid metadata size or executable permissions")
        with bundle.extractfile(members["build-info.json"]) as stream:
            metadata = json.load(stream)
        with bundle.extractfile(members["LICENSE"]) as stream:
            if stream.read() != (ROOT / "LICENSE").read_bytes():
                raise ValueError("Unexpected license content")
        with bundle.extractfile(members["obelisk-radio"]) as stream:
            if binary_target(stream.read(64)) != target:
                raise ValueError("Executable platform does not match the archive name")
            stream.seek(0)
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        release_dependencies = tomllib.loads((ROOT / "pyproject.toml").read_text())[
            "dependency-groups"
        ]["release"]
        pyinstaller = next(
            item.split("==")[1]
            for item in release_dependencies
            if item.startswith("pyinstaller==")
        )
        expected = {
            "tag": tag,
            "version": project_version(),
            "target": target,
            "commit": commit,
            "python": platform.python_version(),
            "pyinstaller": pyinstaller,
            "binary_sha256": digest,
        }
        if metadata != expected:
            raise ValueError(
                "Build metadata or executable checksum does not match the release"
            )


def validate_set(directory: Path, tag: str, commit: str) -> list[Path]:
    verify_tag(tag)
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("A full source commit is required")
    archives = [directory / archive_name(tag, target) for target in TARGETS]
    names = {path.name for path in directory.iterdir()}
    if names - {"SHA256SUMS"} != {path.name for path in archives}:
        raise ValueError("Expected exactly three native release archives")
    for archive, target in zip(archives, TARGETS, strict=True):
        validate_archive(archive, tag, target, commit)
    checksums = directory / "SHA256SUMS"
    content = "".join(f"{sha256(path)}  {path.name}\n" for path in sorted(archives))
    if checksums.is_symlink() or (
        checksums.exists() and checksums.read_text() != content
    ):
        raise ValueError("Existing SHA256SUMS does not match validated archives")
    checksums.write_text(content)
    return [*archives, checksums]


def publish(*args, **kwargs):
    raise ValueError(
        "Radio publication is disabled pending documented Spotify policy review; a separate reviewed change is required"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["verify-tag", "validate", "publish", "check-policy"]
    )
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit")
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    if args.command in ("publish", "check-policy"):
        publish()
    verify_tag(args.tag)
    if args.command == "verify-tag":
        print(f"{args.tag} matches Radio version {project_version()}")
    elif args.directory and args.commit:
        validate_set(args.directory, args.tag, args.commit)
    else:
        parser.error("--directory and --commit are required")


if __name__ == "__main__":
    main()

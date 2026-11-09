"""Validate release inputs and archives before any GitHub release write."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import platform
import re
import subprocess
import tarfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ("darwin-arm64", "darwin-amd64", "linux-amd64")
MAX_ARCHIVE = 256 * 1024 * 1024
TAG_PATTERN = (
    r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(alpha|beta|rc)\.(0|[1-9]\d*))?"
)


def project_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def verify_tag(tag: str, version: str | None = None) -> bool:
    match = re.fullmatch(TAG_PATTERN, tag)
    if not match:
        raise ValueError("Use vX.Y.Z or vX.Y.Z-alpha.N / beta.N / rc.N")
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
    return f"obelisk-{tag}-{target}.tar.gz"


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
            ("obelisk", binary.read_bytes(), 0o755),
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
                member.name not in {"obelisk", "LICENSE", "build-info.json"}
                or member.name in members
                or not member.isfile()
            ):
                raise ValueError(
                    "Archive contains an unexpected member, duplicate, or link"
                )
            if member.size < 0 or member.size > MAX_ARCHIVE:
                raise ValueError("Invalid member size")
            members[member.name] = member
        if set(members) != {"obelisk", "LICENSE", "build-info.json"}:
            raise ValueError("Archive is missing required members")
        if (
            members["build-info.json"].size > 16_384
            or members["LICENSE"].size > 65_536
            or members["obelisk"].mode != 0o755
        ):
            raise ValueError("Invalid metadata size or executable permissions")
        with bundle.extractfile(members["build-info.json"]) as stream:
            metadata = json.load(stream)
        with bundle.extractfile(members["LICENSE"]) as stream:
            if stream.read() != (ROOT / "LICENSE").read_bytes():
                raise ValueError("Unexpected license content")
        with bundle.extractfile(members["obelisk"]) as stream:
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


def gh(*args: str) -> str:
    result = subprocess.run(
        ["gh", *args], check=True, text=True, stdout=subprocess.PIPE, timeout=300
    )
    return result.stdout


def github_api(path: str, method: str = "GET", *fields: str) -> dict | list:
    return json.loads(
        gh("api", "--hostname", "github.com", "--method", method, path, *fields)
    )


def find_release(repository: str, tag: str) -> dict | None:
    # The tag endpoint only finds published releases; list includes visible drafts.
    pages = github_api(
        f"repos/{repository}/releases?per_page=100", "GET", "--paginate", "--slurp"
    )
    matches = [item for page in pages for item in page if item["tag_name"] == tag]
    if len(matches) > 1:
        raise ValueError("Multiple releases have this tag; refusing ambiguous retry")
    return matches[0] if matches else None


def missing_assets(release: dict, files: list[Path], tag: str) -> list[Path]:
    if (
        release["tag_name"] != tag
        or release["prerelease"] != verify_tag(tag)
        or not isinstance(release["draft"], bool)
    ):
        raise ValueError("Release metadata does not match the validated tag")
    expected = {
        path.name: (path.stat().st_size, "sha256:" + sha256(path)) for path in files
    }
    assets = release["assets"]
    actual = {asset["name"]: (asset["size"], asset.get("digest")) for asset in assets}
    if (
        len(actual) != len(assets)
        or any(asset["state"] != "uploaded" for asset in assets)
        or any(
            name not in expected or value != expected[name]
            for name, value in actual.items()
        )
    ):
        raise ValueError(
            "Existing release assets failed verification; no assets replaced"
        )
    return [path for path in files if path.name not in actual]


def verify_remote_tag(repository: str, tag: str, commit: str) -> None:
    reference = json.loads(
        gh(
            "api",
            "--hostname",
            "github.com",
            "--method",
            "GET",
            f"repos/{repository}/git/ref/tags/{tag}",
        )
    )["object"]
    for _ in range(5):
        if reference["type"] == "commit" and reference["sha"] == commit:
            return
        if reference["type"] != "tag" or not re.fullmatch(
            r"[0-9a-f]{40}", reference["sha"]
        ):
            break
        reference = json.loads(
            gh(
                "api",
                "--hostname",
                "github.com",
                "--method",
                "GET",
                f"repos/{repository}/git/tags/{reference['sha']}",
            )
        )["object"]
    raise ValueError("Remote tag moved or does not resolve to the build commit")


def publish(directory: Path, tag: str, commit: str, repository: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid GitHub repository")
    files = validate_set(directory, tag, commit)
    prerelease = verify_tag(tag)
    verify_remote_tag(repository, tag, commit)
    release = find_release(repository, tag)
    if release is None:
        verify_remote_tag(repository, tag, commit)
        release = github_api(
            f"repos/{repository}/releases",
            "POST",
            "-f",
            f"tag_name={tag}",
            "-f",
            f"target_commitish={commit}",
            "-F",
            "draft=true",
            "-F",
            f"prerelease={str(prerelease).lower()}",
            "-f",
            f"name=Obelisk Music {tag}",
            "-f",
            "body=Standalone Spotify Connect controller. See README for installation, checksums, and platform limitations.",
        )
    missing = missing_assets(release, files, tag)
    if not release["draft"]:
        if missing:
            raise ValueError("Published release is incomplete; refusing any changes")
        print(f"{tag} is already published with matching assets; unchanged")
        return
    release_id = release["id"]
    if type(release_id) is not int or release_id <= 0:
        raise ValueError("Invalid release ID")
    endpoint = f"repos/{repository}/releases/{release_id}"
    for path in missing:
        current = github_api(endpoint)
        remaining = missing_assets(current, files, tag)
        if not current["draft"]:
            raise ValueError("Release was published during upload; refusing changes")
        if path in remaining:
            # No --clobber: an uncertain upload is reconciled on the next invocation.
            gh(
                "release",
                "upload",
                tag,
                str(path),
                "--repo",
                f"github.com/{repository}",
            )
    current = github_api(endpoint)
    if missing_assets(current, files, tag) or not current["draft"]:
        raise ValueError("Draft assets failed verification; no publication attempted")
    verify_remote_tag(repository, tag, commit)
    github_api(
        endpoint,
        "PATCH",
        "-F",
        "draft=false",
        "-F",
        f"prerelease={str(prerelease).lower()}",
        "-f",
        f"make_latest={str(not prerelease).lower()}",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["verify-tag", "validate", "publish"])
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit")
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--repository")
    args = parser.parse_args()
    verify_tag(args.tag)
    if args.command == "verify-tag":
        print(f"{args.tag} matches package version {project_version()}")
    elif not args.directory or not args.commit:
        parser.error("--directory and --commit are required")
    elif args.command == "validate":
        validate_set(args.directory, args.tag, args.commit)
    elif not args.repository:
        parser.error("--repository is required to publish")
    else:
        publish(args.directory, args.tag, args.commit, args.repository)


if __name__ == "__main__":
    main()

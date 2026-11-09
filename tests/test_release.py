"""Release boundary tests use synthetic archives and mocked GitHub calls only."""

import gzip
import hashlib
import importlib.util
import io
import json
import platform
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "release_tools", ROOT / "scripts" / "release.py"
)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
TAG = "v0.2.0-alpha.1"
COMMIT = "a" * 40


def binary(target):
    if target.startswith("darwin"):
        cpu = 0x100000C if target.endswith("arm64") else 0x1000007
        return b"\xcf\xfa\xed\xfe" + cpu.to_bytes(4, "little") + b"\0" * 56
    return b"\x7fELF\x02\x01" + b"\0" * 12 + (62).to_bytes(2, "little") + b"\0" * 44


def make_archive(
    directory,
    target,
    *,
    metadata_changes=None,
    extra=None,
    binary_target=None,
    link=False,
):
    content = binary(binary_target or target)
    metadata = {
        "tag": TAG,
        "version": release.project_version(),
        "target": target,
        "commit": COMMIT,
        "python": platform.python_version(),
        "pyinstaller": "6.22.3",
        "binary_sha256": hashlib.sha256(content).hexdigest(),
    }
    metadata.update(metadata_changes or {})
    path = directory / release.archive_name(TAG, target)
    with tarfile.open(path, "w:gz") as archive:
        values = [
            ("obelisk", content),
            ("LICENSE", (ROOT / "LICENSE").read_bytes()),
            ("build-info.json", json.dumps(metadata).encode()),
        ]
        if extra:
            values.append((extra, b"not allowed"))
        for name, value in values:
            info = tarfile.TarInfo(name)
            info.size = len(value)
            info.mode = 0o755 if name == "obelisk" else 0o644
            if link and name == "obelisk":
                info.type = tarfile.SYMTYPE
                info.linkname = "/tmp/elsewhere"
                info.size = 0
            archive.addfile(info, io.BytesIO(value))
    return path


@pytest.mark.parametrize(
    ("tag", "version", "prerelease"),
    [
        (TAG, "0.2.0a1", True),
        ("v1.0.0-beta.2", "1.0.0b2", True),
        ("v1.0.0-rc.3", "1.0.0rc3", True),
        ("v1.0.0", "1.0.0", False),
    ],
)
def test_tag_versions(tag, version, prerelease):
    assert release.verify_tag(tag, version) is prerelease


@pytest.mark.parametrize(
    "tag",
    [
        "v0.2.0",
        "v0.2.0-alpha.2",
        "0.2.0-alpha.1",
        "v00.2.0-alpha.1",
        "v0.2.0-alpha.01",
        "v0.2.0-dev.1",
        "v0.2.0-alpha.1;bad",
    ],
)
def test_invalid_or_mismatched_tag_is_rejected(tag):
    with pytest.raises(ValueError):
        release.verify_tag(tag)


@pytest.mark.parametrize("target", release.TARGETS)
def test_valid_archives_and_binary_headers(tmp_path, target):
    path = make_archive(tmp_path, target)
    release.validate_archive(path, TAG, target, COMMIT)
    assert release.binary_target(binary(target)) == target


@pytest.mark.parametrize(
    "change",
    [
        {"tag": "v9.9.9"},
        {"version": "9.9.9"},
        {"commit": "b" * 40},
        {"target": "linux-amd64"},
        {"binary_sha256": "0" * 64},
        {"python": "0.0.0"},
        {"pyinstaller": "0.0.0"},
    ],
)
def test_manifest_mismatch_is_rejected(tmp_path, change):
    path = make_archive(tmp_path, "darwin-arm64", metadata_changes=change)
    with pytest.raises(ValueError, match="metadata|checksum"):
        release.validate_archive(path, TAG, "darwin-arm64", COMMIT)


@pytest.mark.parametrize("extra", ["../escape", ".env", "unexpected.py", "obelisk"])
def test_extra_paths_secrets_and_duplicates_are_rejected(tmp_path, extra):
    path = make_archive(tmp_path, "darwin-arm64", extra=extra)
    with pytest.raises(ValueError, match="unexpected member"):
        release.validate_archive(path, TAG, "darwin-arm64", COMMIT)


def test_links_and_wrong_architecture_are_rejected(tmp_path):
    path = make_archive(tmp_path, "darwin-arm64", link=True)
    with pytest.raises(ValueError, match="link"):
        release.validate_archive(path, TAG, "darwin-arm64", COMMIT)
    path = make_archive(tmp_path, "darwin-arm64", binary_target="linux-amd64")
    with pytest.raises(ValueError, match="platform"):
        release.validate_archive(path, TAG, "darwin-arm64", COMMIT)


def test_compressed_archive_size_is_bounded(tmp_path, monkeypatch):
    path = make_archive(tmp_path, "darwin-arm64")
    monkeypatch.setattr(release, "MAX_ARCHIVE", path.stat().st_size - 1)
    with pytest.raises(ValueError, match="size"):
        release.validate_archive(path, TAG, "darwin-arm64", COMMIT)


def test_decompression_is_bounded_before_tar_parsing(tmp_path, monkeypatch):
    path = tmp_path / release.archive_name(TAG, "darwin-arm64")
    path.write_bytes(gzip.compress(b"\0" * 10000))
    monkeypatch.setattr(release, "MAX_ARCHIVE", 1000)
    with pytest.raises(ValueError, match="uncompressed"):
        release.validate_archive(path, TAG, "darwin-arm64", COMMIT)


def test_complete_set_is_required_and_checksums_are_generated(tmp_path):
    make_archive(tmp_path, "darwin-arm64")
    with pytest.raises(ValueError, match="exactly three"):
        release.validate_set(tmp_path, TAG, COMMIT)
    for target in release.TARGETS[1:]:
        make_archive(tmp_path, target)
    files = release.validate_set(tmp_path, TAG, COMMIT)
    checksums = files[-1].read_text().splitlines()
    assert len(checksums) == 3
    for line in checksums:
        digest, name = line.split("  ")
        assert digest == hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()


def test_archive_container_is_deterministic(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "native_target", lambda: "darwin-arm64")
    executable = tmp_path / "obelisk"
    executable.write_bytes(binary("darwin-arm64"))
    first = release.create_archive(
        executable, tmp_path / "first", TAG, COMMIT, 100, "6.22.3"
    )
    second = release.create_archive(
        executable, tmp_path / "second", TAG, COMMIT, 100, "6.22.3"
    )
    assert first.read_bytes() == second.read_bytes()


def prepare_set(tmp_path):
    for target in release.TARGETS:
        make_archive(tmp_path, target)


def test_publish_waits_for_verified_assets_and_handles_annotated_tag(
    tmp_path, monkeypatch
):
    prepare_set(tmp_path)
    calls = []

    def fake_gh(*args):
        calls.append(args)
        if args[0] == "api" and "/git/ref/tags/" in args[-1]:
            return json.dumps({"object": {"type": "tag", "sha": "b" * 40}})
        if args[0] == "api" and "/git/tags/" in args[-1]:
            return json.dumps({"object": {"type": "commit", "sha": COMMIT}})
        if args[0] == "api":
            return json.dumps(
                {
                    "draft": True,
                    "prerelease": True,
                    "tag_name": TAG,
                    "assets": [
                        {
                            "name": p.name,
                            "size": p.stat().st_size,
                            "digest": "sha256:" + release.sha256(p),
                        }
                        for p in tmp_path.iterdir()
                    ],
                }
            )
        return ""

    monkeypatch.setattr(release, "gh", fake_gh)
    release.publish(tmp_path, TAG, COMMIT, "owner/repo")
    creates = [c for c in calls if c[:2] == ("release", "create")]
    assert (
        len(creates) == 1
        and "--draft" in creates[0]
        and "--verify-tag" in creates[0]
        and "--prerelease" in creates[0]
    )
    assert "github.com/owner/repo" in creates[0]
    assert (
        calls[-1][:2] == ("release", "edit")
        and "--draft=false" in calls[-1]
        and "--latest=false" in calls[-1]
    )


@pytest.mark.parametrize(
    "failure", ["upload", "missing-assets", "wrong-digest", "moved-tag"]
)
def test_publish_failures_never_publish_a_partial_release(
    tmp_path, monkeypatch, failure
):
    prepare_set(tmp_path)
    calls = []

    def fake_gh(*args):
        calls.append(args)
        if args[0] == "api" and "/git/ref/tags/" in args[-1]:
            return json.dumps(
                {
                    "object": {
                        "type": "commit",
                        "sha": "b" * 40 if failure == "moved-tag" else COMMIT,
                    }
                }
            )
        if args[:2] == ("release", "create") and failure == "upload":
            raise subprocess.CalledProcessError(1, args)
        if args[0] == "api":
            assets = (
                []
                if failure == "missing-assets"
                else [
                    {"name": p.name, "size": p.stat().st_size, "digest": "sha256:wrong"}
                    for p in tmp_path.iterdir()
                ]
            )
            return json.dumps(
                {"draft": True, "prerelease": True, "tag_name": TAG, "assets": assets}
            )
        return ""

    monkeypatch.setattr(release, "gh", fake_gh)
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        release.publish(tmp_path, TAG, COMMIT, "owner/repo")
    assert not any(c[:2] == ("release", "edit") for c in calls)
    if failure == "moved-tag":
        assert not any(c[:2] == ("release", "create") for c in calls)


def test_invalid_archives_never_invoke_github(tmp_path, monkeypatch):
    def forbidden(*args):
        raise AssertionError("GitHub called before artifact validation")

    monkeypatch.setattr(release, "gh", forbidden)
    with pytest.raises(ValueError, match="exactly three"):
        release.publish(tmp_path, TAG, COMMIT, "owner/repo")


def test_remote_tag_moving_after_upload_leaves_draft_unpublished(tmp_path, monkeypatch):
    prepare_set(tmp_path)
    calls = []

    def fake_gh(*args):
        calls.append(args)
        if args[0] == "api" and "/git/ref/tags/" in args[-1]:
            uploaded = any(call[:2] == ("release", "create") for call in calls)
            return json.dumps(
                {"object": {"type": "commit", "sha": "b" * 40 if uploaded else COMMIT}}
            )
        if args[0] == "api":
            return json.dumps(
                {
                    "draft": True,
                    "prerelease": True,
                    "tag_name": TAG,
                    "assets": [
                        {
                            "name": p.name,
                            "size": p.stat().st_size,
                            "digest": "sha256:" + release.sha256(p),
                        }
                        for p in tmp_path.iterdir()
                    ],
                }
            )
        return ""

    monkeypatch.setattr(release, "gh", fake_gh)
    with pytest.raises(ValueError, match="Remote tag moved"):
        release.publish(tmp_path, TAG, COMMIT, "owner/repo")
    assert any(call[:2] == ("release", "create") for call in calls)
    assert not any(call[:2] == ("release", "edit") for call in calls)


def test_binary_smoke_failure_reports_diagnostics(tmp_path, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "release", release)
    spec = importlib.util.spec_from_file_location(
        "smoke_binary", ROOT / "scripts" / "smoke_binary.py"
    )
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    monkeypatch.setattr(smoke, "native_target", lambda: "darwin-arm64")
    executable = tmp_path / "obelisk"
    executable.write_bytes(binary("darwin-arm64"))
    monkeypatch.setattr(
        smoke.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 1, "", "missing resource"
        ),
    )
    with pytest.raises(ValueError, match="missing resource"):
        smoke.smoke(executable, release.project_version())

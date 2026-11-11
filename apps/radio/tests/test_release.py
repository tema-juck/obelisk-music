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
TAG = "radio-v0.2.0-alpha.1"
COMMIT = "a" * 40


@pytest.fixture(autouse=True)
def synthetic_package_version(monkeypatch):
    # Boundary fixtures must not depend on the next application release number.
    monkeypatch.setattr(release, "project_version", lambda: "0.2.0a1")


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
            ("obelisk-radio", content),
            ("LICENSE", (ROOT / "LICENSE").read_bytes()),
            ("build-info.json", json.dumps(metadata).encode()),
        ]
        if extra:
            values.append((extra, b"not allowed"))
        for name, value in values:
            info = tarfile.TarInfo(name)
            info.size = len(value)
            info.mode = 0o755 if name == "obelisk-radio" else 0o644
            if link and name == "obelisk-radio":
                info.type = tarfile.SYMTYPE
                info.linkname = "/tmp/elsewhere"
                info.size = 0
            archive.addfile(info, io.BytesIO(value))
    return path


@pytest.mark.parametrize(
    ("tag", "version", "prerelease"),
    [
        (TAG, "0.2.0a1", True),
        ("radio-v0.2.0-alpha.2", "0.2.0a2", True),
        ("radio-v0.2.0-alpha.3", "0.2.0a3", True),
        ("radio-v0.2.0-alpha.4", "0.2.0a4", True),
        ("radio-v0.3.0-alpha.1", "0.3.0a1", True),
        ("radio-v0.3.0-alpha.2", "0.3.0a2", True),
        ("radio-v1.0.0-beta.2", "1.0.0b2", True),
        ("radio-v1.0.0-rc.3", "1.0.0rc3", True),
        ("radio-v1.0.0", "1.0.0", False),
    ],
)
def test_tag_versions(tag, version, prerelease):
    assert release.verify_tag(tag, version) is prerelease


@pytest.mark.parametrize(
    "tag",
    [
        "radio-v0.2.0",
        "radio-v0.2.0-alpha.2",
        "0.2.0-alpha.1",
        "radio-v00.2.0-alpha.1",
        "radio-v0.2.0-alpha.01",
        "radio-v0.2.0-dev.1",
        "radio-v0.2.0-alpha.1;bad",
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
        {"tag": "radio-v9.9.9"},
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


@pytest.mark.parametrize(
    "extra", ["../escape", ".env", "unexpected.py", "obelisk-radio"]
)
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
    executable = tmp_path / "obelisk-radio"
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


def test_validation_is_repeatable_and_rejects_modified_checksums(tmp_path):
    prepare_set(tmp_path)
    assert release.validate_set(tmp_path, TAG, COMMIT) == release.validate_set(
        tmp_path, TAG, COMMIT
    )
    (tmp_path / "SHA256SUMS").write_text("tampered")
    with pytest.raises(ValueError, match="SHA256SUMS"):
        release.validate_set(tmp_path, TAG, COMMIT)


def test_checksum_symlink_is_not_followed(tmp_path):
    prepare_set(tmp_path)
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.write_text("untouched")
    (tmp_path / "SHA256SUMS").symlink_to(outside)
    with pytest.raises(ValueError, match="SHA256SUMS"):
        release.validate_set(tmp_path, TAG, COMMIT)
    assert outside.read_text() == "untouched"


def test_binary_smoke_failure_reports_diagnostics(tmp_path, monkeypatch):
    import sys

    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    monkeypatch.setitem(sys.modules, "release", release)
    spec = importlib.util.spec_from_file_location(
        "smoke_binary", ROOT / "scripts" / "smoke_binary.py"
    )
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    monkeypatch.setattr(smoke, "native_target", lambda: "darwin-arm64")
    executable = tmp_path / "obelisk-radio"
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


def test_publication_is_blocked_before_any_io(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("Policy-blocked publication attempted I/O")

    monkeypatch.setattr(release, "validate_set", blocked)
    with pytest.raises(ValueError, match="disabled pending"):
        release.publish()


@pytest.mark.parametrize("tag", ["v0.2.0-alpha.1", "player-v0.2.0-alpha.1"])
def test_player_tags_are_not_radio_tags(tag):
    with pytest.raises(ValueError):
        release.verify_tag(tag)


def test_wheel_smoke_rejects_player_resources(tmp_path):
    from zipfile import ZipFile

    spec = importlib.util.spec_from_file_location(
        "radio_wheel_smoke", ROOT / "scripts/smoke_wheel.py"
    )
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    wheel = tmp_path / "mixed.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr("obelisk_music/auth.py", "not Radio")
    with pytest.raises(SystemExit, match="other product"):
        smoke.check_wheel(wheel)


def test_policy_gate_blocks_tag_triggered_release_workflow(monkeypatch):
    import sys

    monkeypatch.setattr(
        sys, "argv", ["release.py", "check-policy", "--tag", "radio-v0.1.0-alpha.1"]
    )
    with pytest.raises(ValueError, match="disabled pending"):
        release.main()

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
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "release_tools", ROOT / "scripts" / "release.py"
)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
TAG = "v0.2.0-alpha.1"
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
        ("v0.2.0-alpha.2", "0.2.0a2", True),
        ("v0.2.0-alpha.3", "0.2.0a3", True),
        ("v0.2.0-alpha.4", "0.2.0a4", True),
        ("v0.3.0-alpha.1", "0.3.0a1", True),
        ("v0.3.0-alpha.2", "0.3.0a2", True),
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


def asset(path):
    return {
        "name": path.name,
        "size": path.stat().st_size,
        "digest": "sha256:" + release.sha256(path),
        "state": "uploaded",
    }


@pytest.fixture
def github(tmp_path, monkeypatch):
    prepare_set(tmp_path)
    files = release.validate_set(tmp_path, TAG, COMMIT)
    remote = SimpleNamespace(
        document=None,
        calls=[],
        files=files,
        error=None,
        upload_error=None,
        bad_upload=False,
        moved=False,
        interrupted_create=False,
        interrupted_patch=False,
        reads=0,
        on_read=lambda: None,
    )

    def fake_gh(*args):
        remote.calls.append(args)
        if args[:2] == ("release", "upload"):
            path = Path(args[3])
            assert "--clobber" not in args
            assert "github.com/owner/repo" in args
            assert remote.document["draft"]
            assert path.name not in {a["name"] for a in remote.document["assets"]}
            if remote.bad_upload == "missing":
                return ""
            remote.document["assets"].append(asset(path))
            if remote.bad_upload:
                remote.document["assets"][-1]["digest"] = "sha256:wrong"
            if remote.upload_error:
                raise remote.upload_error
            return ""
        assert args[:4] == ("api", "--hostname", "github.com", "--method")
        method, endpoint = args[4:6]
        if "/git/ref/tags/" in endpoint:
            return json.dumps({"object": {"type": "tag", "sha": "b" * 40}})
        if "/git/tags/" in endpoint:
            return json.dumps(
                {
                    "object": {
                        "type": "commit",
                        "sha": "c" * 40 if remote.moved else COMMIT,
                    }
                }
            )
        if "/releases/tags/" in endpoint:
            raise subprocess.CalledProcessError(
                1, args, stderr="gh: Not Found (HTTP 404)"
            )
        if endpoint.endswith("releases?per_page=100"):
            assert method == "GET" and "--paginate" in args and "--slurp" in args
            if remote.error:
                raise remote.error
            # A matching draft can be on a later page, behind unrelated releases.
            return json.dumps(
                [[{"tag_name": "v0.1.0"}], [remote.document] if remote.document else []]
            )
        if method == "POST":
            assert endpoint == "repos/owner/repo/releases"
            assert remote.document is None
            assert f"target_commitish={COMMIT}" in args
            assert f"name=Obelisk Player {TAG}" in args
            assert "draft=true" in args and "prerelease=true" in args
            remote.document = draft([])
            if remote.interrupted_create:
                raise subprocess.TimeoutExpired("gh", 300)
        else:
            assert endpoint == "repos/owner/repo/releases/17"
            if method == "PATCH":
                assert len(remote.document["assets"]) == 4
                assert "draft=false" in args and "make_latest=false" in args
                remote.document["draft"] = False
                if remote.interrupted_patch:
                    raise subprocess.TimeoutExpired("gh", 300)
            else:
                assert method == "GET"
                remote.reads += 1
                remote.on_read()
        return json.dumps(remote.document)

    monkeypatch.setattr(release, "gh", fake_gh)
    return remote


def draft(assets):
    return {
        "id": 17,
        "draft": True,
        "prerelease": True,
        "tag_name": TAG,
        "assets": assets,
    }


def mutations(remote):
    return [
        c
        for c in remote.calls
        if c[:2] == ("release", "upload") or (c[0] == "api" and c[4] != "GET")
    ]


def test_first_release_tag_lookup_404_does_not_hide_new_draft(github, tmp_path):
    release.publish(tmp_path, TAG, COMMIT, "owner/repo")
    assert github.document["draft"] is False
    assert len(github.document["assets"]) == 4
    assert sum(c[0] == "api" and c[4] == "POST" for c in github.calls) == 1
    assert not any("/releases/tags/" in str(c) for c in github.calls)
    assert github.calls[-1][4:6] == ("PATCH", "repos/owner/repo/releases/17")


@pytest.mark.parametrize("existing", [0, 2, 4])
def test_existing_draft_resumes_only_missing_assets(github, tmp_path, existing):
    github.document = draft([asset(p) for p in github.files[:existing]])
    release.publish(tmp_path, TAG, COMMIT, "owner/repo")
    assert github.document["draft"] is False
    assert not any(c[0] == "api" and c[4] == "POST" for c in github.calls)
    assert sum(c[:2] == ("release", "upload") for c in github.calls) == 4 - existing


def test_existing_published_release_is_unchanged(github, tmp_path, capsys):
    github.document = draft([asset(p) for p in github.files])
    github.document["draft"] = False
    release.publish(tmp_path, TAG, COMMIT, "owner/repo")
    assert not mutations(github)
    assert "already published" in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["digest", "extra", "duplicate", "starter"])
@pytest.mark.parametrize("published", [False, True])
def test_conflicting_assets_are_never_replaced(github, tmp_path, kind, published):
    github.document = draft([asset(p) for p in github.files])
    github.document["draft"] = not published
    assets = github.document["assets"]
    if kind == "digest":
        assets[0]["digest"] = "sha256:wrong"
    elif kind == "extra":
        assets.append({**assets[0], "name": "unexpected"})
    elif kind == "duplicate":
        assets.append(assets[0].copy())
    else:
        assets[0]["state"] = "starter"
    with pytest.raises(ValueError):
        release.publish(tmp_path, TAG, COMMIT, "owner/repo")
    assert not mutations(github)


def test_published_release_missing_assets_is_never_modified(github, tmp_path):
    github.document = draft([asset(p) for p in github.files[:-1]])
    github.document["draft"] = False
    with pytest.raises(ValueError, match="Published release is incomplete"):
        release.publish(tmp_path, TAG, COMMIT, "owner/repo")
    assert not mutations(github)


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 503])
def test_api_errors_are_not_treated_as_a_missing_release(github, tmp_path, status):
    error = subprocess.CalledProcessError(1, "gh", stderr=f"HTTP {status}")
    github.error = error
    with pytest.raises(subprocess.CalledProcessError) as exc:
        release.publish(tmp_path, TAG, COMMIT, "owner/repo")
    assert exc.value is error
    assert not mutations(github)


@pytest.mark.parametrize(
    "error",
    [
        OSError("network unavailable"),
        subprocess.TimeoutExpired("gh", 300),
        json.JSONDecodeError("invalid response", "", 0),
    ],
)
def test_transport_and_invalid_api_responses_stop_publication(github, tmp_path, error):
    github.error = error
    with pytest.raises(type(error)):
        release.publish(tmp_path, TAG, COMMIT, "owner/repo")
    assert not mutations(github)


def test_retry_after_upload_response_is_lost(github, tmp_path):
    github.upload_error = subprocess.TimeoutExpired("gh", 300)
    with pytest.raises(subprocess.TimeoutExpired):
        release.publish(tmp_path, TAG, COMMIT, "owner/repo")
    assert github.document["draft"] and len(github.document["assets"]) == 1
    github.upload_error = None
    release.publish(tmp_path, TAG, COMMIT, "owner/repo")
    assert github.document["draft"] is False
    assert sum(c[:2] == ("release", "upload") for c in github.calls) == 4


def test_retry_after_draft_creation_response_is_lost(github, tmp_path):
    github.interrupted_create = True
    with pytest.raises(subprocess.TimeoutExpired):
        release.publish(tmp_path, TAG, COMMIT, "owner/repo")
    assert github.document["draft"] and not github.document["assets"]
    release.publish(tmp_path, TAG, COMMIT, "owner/repo")
    assert github.document["draft"] is False
    assert sum(c[0] == "api" and c[4] == "POST" for c in github.calls) == 1


def test_retry_after_publication_response_is_lost(github, tmp_path):
    github.interrupted_patch = True
    with pytest.raises(subprocess.TimeoutExpired):
        release.publish(tmp_path, TAG, COMMIT, "owner/repo")
    assert github.document["draft"] is False
    previous_mutations = mutations(github)
    release.publish(tmp_path, TAG, COMMIT, "owner/repo")
    assert mutations(github) == previous_mutations


@pytest.mark.parametrize("failure", ["digest", "missing"])
def test_uploaded_asset_mismatch_prevents_publication(github, tmp_path, failure):
    github.bad_upload = failure
    with pytest.raises(ValueError, match="assets failed verification"):
        release.publish(tmp_path, TAG, COMMIT, "owner/repo")
    assert github.document["draft"]
    assert not any(c[0] == "api" and c[4] == "PATCH" for c in github.calls)


def test_tag_moved_before_creation_prevents_mutations(github, tmp_path):
    github.moved = True
    with pytest.raises(ValueError, match="tag moved"):
        release.publish(tmp_path, TAG, COMMIT, "owner/repo")
    assert not mutations(github)


def test_tag_moved_after_upload_leaves_draft_unpublished(github, tmp_path):
    github.on_read = lambda: setattr(github, "moved", True)
    with pytest.raises(ValueError, match="tag moved"):
        release.publish(tmp_path, TAG, COMMIT, "owner/repo")
    assert github.document["draft"]
    assert not any(c[0] == "api" and c[4] == "PATCH" for c in github.calls)


def test_tag_moved_during_discovery_prevents_draft_creation(
    github, tmp_path, monkeypatch
):
    original = release.find_release

    def moved_during_listing(repository, tag):
        result = original(repository, tag)
        github.moved = True
        return result

    monkeypatch.setattr(release, "find_release", moved_during_listing)
    with pytest.raises(ValueError, match="tag moved"):
        release.publish(tmp_path, TAG, COMMIT, "owner/repo")
    assert not mutations(github)


def test_release_published_during_upload_is_not_modified(github, tmp_path):
    github.on_read = lambda: github.document.update(draft=False)
    with pytest.raises(ValueError, match="published during upload"):
        release.publish(tmp_path, TAG, COMMIT, "owner/repo")
    assert not any(c[:2] == ("release", "upload") for c in github.calls)


def test_invalid_archives_never_invoke_github(tmp_path, monkeypatch):
    monkeypatch.setattr(
        release, "gh", lambda *args: pytest.fail("GitHub called before validation")
    )
    with pytest.raises(ValueError, match="exactly three"):
        release.publish(tmp_path, TAG, COMMIT, "owner/repo")


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


def test_multiple_matching_releases_prevent_publication(github, tmp_path, monkeypatch):
    original = release.gh

    def duplicated(*args):
        if "repos/owner/repo/releases?per_page=100" in args:
            return json.dumps([[draft([])], [draft([])]])
        return original(*args)

    monkeypatch.setattr(release, "gh", duplicated)
    with pytest.raises(ValueError, match="Multiple releases"):
        release.publish(tmp_path, TAG, COMMIT, "owner/repo")
    assert not mutations(github)


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

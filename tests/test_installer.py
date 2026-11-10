"""Run the real POSIX scripts with offline platform and download fixtures."""

import gzip
import hashlib
import io
import os
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ("darwin-arm64", "darwin-amd64", "linux-amd64")
STABLE = "v1.2.3"
PRE = "v0.2.0-alpha.1"


def make_archive(path, *, extra=None, kind=None, omit=None, content=None):
    content = content or b'#!/bin/sh\necho executed > "$HOME/executed"\n'
    with tarfile.open(path, "w:gz", format=tarfile.USTAR_FORMAT) as archive:
        values = [("obelisk", content), ("LICENSE", b"MIT"), ("build-info.json", b"{}")]
        if extra:
            values.append((extra, b"unexpected"))
        for name, data in values:
            if name == omit:
                continue
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if name == "obelisk" else 0o644
            if kind and name == "obelisk":
                info.type = kind
                info.linkname = "LICENSE"
                info.size = 0
            archive.addfile(info, io.BytesIO(data))
    return content


def checksums(directory):
    lines = []
    for path in sorted(directory.glob("*.tar.gz")):
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n")
    (directory / "SHA256SUMS").write_text("".join(lines))


@pytest.fixture
def installer(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    temporary = tmp_path / "private temporary"
    temporary.mkdir()
    fixtures = tmp_path / "releases"
    for tag in (STABLE, PRE):
        directory = fixtures / tag
        directory.mkdir(parents=True)
        for target in TARGETS:
            make_archive(directory / f"obelisk-{tag}-{target}.tar.gz")
        checksums(directory)
    fake_bin = tmp_path / "tools"
    fake_bin.mkdir()
    mock = fake_bin / "mock"
    mock.write_text(
        f"#!{sys.executable}\n"
        + """import json, os, shutil, sys
from pathlib import Path
args = sys.argv[1:]
if Path(sys.argv[0]).name == "uname":
    print(os.environ["TEST_OS"] if args == ["-s"] else os.environ["TEST_ARCH"])
    sys.exit(0)
url = args[-1]
with open(os.environ["TEST_REQUESTS"], "a") as log:
    log.write(url + "\\n")
assert args[0] == "-q"
assert args[args.index("--proto") + 1] == "=https"
assert args[args.index("--proto-redir") + 1] == "=https"
output = Path(args[args.index("--output") + 1])
if os.environ.get("TEST_FAIL_DOWNLOAD") == "1":
    sys.exit(22)
root = Path(os.environ["TEST_RELEASES"])
api = "https://api.github.com/repos/tema-juck/obelisk-player/releases"
base = "https://github.com/tema-juck/obelisk-player/releases/download/"
if url.startswith(api):
    if url.endswith("/latest"):
        if os.environ.get("TEST_NO_STABLE"):
            sys.exit(22)
        tag = "v1.2.3"
    elif url.endswith("?per_page=1"):
        tag = "v0.2.0-alpha.1"
    else:
        assert url.startswith(api + "/tags/")
        tag = url.removeprefix(api + "/tags/")
    data = {"tag_name": tag, "draft": False, "prerelease": "-" in tag,
            "body": 'A quoted "tag_name": "v9.9.9" is untrusted text.'}
    output.write_text(json.dumps([data] if "?" in url else data))
else:
    assert url.startswith(base), url
    tag, name = url.removeprefix(base).split("/")
    shutil.copyfile(root / tag / name, output)
    if os.environ.get("TEST_CHANGE_BINARY") and name.endswith(".tar.gz"):
        Path(os.environ["TEST_CHANGE_BINARY"]).write_text("changed during download")
"""
    )
    mock.chmod(0o755)
    for name in ("curl", "uname"):
        (fake_bin / name).symlink_to(mock)
    env = {
        **os.environ,
        "HOME": str(home),
        "XDG_BIN_HOME": "",
        "TMPDIR": str(temporary),
        "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
        "TEST_OS": "Linux",
        "TEST_ARCH": "x86_64",
        "TEST_RELEASES": str(fixtures),
        "TEST_REQUESTS": str(tmp_path / "requests"),
    }

    def run(*args, uninstall=False, shell="/bin/sh"):
        script = ROOT / "scripts" / ("uninstall.sh" if uninstall else "install.sh")
        return subprocess.run(
            [shell, str(script), *args],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )

    return SimpleNamespace(
        run=run,
        env=env,
        home=home,
        temporary=temporary,
        fixtures=fixtures,
        binary=home / ".local/bin/obelisk",
        requests=tmp_path / "requests",
    )


@pytest.mark.parametrize(
    ("system", "machine", "target"),
    [
        ("Darwin", "arm64", "darwin-arm64"),
        ("Darwin", "x86_64", "darwin-amd64"),
        ("Linux", "x86_64", "linux-amd64"),
    ],
)
def test_supported_platform_installation_never_executes_binary(
    installer, system, machine, target
):
    installer.env.update(
        TEST_OS=system,
        TEST_ARCH=machine,
        TAR_OPTIONS="--to-command=exit 99",
        GZIP="--invalid",
    )
    result = installer.run()
    assert result.returncode == 0, result.stderr
    assert f"{STABLE}-{target}.tar.gz" in installer.requests.read_text()
    assert stat.S_IMODE(installer.binary.stat().st_mode) == 0o755
    assert not (installer.home / "executed").exists()
    assert not list(installer.temporary.iterdir())
    assert "PATH notice" in result.stderr
    assert not list(installer.home.glob(".*rc"))


@pytest.mark.parametrize(
    "args", [("--prerelease",), ("--version", PRE), ("--version", PRE[1:])]
)
def test_explicit_prerelease_and_version_selection(installer, args):
    result = installer.run(*args)
    assert result.returncode == 0, result.stderr
    assert PRE in result.stdout


@pytest.mark.parametrize(
    "tag", ["v0.2.0-alpha.3", "v0.2.0-alpha.4", "v0.3.0-alpha.1", "v0.3.0-alpha.2"]
)
def test_prerelease_archive_selection(installer, tag):
    directory = installer.fixtures / tag
    directory.mkdir()
    for target in TARGETS:
        make_archive(directory / f"obelisk-{tag}-{target}.tar.gz")
    checksums(directory)
    result = installer.run("--version", tag)
    assert result.returncode == 0, result.stderr
    assert tag in result.stdout
    assert (
        f"/releases/download/{tag}/obelisk-{tag}-linux-amd64.tar.gz"
        in installer.requests.read_text()
    )
    assert not (installer.home / "executed").exists()


def test_no_stable_release_has_actionable_error(installer):
    installer.env["TEST_NO_STABLE"] = "1"
    result = installer.run()
    assert result.returncode != 0 and "--prerelease" in result.stderr
    assert not installer.binary.exists()
    assert not list(installer.temporary.iterdir())


@pytest.mark.parametrize(
    "system,arch", [("Linux", "aarch64"), ("Windows", "x86_64"), ("Darwin", "i386")]
)
def test_unsupported_platform_is_rejected(installer, system, arch):
    installer.env.update(TEST_OS=system, TEST_ARCH=arch)
    result = installer.run()
    assert result.returncode != 0 and "Unsupported platform" in result.stderr
    assert not installer.requests.exists()


@pytest.mark.parametrize(
    "args",
    [
        ("--unknown",),
        ("--version",),
        ("--bin-dir",),
        ("--version", "../../bad"),
        ("--version", PRE, "--prerelease"),
    ],
)
def test_invalid_options_fail_closed(installer, args):
    result = installer.run(*args)
    assert result.returncode != 0
    assert not installer.requests.exists()


def test_corrupt_download_is_rejected_before_extraction(installer):
    directory = installer.fixtures / STABLE
    archive = directory / f"obelisk-{STABLE}-linux-amd64.tar.gz"
    archive.write_bytes(b"corrupt")
    result = installer.run()
    assert result.returncode != 0 and "checksum mismatch" in result.stderr
    assert not installer.binary.exists()
    assert not list(installer.temporary.iterdir())


@pytest.mark.parametrize(
    "failure", ["duplicate", "missing", "invalid-hash", "extra-path"]
)
def test_bad_checksum_manifest_fails_closed(installer, failure):
    path = installer.fixtures / STABLE / "SHA256SUMS"
    lines = path.read_text().splitlines(keepends=True)
    if failure == "duplicate":
        lines.append(lines[0])
    elif failure == "missing":
        lines.pop()
    elif failure == "invalid-hash":
        lines[0] = "z" * 64 + lines[0][64:]
    else:
        lines[0] = lines[0].replace("obelisk-", "../obelisk-")
    path.write_text("".join(lines))
    result = installer.run()
    assert result.returncode != 0 and "Invalid SHA256SUMS" in result.stderr
    assert not installer.binary.exists()
    assert not list(installer.temporary.iterdir())


@pytest.mark.parametrize(
    "extra",
    ["../escape", "/tmp/escape", "folder/obelisk", "obelisk", "unexpected", "a\nb"],
)
def test_unsafe_archive_paths_and_duplicates_are_rejected(installer, extra):
    directory = installer.fixtures / STABLE
    make_archive(directory / f"obelisk-{STABLE}-linux-amd64.tar.gz", extra=extra)
    checksums(directory)
    result = installer.run()
    assert result.returncode != 0 and "Unsafe archive" in result.stderr
    assert not installer.binary.exists()


@pytest.mark.parametrize(
    "kind",
    [
        tarfile.SYMTYPE,
        tarfile.LNKTYPE,
        tarfile.FIFOTYPE,
        tarfile.CHRTYPE,
        tarfile.DIRTYPE,
    ],
)
def test_archive_links_and_special_files_are_rejected(installer, kind):
    directory = installer.fixtures / STABLE
    make_archive(directory / f"obelisk-{STABLE}-linux-amd64.tar.gz", kind=kind)
    checksums(directory)
    result = installer.run()
    assert result.returncode != 0
    assert not installer.binary.exists()


@pytest.mark.parametrize("omit", ["obelisk", "LICENSE", "build-info.json"])
def test_incomplete_archive_is_rejected(installer, omit):
    directory = installer.fixtures / STABLE
    make_archive(directory / f"obelisk-{STABLE}-linux-amd64.tar.gz", omit=omit)
    checksums(directory)
    assert installer.run().returncode != 0
    assert not installer.binary.exists()


def test_checksum_valid_but_invalid_compression_is_rejected(installer):
    directory = installer.fixtures / STABLE
    (directory / f"obelisk-{STABLE}-linux-amd64.tar.gz").write_bytes(b"not gzip")
    checksums(directory)
    result = installer.run()
    assert result.returncode != 0 and "Corrupt archive" in result.stderr
    assert not installer.binary.exists()


def test_decompression_is_bounded_and_private_files_are_cleaned(installer):
    directory = installer.fixtures / STABLE
    path = directory / f"obelisk-{STABLE}-linux-amd64.tar.gz"
    with gzip.open(path, "wb") as archive:
        for _ in range(129):
            archive.write(b"\0" * 1024 * 1024)
    checksums(directory)
    result = installer.run()
    assert result.returncode != 0 and "limit" in result.stderr
    assert not installer.binary.exists()
    assert not list(installer.temporary.iterdir())


@pytest.mark.parametrize("tool", ["shasum", "sha256sum", None])
def test_checksum_tools_and_missing_tool_error(installer, tmp_path, tool):
    if tool and not shutil.which(tool):
        pytest.skip(f"{tool} is not available on this machine")
    isolated = tmp_path / "isolated tools"
    isolated.mkdir()
    for name in (
        "curl",
        "uname",
        "mktemp",
        "gzip",
        "tar",
        "awk",
        "sed",
        "grep",
        "wc",
        "mkdir",
        "rmdir",
        "rm",
        "cp",
        "mv",
        "chmod",
        "cat",
        "pwd",
    ):
        location = shutil.which(name, path=installer.env["PATH"])
        if location:
            (isolated / name).symlink_to(location)
    if tool:
        (isolated / tool).symlink_to(shutil.which(tool))
    installer.env["PATH"] = str(isolated)
    result = installer.run()
    if tool:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0 and "sha256sum or shasum" in result.stderr
        assert not installer.binary.exists()


def test_custom_directory_spaces_upgrade_and_uninstall(installer, tmp_path):
    destination = tmp_path / "custom bin"
    installer.env["XDG_BIN_HOME"] = str(destination)
    installer.env["PATH"] += os.pathsep + str(destination)
    first = installer.run()
    assert first.returncode == 0, first.stderr
    assert (destination / ".obelisk-managed.sha256").read_text().splitlines()[0] == (
        "obelisk-music-installer-v1"
    )
    assert "PATH notice" not in first.stderr
    directory = installer.fixtures / PRE
    new = make_archive(
        directory / f"obelisk-{PRE}-linux-amd64.tar.gz", content=b"new version bytes"
    )
    checksums(directory)
    result = installer.run("--version", PRE, "--bin-dir", str(destination))
    assert result.returncode == 0, result.stderr
    assert (destination / "obelisk").read_bytes() == new
    unrelated = destination / "keep-me"
    unrelated.write_text("unrelated")
    result = installer.run("--bin-dir", str(destination), uninstall=True)
    assert result.returncode == 0, result.stderr
    assert list(destination.iterdir()) == [unrelated]


@pytest.mark.parametrize("kind", ["unmanaged", "changed", "symlink", "receipt-symlink"])
def test_unrelated_or_modified_files_are_never_replaced_or_removed(
    installer, tmp_path, kind
):
    installer.binary.parent.mkdir(parents=True)
    if kind == "unmanaged":
        installer.binary.write_text("unrelated")
    else:
        assert installer.run().returncode == 0
        if kind == "changed":
            installer.binary.write_text("modified")
        else:
            path = (
                installer.binary
                if kind == "symlink"
                else installer.binary.parent / ".obelisk-managed.sha256"
            )
            outside = tmp_path / "outside"
            outside.write_bytes(path.read_bytes())
            path.unlink()
            path.symlink_to(outside)
    before = installer.binary.read_bytes()
    assert installer.run().returncode != 0
    assert installer.run(uninstall=True).returncode != 0
    assert installer.binary.read_bytes() == before


def test_changes_during_download_are_not_overwritten(installer):
    assert installer.run().returncode == 0
    installer.env["TEST_CHANGE_BINARY"] = str(installer.binary)
    result = installer.run("--prerelease")
    assert result.returncode != 0
    assert installer.binary.read_text() == "changed during download"


def test_download_failure_preserves_previous_installation_and_cleans_up(installer):
    assert installer.run().returncode == 0
    before = installer.binary.read_bytes()
    installer.env["TEST_FAIL_DOWNLOAD"] = "1"
    assert installer.run("--prerelease").returncode != 0
    assert installer.binary.read_bytes() == before
    assert not list(installer.temporary.iterdir())
    assert not list(installer.binary.parent.glob(".obelisk-stage.*"))
    assert not (installer.binary.parent / ".obelisk-install.lock").exists()


def test_existing_lock_is_not_removed(installer):
    installer.binary.parent.mkdir(parents=True)
    lock = installer.binary.parent / ".obelisk-install.lock"
    lock.mkdir()
    assert installer.run().returncode != 0
    assert installer.run(uninstall=True).returncode != 0
    assert lock.is_dir()


def test_empty_uninstall(installer):
    assert installer.run(uninstall=True).returncode == 0


@pytest.mark.parametrize("option", ["--version", "--bin-dir"])
def test_empty_arguments_never_fall_back_to_defaults(installer, option):
    assert installer.run().returncode == 0
    before = installer.binary.read_bytes()
    assert installer.run(option, "").returncode != 0
    if option == "--bin-dir":
        assert installer.run(option, "", uninstall=True).returncode != 0
    assert installer.binary.read_bytes() == before


@pytest.mark.parametrize("directory", ["/", "/usr", "/etc", "/usr/local/bin"])
def test_system_directory_protection(installer, directory):
    result = installer.run("--bin-dir", directory)
    assert result.returncode != 0 and "user-owned directory" in result.stderr
    assert not installer.requests.exists()


def test_dash_install_upgrade_uninstall(installer):
    dash = shutil.which("dash")
    if not dash:
        pytest.skip("dash is not installed")
    assert installer.run(shell=dash).returncode == 0
    assert installer.run("--prerelease", shell=dash).returncode == 0
    assert installer.run(uninstall=True, shell=dash).returncode == 0
    assert not installer.binary.exists()


def test_shellcheck_when_available():
    shellcheck = shutil.which("shellcheck")
    if not shellcheck:
        pytest.skip("ShellCheck is not installed")
    subprocess.run(
        [
            shellcheck,
            "--shell=sh",
            str(ROOT / "scripts/install.sh"),
            str(ROOT / "scripts/uninstall.sh"),
        ],
        check=True,
        timeout=30,
    )

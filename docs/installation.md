# Installation

Standalone executables bundle Python and runtime dependencies. The current
prerelease is [v0.3.0-alpha.2](https://github.com/tema-juck/obelisk-player/releases/tag/v0.3.0-alpha.2).

Choose the target matching your computer:

| Computer | Target | Native build runner |
| --- | --- | --- |
| macOS Apple Silicon | `darwin-arm64` | `macos-15` ARM64 |
| macOS Intel | `darwin-amd64` | `macos-15-intel` x86_64 |
| Linux Intel/AMD 64-bit | `linux-amd64` | `ubuntu-22.04` x86_64 |

Linux builds target glibc systems (Ubuntu 22.04 or newer is the build baseline),
not Alpine/musl. Older macOS versions are not verified. Linux ARM64 and Homebrew
installation are not provided.

## Shell installer

Download the installer as a file, inspect it, then run it explicitly. Do not pipe
remote scripts into your shell. It needs POSIX `sh`, `curl`, `tar`, `gzip`, common
Unix utilities, and either `sha256sum` (usually Linux) or `shasum` (macOS).

```bash
curl --fail --location --proto '=https' --proto-redir '=https' --output install.sh https://raw.githubusercontent.com/tema-juck/obelisk-player/main/scripts/install.sh
less install.sh
sh install.sh --prerelease
```

The default, `sh install.sh`, selects GitHub's latest **stable** release. There is
no stable release yet, so use `--prerelease` to select the newest published release
including prereleases, or choose an explicit version:

```bash
sh install.sh --version v0.3.0-alpha.2
sh install.sh --prerelease --bin-dir "$HOME/bin"
```

The default destination is `${XDG_BIN_HOME:-$HOME/.local/bin}`. The installer never
uses sudo, edits shell profiles, runs Obelisk, or changes macOS security settings.
It reports when the directory is absent from PATH. For the default location,
explicitly add the following to your shell configuration if needed:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

The script verifies the selected archive against `SHA256SUMS` before bounded
decompression, accepts only the expected three regular archive members, and reads
only `obelisk` to a file without filesystem extraction. The compressed and expanded
archive limit is 128 MiB. Checksums detect corruption; they do not independently
prove publisher identity.

## Upgrade and uninstall

Re-run the reviewed installer with the desired channel or `--version` to upgrade.
The hidden `.obelisk-managed.sha256` receipt records the installed executable's
hash. Only an unchanged managed executable may be replaced. Existing manual
installations, unrelated binaries, symlinks, and modified files are refused: inspect
and move them aside yourself before switching to this installer. There is no force
overwrite option. Keep the receipt alongside the executable.

Download and inspect the uninstaller before running it, using the same directory
as installation:

```bash
curl --fail --location --proto '=https' --proto-redir '=https' --output uninstall.sh https://raw.githubusercontent.com/tema-juck/obelisk-player/main/scripts/uninstall.sh
less uninstall.sh
sh uninstall.sh
# Custom installation: sh uninstall.sh --bin-dir "$HOME/bin"
```

It removes only the unchanged managed executable and its receipt. Other files,
configuration, and Spotify keychain credentials remain. To remove login credentials,
remove the `obelisk-music` entry from your OS keychain explicitly.

Concurrent install/uninstall operations share a directory lock. After a forced
interruption, inspect the installation before retrying: a stale lock or mismatched
receipt deliberately blocks changes. Once no installer is running, an empty stale
`.obelisk-install.lock` can be removed manually. Never bypass a checksum failure.

## Verify release checksums manually

Download from [GitHub Releases](https://github.com/tema-juck/obelisk-player/releases).
For example, for Apple Silicon, run the following in an empty download directory;
replace `darwin-arm64` with `darwin-amd64` for Intel macOS or `linux-amd64` for Linux:

```bash
release=v0.3.0-alpha.2
target=darwin-arm64
asset="obelisk-${release}-${target}.tar.gz"
base="https://github.com/tema-juck/obelisk-player/releases/download/${release}"
curl --fail --location --output "$asset" "$base/$asset"
curl --fail --location --output SHA256SUMS "$base/SHA256SUMS"
awk -v asset="$asset" '$2 == asset {print}' SHA256SUMS > "$asset.sha256"
test -s "$asset.sha256"
```

Verify **before extracting or installing**. The checksum file contains all three
archives, so the commands above select only the one you downloaded:

```bash
# macOS: this must print OK and exit successfully
shasum -a 256 -c "$asset.sha256"

# Linux: use this instead
sha256sum -c "$asset.sha256"
```

These commands are an independent checksum check; the installer performs its own
verification before installing and does not execute the downloaded application.

These initial macOS binaries use ad-hoc signing, not Developer ID signing or Apple
notarization. If macOS blocks a verified download, follow Apple's
[Open Anyway procedure in Privacy & Security](https://support.apple.com/guide/mac-help/open-a-mac-app-from-an-unknown-developer-mh40616/mac).
Do not disable Gatekeeper or remove security protections. Checksums detect file
corruption; they are not a publisher identity signature.

## Configure Spotify and start

Create your own Spotify Developer app, register
`http://127.0.0.1:8765/callback` exactly, and allowlist your Spotify account. Spotify
Premium and a Spotify Connect playback device are required.

Releases since v0.2.0-alpha.4 support TOML configuration:

```bash
obelisk config init
obelisk config edit
obelisk config validate
obelisk
```

Set `spotify.client_id` in `~/.config/obelisk/config.toml`, or under
`$XDG_CONFIG_HOME/obelisk/config.toml` when set. Use `obelisk config path` to print
the effective location. The file also supports playback polling interval, the
`midnight` palette, and enabling/disabling Vim navigation. Releases since
v0.3.0-alpha.1 also support the `mono` palette. See
[Configuration](configuration.md) for the schema, validation, permissions,
editor selection, and environment overrides.

Published `v0.2.0-alpha.1` binaries predate these commands. Those binaries, and
users who prefer environment setup, can continue using:

```bash
export SPOTIFY_CLIENT_ID="your_client_id_here"
obelisk
```

Press **L** to authorize. A client ID is public application identification; no
client secret is needed. Never place access or refresh tokens in shell setup,
the repository, or release archives. `.env` files are not loaded automatically.

## Troubleshooting

- **Command not found:** check `~/.local/bin` is on PATH, or run that executable by its full path.
- **Wrong architecture / bad CPU type:** use the archive matching your hardware; builds are native, not universal or cross-compiled.
- **Login fails:** verify the client ID, exact callback URL, account allowlist, and that port 8765 is available. Obelisk reports locked/unavailable keychains instead of storing tokens in plaintext.
- **Configuration error:** run `obelisk config path`, repair with `config edit`, then `config validate`. Unset an invalid environment override to use the file. Config creation refuses symlinks and never overwrites files.
- **Linux keychain unavailable:** use a desktop session with a running Secret Service keyring and D-Bus session. Headless servers may not provide a secure credential backend.
- **No playback device:** open Spotify, start playback, then press **D** to select a controllable device. Restricted or disappeared devices cannot accept commands.
- **One-file startup fails:** PyInstaller needs a writable temporary directory that permits loading executable code. The smoke test also requires a real PTY and OS IPC support; restrictive sandboxes can block these.


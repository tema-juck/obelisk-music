# ◆ Obelisk Music

**A keyboard-first, open-source Spotify Connect terminal controller.**

Python · Textual · Spotify Web API · macOS / Linux

> **Status:** experimental MVP. Obelisk does not decrypt or stream Spotify audio. Spotify Desktop or another Spotify Connect device handles audio playback.

## Install a standalone pre-release

The first planned pre-release is **v0.2.0-alpha.1**. These downloads become
available only after its release workflow succeeds. Standalone executables include
Python and runtime dependencies; installing Python or uv is not required.

Choose the target matching your computer:

| Computer | Target | Native build runner |
| --- | --- | --- |
| macOS Apple Silicon | `darwin-arm64` | `macos-15` ARM64 |
| macOS Intel | `darwin-amd64` | `macos-15-intel` x86_64 |
| Linux Intel/AMD 64-bit | `linux-amd64` | `ubuntu-22.04` x86_64 |

Linux builds target glibc systems (Ubuntu 22.04 or newer is the build baseline),
not Alpine/musl. Older macOS versions are not verified. Linux ARM64 and Homebrew
installation are not provided.

Download from [GitHub Releases](https://github.com/tema-juck/obelisk-music/releases).
For example, for Apple Silicon, run the following in an empty download directory;
replace `darwin-arm64` with `darwin-amd64` for Intel macOS or `linux-amd64` for Linux:

```bash
release=v0.2.0-alpha.1
target=darwin-arm64
asset="obelisk-${release}-${target}.tar.gz"
base="https://github.com/tema-juck/obelisk-music/releases/download/${release}"
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

After verification succeeds:

```bash
tar -xzf "$asset" obelisk
mkdir -p "$HOME/.local/bin"
install -m 755 obelisk "$HOME/.local/bin/obelisk"
export PATH="$HOME/.local/bin:$PATH"
obelisk --help
obelisk --version
```

Add `$HOME/.local/bin` to your shell's PATH permanently if necessary. To update,
quit Obelisk, download the new version, verify its checksum, and repeat the install
step. To uninstall, remove `~/.local/bin/obelisk`. Refresh tokens stay in your OS
keychain; remove the `obelisk-music` entry there if you also want to remove login
credentials.

These initial macOS binaries use ad-hoc signing, not Developer ID signing or Apple
notarization. If macOS blocks a verified download, follow Apple's
[Open Anyway procedure in Privacy & Security](https://support.apple.com/guide/mac-help/open-a-mac-app-from-an-unknown-developer-mh40616/mac).
Do not disable Gatekeeper or remove security protections. Checksums detect file
corruption; they are not a publisher identity signature.

### Configure Spotify and start

Create your own Spotify Developer app, register
`http://127.0.0.1:8765/callback` exactly, and allowlist your Spotify account. Spotify
Premium and a Spotify Connect playback device are required.

```bash
export SPOTIFY_CLIENT_ID="your_client_id_here"
obelisk
```

Press **L** to authorize. A client ID is public application identification; no
client secret is needed. Never place access or refresh tokens in shell setup,
the repository, or release archives. `.env` files are not loaded automatically.

### Troubleshooting

- **Command not found:** check `~/.local/bin` is on PATH, or run that executable by its full path.
- **Wrong architecture / bad CPU type:** use the archive matching your hardware; builds are native, not universal or cross-compiled.
- **Login fails:** verify the client ID, exact callback URL, account allowlist, and that port 8765 is available. Obelisk reports locked/unavailable keychains instead of storing tokens in plaintext.
- **Linux keychain unavailable:** use a desktop session with a running Secret Service keyring and D-Bus session. Headless servers may not provide a secure credential backend.
- **No playback device:** open Spotify, start playback, then press **D** to select a controllable device. Restricted or disappeared devices cannot accept commands.
- **One-file startup fails:** PyInstaller needs a writable temporary directory that permits loading executable code. The smoke test also requires a real PTY and OS IPC support; restrictive sandboxes can block these.

## Features

- Spotify authorization using OAuth 2.0 PKCE (no client secret)
- Refresh token stored in your OS keychain via `keyring`
- Current song, artist and playback device display
- Play/pause, previous and next keyboard bindings
- Periodic playback state refresh
- Automatic token refresh and bounded recovery after an expired-token response
- Responsive layouts, including compact terminals down to 40×12
- Paginated playlist and track browsing with session-local page caching
- Playlist owners and available item counts; unavailable entries keep their positions
- Enter-to-play using playlist context and the original item offset
- Keyboard-driven Spotify Connect device selection and playback transfer

## Getting started

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), and Spotify Premium.
This section is for running from source. Standalone installation is described above.

1. Create your **own** Spotify Developer application with **Web API** selected.
2. Set the redirect URI exactly to `http://127.0.0.1:8765/callback`.
3. Make sure your Spotify user is permitted to use the app in Development Mode.
4. Open the official Spotify app on your Mac and start playing a track to establish an active device.
5. Run:

```bash
uv sync --dev
export SPOTIFY_CLIENT_ID="your_client_id_here"
uv run obelisk
```

Press **L** to open browser authorization. Complete the consent form and return to the terminal.
After upgrading from the playback-only version, press **L** once to grant
`playlist-read-private` and `playlist-read-collaborative`. Token refresh cannot add
permissions to an existing grant.

### Keyboard shortcuts

| Key | Action |
| --- | --- |
| J / ↓ | Next item |
| K / ↑ | Previous item |
| Enter | Open playlist / play selected track |
| Tab / Shift+Tab | Switch browsing pane |
| Escape | Return to playlists / close overlay |
| L | Authorize Spotify |
| Space | Play/pause |
| N | Next |
| P | Previous |
| D | Spotify Connect device selector |
| R | Refresh playback and the current browsing view |
| ? | Keyboard shortcuts |
| Q | Quit |

Enter on **Load more…** fetches the next page. Opening a cached playlist makes no
additional library requests. **R** reloads the focused view from its first page;
refreshing the playlist list also clears cached track pages. A new login clears
the library cache and selected target device.

Normal terminals show both browsing panes. At narrow widths, **Tab** switches the
single visible pane and **Escape** returns to playlists. The playback strip stays
visible. Row highlighting and **▸** mark selection; **♫** marks the currently
playing Spotify track URI. Merely navigating never starts playback.

In the device selector, **Enter** transfers to the selected controllable device,
**R** reloads available devices, and **Escape** or **Q** closes the selector. The
active device has **●**; a previously selected target is labeled separately.
Transfers preserve playback state. The target changes only after a successful
transfer, and failed commands never silently switch to another device. Dialogs
and focused text inputs do not trigger application playback shortcuts.

Search is reserved for P2; **/** has no search action in P1.

### Testing

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

### CI checks locally

GitHub Actions runs on pull requests targeting `main` and pushes to `main`, with
Ubuntu and macOS jobs for Python 3.13 and 3.14. Actions are pinned to commit SHAs;
the workflow pins uv to 0.13.7 and uses read-only repository permissions.

Run the equivalent checks from the repository root:

```bash
export UV_FROZEN=true
uv sync --frozen --dev
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv build
uv run python scripts/smoke_wheel.py dist/*.whl
```

Set `UV_PYTHON=3.13` or `UV_PYTHON=3.14` before these commands to select a matrix
version. uv must be on `PATH`; CI uses 0.13.7. If `dist` contains older wheels,
pass the freshly built wheel's exact path instead of the wildcard.

The smoke check verifies the stylesheet and runtime modules inside the wheel,
creates a temporary virtual environment outside the checkout, installs hashed
runtime dependencies exported from `uv.lock`, and installs the wheel without
development dependencies. It then checks dependency consistency, the `obelisk`
entry point, and headless startup/quit from the installed package in Python
isolated mode. The temporary environment is removed afterward.

Tests use mocked Spotify responses and block network, browser, and keychain
access. No Spotify credentials are needed. Dependency installation and building
can download packages from PyPI; the tests and installed-app probe do not contact
Spotify. Local checks do not establish that GitHub-hosted jobs have passed;
those results are available only after the workflow runs on GitHub.

## Release procedure (maintainers)

`pyproject.toml` is the application version authority. Runtime `__version__`, the
TUI footer, and `obelisk --version` read installed package metadata. PEP 440
`0.2.0a1` corresponds to tag `v0.2.0-alpha.1`; beta and rc tags similarly map to
`bN` and `rcN`. Mismatched, malformed, dev, or local-version tags are rejected.

Release tools are frozen in `uv.lock`, including PyInstaller 6.22.3 and Hatchling
1.33.0. The release workflow pins uv 0.13.7 and Python 3.13.18. On each target OS,
the local equivalent is:

```bash
export UV_PYTHON=3.13.18
uv sync --frozen --dev --group release
uv run python scripts/release.py verify-tag --tag v0.2.0-alpha.1
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv build
uv run python scripts/smoke_wheel.py dist/*.whl
# Choose the native target from the platform table; other targets are refused.
uv run python scripts/build_binary.py --tag v0.2.0-alpha.1 --target darwin-arm64
```

The binary smoke copies `obelisk` outside the checkout, clears Spotify/Python
configuration variables, checks help/version, waits for the real styled TUI to
render in an 80×24 PTY, sends **Q**, and requires successful exit. Help/version
have 30-second timeouts; startup/quit has 45 seconds; the build has 15 minutes.
No Spotify credentials are required. Archives contain only `obelisk`, `LICENSE`,
and version/platform/commit/checksum build metadata. Tar ordering, ownership,
timestamps, and gzip headers are normalized. Bit-for-bit binary reproducibility
across changing runner images, SDKs, or ad-hoc signatures is not guaranteed.

After review, merge this infrastructure into `main` and require green CI. **Only
after explicit release approval**, on the reviewed commit:

```bash
git switch main
git pull --ff-only
uv run python scripts/release.py verify-tag --tag v0.2.0-alpha.1
git tag -a v0.2.0-alpha.1 -m "Obelisk Music v0.2.0-alpha.1"
git push origin v0.2.0-alpha.1
```

The tag-only release workflow first runs quality/wheel checks, then all three
native regression/build/smoke jobs with read-only repository permissions. Only
the publishing job receives `contents: write`. It validates the complete archive
set without extracting or executing downloads, checks platform headers, package
version, source commit, pinned tool versions, and binary checksums, then generates
`SHA256SUMS`. It verifies the remote tag still points to the build commit.

Publishing first creates a **draft**, uploads all three archives and checksums,
verifies GitHub asset names/sizes/digests, rechecks the tag, and only then makes the
release public. Alpha/beta/rc releases are marked prerelease and not Latest. A
failed build produces no release; a failed upload or verification leaves only an
unpublished draft. Inspect and remove that failed draft in GitHub before retrying,
keeping the Git tag. Existing releases are never overwritten. Do not move a
published tag; fix failures on a new version instead.

This repository does not publish to PyPI, create Homebrew taps, or build Linux
ARM64 in this workflow. Platform verification claims require an actual native
smoke result; local Apple Silicon checks do not verify Intel macOS or Linux.

## Limitations

- Audio plays in a separate Spotify Connect device (for example Spotify Desktop), not through Obelisk itself.
- An active Spotify device is necessary for basic playback controls.
- The Spotify Web API enforces rate and development quotas. Avoid aggressive polling.
- Expired access tokens refresh automatically. Revoked or expired authorization still requires login; press **R** to retry stored credentials or **L** to authorize again.
- A Spotify 429 response starts a shared request cooldown using `Retry-After` (30 seconds if absent or invalid). Playback commands are not automatically replayed after rate limits or uncertain network failures.
- OS keychain operations and browser launching run off the UI thread. A pending credential write must finish before shutdown to preserve token rotation; an OS keychain prompt may delay process exit.
- Spotify can change API behavior and restrict endpoints for Development Mode applications.
- Spotify exposes playlist items only for playlists the user owns or collaborates on. Followed playlists can appear in the list while their contents remain inaccessible; Obelisk shows an unavailable message without forcing login. See [Get Playlist Items](https://developer.spotify.com/documentation/web-api/reference/get-playlists-items).
- Null, local, explicitly unplayable, and unsupported items remain visible but cannot be started from Obelisk. Duplicate tracks remain separate occurrences. Spotify playback state identifies a track URI rather than a duplicate occurrence, so matching duplicates share the **♫** indicator.
- Library pages are cached until refresh or login. External playlist edits can move item offsets; press **R** to reload after edits. Rejected context playback is reported without switching to another playback mode. Device availability and control depend on Spotify Connect; some device models do not appear in the API.

## Architecture

`Textual workers -> SpotifyClient + SpotifySession -> Spotify Web API -> Spotify Connect device`

`auth.py` handles PKCE callbacks and keychain persistence; `session.py` coordinates
token expiry and refresh; `spotify.py` reuses one HTTPX client and applies request
cooldowns. `models.py` validates consumed Spotify data; `library.py` owns page
caches and offset progression. `browser.py` renders keyboard browsing tables,
`widgets.py` renders plain-text playback information, and `screens.py` provides
device/help overlays. Widgets emit messages and do not make API requests.
`app.py` coordinates managed workers, view revisions, playback commands, and
session lifecycle. Playback mutations are serialized and do not get cancelled
when browsing selections change.

## Open source

Contributions welcome. MIT licensed. This is an independent project, not affiliated with Spotify.

## Minimal TUI design (v0.2)

The interface is borderless, keyboard-first, and intentionally avoids buttons and heavy panels.
Playlists and tracks use compact tables above a persistent playback strip, with
a single browsing pane in narrow terminals. Help is an overlay rather than a
permanent shortcut sidebar. No audio is decoded in this app.

The layout is implemented in `src/obelisk_music/app.py` and styled in
`src/obelisk_music/obelisk.tcss`. To try it:

```shell
uv sync --dev
uv run obelisk
```

Keyboard: `j/k` navigate, `enter` open/play, `tab` switch pane, `d` devices,
`?` help, `space` play/pause, `n/p` skip, `l` login, `r` refresh, `q` quit.

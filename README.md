# ◆ Obelisk Music

**A keyboard-first, open-source Spotify Connect terminal controller.**

Python · Textual · Spotify Web API · macOS / Linux

> **Status:** experimental MVP. Obelisk does not decrypt or stream Spotify audio. Spotify Desktop or another Spotify Connect device handles audio playback.

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

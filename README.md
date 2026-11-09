# ◆ Obelisk Music

**A keyboard-first, open-source Spotify Connect terminal controller.**

Python · Textual · Spotify Web API · macOS / Linux

> **Status:** experimental MVP. Obelisk does not decrypt or stream Spotify audio. Spotify Desktop or another Spotify Connect device handles audio playback.

## Features (MVP)

- Spotify authorization using OAuth 2.0 PKCE (no client secret)
- Refresh token stored in your OS keychain via `keyring`
- Current song, artist and playback device display
- Play/pause, previous and next buttons and keyboard bindings
- Periodic playback state refresh

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

### Keyboard shortcuts

| Key | Action |
| --- | --- |
| L | Authorize Spotify |
| Space | Play/pause |
| N | Next |
| P | Previous |
| R | Refresh |
| Q | Quit |

### Testing

```bash
uv run pytest -q
uv run ruff check .
```

## Limitations

- Audio plays in a separate Spotify Connect device (for example Spotify Desktop), not through Obelisk itself.
- An active Spotify device is necessary for basic playback controls.
- The Spotify Web API enforces rate and development quotas. Avoid aggressive polling.
- This MVP handles 401 errors by showing a login message; automatic token refresh on a mid-session 401 is a planned improvement.
- Spotify can change API behavior and restrict endpoints for Development Mode applications.

## Architecture

`Textual TUI -> SpotifyClient (HTTPX) -> Spotify Web API -> Spotify Connect device`

## Open source

Contributions welcome. MIT licensed. This is an independent project, not affiliated with Spotify.

## Minimal TUI design (v0.2)

The interface is borderless, keyboard-first, and intentionally avoids buttons and heavy panels.
The sidebar shows the current view and keyboard shortcuts; the main area displays live
Spotify Connect playback metadata and a progress indicator. No audio is decoded in this app.

The layout is implemented in `src/obelisk_music/app.py` and styled in
`src/obelisk_music/obelisk.tcss`. To try it:

```shell
uv sync --dev
uv run obelisk
```

Keyboard: `l` login, `space` play/pause, `n` next, `p` previous, `r` refresh, `q` quit.
The playlist and search views are roadmap items, not yet implemented.

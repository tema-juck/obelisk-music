# Changelog

## v0.3.0-alpha.1 — Unreleased

Package version: `0.3.0a1`. This candidate contains the P2 music experience merged
in `37d3de6`. The latest published baseline remains `v0.2.0-alpha.4`.

### Added

- Global `/` search for tracks, albums, and artists, with 300 ms debounce,
  on-demand pagination, and session-local caching.
- Album metadata and paginated tracks; artist metadata and browsable albums.
- Enter-to-play for search tracks and album context with original item offsets.
- Local Now Playing progress interpolation with safe pause/resume and duration
  clamping, without increasing Spotify polling.
- A monochrome `mono` theme alongside the existing `midnight` theme.
- Catalog shortcuts, contextual hints, Spotify links, and focus restoration.

### Improved

- Responsive browsing and overlays at 120×35, 80×24, 60×20, and 40×12.
- Superseded read cancellation and protection against late UI/cache responses.
- Graceful worker shutdown, asynchronous catalog Quit, and search/login coordination.
- Explicit empty, unavailable, restricted, rate-limited, and failed-request states.
- Offline regression coverage and installed-wheel smoke checks for catalog views
  and both supported theme settings.

### Compatibility

- Existing TOML configuration, `SPOTIFY_CLIENT_ID`, PKCE, keychain credentials,
  Spotify Connect playback, and the CLI remain supported.
- Theme changes require a restart. Account/market restrictions still apply.
- No direct audio streaming, downloading, lyrics, or queue editing is added.

See [release notes](docs/releases/v0.3.0-alpha.1.md) for artifacts, verification,
known limitations, and the remaining publication gates.

## v0.2.0-alpha.4 — Published baseline

Reliable Spotify sessions, paginated playlists/tracks, Spotify Connect devices,
keyboard navigation, standalone macOS/Linux executables, the shell installer,
and XDG TOML configuration. This existing release and all older tags are preserved.

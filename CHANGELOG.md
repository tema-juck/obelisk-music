# Changelog

## v0.3.0-alpha.2 — Unreleased

Package version: `0.3.0a2`. Contains F1 playback hotfix `a189d5b`, merged into
main at `589c06e`. The latest published baseline remains `v0.3.0-alpha.1`.

### Fixed

- F1 search playback starts the exact selected URI in its album context, avoiding
  silent no-ops from URI-list playback on affected Spotify clients.
- Search row positions and disc-relative track numbers are not used as album
  offsets. Paginated search results retain the correct selected track.
- Missing album metadata and rejected playback requests provide actionable guidance.

### Preserved

- F2/F3 album playback retains its original numeric item offsets.
- Focus, selection, input shortcuts, device targeting, serialized mutations,
  shared cooldowns, and protection against ambiguous mutation retries remain in use.
- Package installation smoke now exercises F1 playback and Now Playing through
  mocked HTTP. The hotfix adds 19 regression cases.

Spotify may continue to subsequent album tracks after the selected track ends.
See [alpha.2 release notes](docs/releases/v0.3.0-alpha.2.md) for verification,
installation, and the remaining publication gates.

## v0.3.0-alpha.1 — Published

Package version: `0.3.0a1`. Contains the P2 music experience merged in `37d3de6`.
The F1 playback hotfix is included starting with alpha.2.

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

See [alpha.1 release notes](docs/releases/v0.3.0-alpha.1.md) for its artifacts and
limitations. The existing release and tag remain unchanged.

## v0.2.0-alpha.4 — Published

Reliable Spotify sessions, paginated playlists/tracks, Spotify Connect devices,
keyboard navigation, standalone macOS/Linux executables, the shell installer,
and XDG TOML configuration. This existing release and all older tags are preserved.

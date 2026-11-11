# Obelisk Radio

Independent Internet Radio application in the Obelisk monorepo. Development only;
publication is blocked pending policy review. Install mpv separately, then launch
`obelisk-radio`. No Player package, authentication or keyring is used.

Use `/` to search; Tab cycles name/country/language/tag and results. Enter plays;
g shows genres, v favorites, c custom, a adds a direct URL, f toggles favorites.
Space stops/resumes live; +/- adjusts volume; x stops, Escape returns to discovery,
? opens help and q quits. Direct public MP3/AAC only; no HLS/playlists or recording.

Configuration: `${XDG_CONFIG_HOME:-$HOME/.config}/obelisk-radio/config.toml`.
Commands: `obelisk-radio config init`, `edit`, `path`, `validate`.
Settings: `[appearance] theme = "midnight"` (or mono), `[interface] vim_keys = true`.
Radio-only environment overrides: OBELISK_RADIO_THEME, OBELISK_RADIO_VIM_KEYS.
Favorites/custom/selection/volume: `${XDG_DATA_HOME:-$HOME/.local/share}/obelisk-radio/radio.json`.
Corrupt state is retained and saving disabled; repair or move it aside and restart.
No automatic migration of Player configuration, credentials or experimental state.

See [the monorepo guide](../../docs/monorepo.md) for architecture, release namespaces, policy questions,
and verification. Original MIT attribution and radio commit provenance are retained
in LICENSE and PROVENANCE.json. mpv JSON IPC provides state, not PCM samples;
future radio-only analysis requires the documented PCM output adapter and audio sink.

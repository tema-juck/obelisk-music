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
the optional analysis path explicitly connects PCM output to an audio sink.

## Audio visualizer (development)

Press `t` to enable/disable visualization and `z` to cycle Spectrum → Beat Pulse
→ Waveform → Off. Shortcuts do not fire while typing in fields. Enabling it
during ordinary playback restarts at the live edge; switching presets does not.
Off clears the display and stops analysis. If PCM playback is already active,
its audio sink stays alive until Stop; the next playback uses one mpv when off.

Spectrum shows 24 logarithmic frequency bands; Beat Pulse responds to detected
onsets and bass energy; Waveform shows signed real sample amplitudes. These are
audio measurements, not randomized animation or BPM/tempo estimation. No samples
or a stalled clock produces an explicit unavailable state.

Optional configuration (restart after editing; keyboard changes are session-only):

```toml
[visualizer]
enabled = false
mode = "spectrum" # spectrum, beat-pulse, waveform, off
fps = 30 # integer 10–60, maximum analysis/render rate
sensitivity = 1.0 # 0.25–4.0
```

This Radio-only path uses mpv twice: decoder → anonymous PCM pipe → audible mpv
sink, with a bounded memory copy for NumPy analysis. The sink's audio clock
selects the analysis window. It requires an mpv build supporting PCM output,
rawaudio input and a working system audio device on macOS/Linux. Audio is never
recorded. Default off preserves the original single-process engine. Player and
the Radio publication hold are unchanged. See the
[design and resource bounds](../../docs/radio-visualizer-design.md) and
[verification/acceptance results](../../docs/radio-visualizer-verification.md).

Opt-in local acceptance checks play sound and do not save audio:

```sh
# Original generated pulses; external ffmpeg is only needed for this check.
uv run --directory apps/radio python scripts/check_visualizer.py --seconds 12
# Explicitly chosen public Radio stream; no ffmpeg needed.
uv run --directory apps/radio python scripts/check_visualizer.py --seconds 12 --url https://ice1.somafm.com/groovesalad-128-mp3
```


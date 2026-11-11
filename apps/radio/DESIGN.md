# Obelisk Radio design

Radio reuses the established midnight/mono themes and minimalist terminal grid.
Runtime Theme definitions in src/obelisk_radio/app.py own the palette; radio.tcss
uses semantic variables only. NavigationTable owns row selection and j/k. PlainText
renders all station/ICY metadata without markup. Input owns typing. RadioScreen
emits intents; RadioController owns workers and I/O. Help uses native focus restore.

Verify 120×35, 80×24, 60×20 and 40×12 in both themes, including empty and slow
requests. Favorites are the offline startup view. Source switching and Spotify
controls do not exist. Enter in a field applies filters without starting audio;
Space in results stops/resumes at the live edge. Metadata is never invented.

Visualizer owns a non-focusable, borderless strip below playback metadata. It
inherits $accent from the existing runtime theme; no new palette or input
primitive is introduced. Height is four rows normally, three in compact mode,
and two in tiny mode. Tiny layouts omit genre/view/metadata labels to preserve
station rows, playback state and keyboard help. The one-row waveform uses signed
amplitude glyphs. Disabled visualization occupies no rows and pauses its timer.

RadioScreen owns t (toggle) and z (preset), gated while Input has focus like all
other Radio shortcuts. RadioController owns state and restart-at-live-edge when
PCM is first enabled during playback. Switching presets keeps playback running;
off immediately clears the frame and skips DSP. Choices last for this session.
The widget reports Audio unavailable for missing/stale PCM or a stopped sink.
It never fabricates samples, takes focus, or queues old animation frames.

Verification: all three modes, missing samples, off, stop, text-entry shortcuts,
focus retention, resize and both themes at all four required terminal sizes.

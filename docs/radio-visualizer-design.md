# Radio visualizer design (P4)

Design recorded before implementation on `feat/radio-audio-visualizer`.

## Inspection and decision

`radio_audio.py` checks HTTP destinations, content framing and MP3/AAC signatures,
strips ICY metadata, and supervises mpv with cancellation-safe process acquisition
and reaping. Preserve that file, its provenance hash and the normal one-process
playback path. Extend Radio through an optional process adapter, not Player.

The previous FIFO probe is described in `docs/monorepo.md`: 192000 decoded bytes
for one second of 48 kHz stereo s16, but no audible sink. No executable probe was
present in this checkout. mpv JSON IPC provides control/properties, not PCM.
The [mpv manual](https://mpv.io/manual/stable/) documents `ao=pcm` as a file
writer, `demuxer=rawaudio` as a PCM reader, and `audio-pts` as the playback clock
including the audio driver's delay. Selecting PCM output replaces system output.

Recommended pipeline:

```text
checked Radio HTTP bytes → mpv decoder (unity volume, PCM stdout)
                          → bounded pipe fan-out → mpv raw-audio system sink
                                                → bounded PCM ring → DSP worker
sink audio-pts IPC ────────────────────────────────────────────────┘
DSP latest immutable frame → non-focusable Textual Visualizer
```

An initial macOS ARM64/mpv 0.42.0 generated-tone probe confirmed 192000 clean PCM
bytes and successful playback through the default system sink. This proves the
building blocks, not simultaneous streaming, human audibility or synchronization.

## Implementation plan and contracts

1. Add Radio-only DSP and validated configuration, then deterministic signal tests.
2. Add the optional decoder/sink adapter and bounded fan-out, keeping the original
   backend and URL validation unchanged. Test startup failures and cancellation.
3. Add the widget, keyboard intents, help, responsive layouts and UI tests.
4. Exercise generated audio and a real stream, run both product regressions,
   packaging/installer checks, and record measured results and native limitations.

Normalize decoder output to native-endian signed s16, interleaved stereo, 48 kHz;
the sink's raw format explicitly matches host byte order. mpv handles source
resampling/channel conversion. Analyze the mean of stereo channels as float;
RMS/spectrum use real samples only (opposite-phase stereo may cancel in mono).
No files/FIFOs contain audio: only anonymous pipes and a two-second memory ring.
StreamReader limits, writer watermarks and bounded chunk reads bound transport
memory. Audio backpressure comes only from the sink; analysis never delays it.

Use sink `audio-pts` to select a 2048-sample Hann window in the ring, avoiding
decoder-ahead visualizations. No valid clock/window means unavailable. A single
worker computes RMS, normalized rFFT magnitudes, logarithmic bands, positive
spectral flux, adaptive noise/onset thresholds, refractory onset events and
attack/decay envelopes. NumPy provides the tested native FFT and releases Python
work from Textual callbacks. Only the newest frame is rendered at configured FPS;
stale clock/PCM expires rather than replaying. Onsets are not tempo estimates.

Default disabled preserves existing startup/playback behavior. `t` toggles and
`z` cycles spectrum/beat-pulse/waveform/off outside inputs. Enabling during normal
playback restarts at the live edge to acquire PCM. Disabling skips DSP immediately;
an active PCM sink stays until Stop, avoiding a playback interruption. Next playback
uses the original one-process path when disabled. TOML changes require restart;
keyboard choices last for the session. No automatic configuration writes.

The scope reaches configuration, controller construction/intents, screen/widget,
stylesheet/help, Radio lockfile/package checks, Radio tests and documentation.
No Player runtime, dependency, version, release gate or tag changes.

## Risks and acceptance

Two mpv processes cost more CPU/memory than normal playback. Startup errors,
either child's exit, stalled sink writes, repeated cancellation and station
switches must reap both children. DSP must not accumulate jobs; stop waits for
its in-flight calculation. No sample values or untrusted mpv stderr are logged.
Missing PCM/clock is visible and never replaced by synthesized frames.

macOS and Linux support anonymous pipes and Unix IPC. mpv builds must support
PCM output and rawaudio input. Audio-device buffering and terminal refresh add
latency: `audio-pts` is a driver estimate, not a physical acoustic measurement.
The sink uses a modest 100 ms requested buffer; hardware may choose a larger one.
Do not claim exact synchronization/BPM, zero underruns, or human-confirmed audio
without evidence. Native Intel/Linux and human audiovisual acceptance remain
explicit checks when unavailable locally. Radio publication remains blocked.

# Radio visualizer P4 verification

Local verification on 2026-11-11, macOS ARM64, mpv 0.42.0 (FFmpeg 9.0.2).
Development branch: `feat/radio-audio-visualizer`. No versions, tags, Player
runtime/dependencies or publication gates changed. All commits remain local.

## Implemented architecture

```text
validated Radio HTTP → original engine's ICY removal/MP3-AAC detection
  → mpv decoder, unity volume, 48 kHz native s16 stereo PCM stdout
  → anonymous pipe fan-out → mpv rawaudio → system audio device
                           → two-second memory ring
sink audio-pts JSON IPC → sample-indexed window → serial DSP worker
  → latest immutable AudioFrame → Textual Visualizer
```

The sink, not the PCM writer, owns audible output, volume, playback state and
the audio clock. mpv converts incoming sample formats/rates/channel layouts.
Analysis uses a mono average of the two normalized channels. IPC carries only
control and clock data. No named FIFO, audio recording, persistent sample file,
raw-audio log or second network stream is used. The original `radio_audio.py`
and its provenance SHA256 remain unchanged; its stream validation, retry,
process-acquisition shielding and cancellation/reaping are retained.

The analysis ring is capped at 384000 bytes (two seconds). The decoder stdout
reader has an 8192-byte limit; fan-out reads 4096-byte chunks and the sink writer
uses 8192/4096-byte watermarks. OS pipes are bounded. The sink demux cap is
128 KiB, with no cache/readahead and a requested 100 ms device buffer; the driver
may choose a larger buffer. The decoder retains the engine's 2 MiB demux cap.
One serial analysis job and one latest display frame prevent stale work queues.
Only audio-sink backpressure delays delivery; DSP runs in a worker thread.

Sink clock polling is bounded by configured FPS and a 200 ms response deadline.
The window ends at the sink's current audible sample index rather than the
decoder's write head. Missing clock/PCM expires the visible frame within 300 ms.
Stop/resume (at the live edge), station switches and reconnects get fresh sessions;
disabled analysis, stale results and shutdown cannot continue old pulse frames.
Shutdown waits for in-flight DSP, terminates both children and escalates to kill
for stubborn processes. Unit tests include repeated cancellation and forced kill.

## DSP, presets and settings

- RMS: short-window mono root-mean-square amplitude, also used for noise gating.
- Spectrum: 2048-sample Hann window, NumPy real FFT, coherent-gain magnitude
  normalization, low (20–250 Hz), mid (250–4000 Hz), high (4000–20000 Hz) energy
  proxies, and 24 logarithmic display bands (30–16000 Hz). Display maps a 60 dB
  range with fast-rise/slow-fall exponential smoothing independent of frame rate.
- Transients: positive spectral flux, adaptive mean/deviation threshold and RMS
  noise floor, plus a 180 ms refractory interval. Beat Pulse attacks on an onset,
  follows bass energy and decays over 200 ms. No BPM/tempo estimator is claimed.
- Waveform: signed peak-preserving downsampling to 128 real sample values, drawn
  with Braille subcells so small signals remain visible in short terminals.
- Off: clears the frame, skips FFT/clock polling and pauses widget refresh.
  An already active PCM sink stays until Stop to preserve audio; subsequent
  playback uses the original single-process route while visualization is off.

`t` toggles; `z` cycles Spectrum → Beat Pulse → Waveform → Off. Neither captures
typing in input fields or changes focus. First enabling PCM during ordinary
playback restarts at the live edge. TOML requires restart; keys are session-only.

```toml
[visualizer]
enabled = false
mode = "spectrum" # spectrum | beat-pulse | waveform | off
fps = 30 # integer 10–60
sensitivity = 1.0 # finite 0.25–4.0
```

Defaults preserve old configurations and offline startup. Invalid values fail
with field-specific errors and without echoing user input. No Player setting or
new environment override exists.

## Automated regression results

| Check | Actual result |
| --- | --- |
| Radio pytest, Python 3.13.3 | 242 passed, 57.30 s |
| Radio pytest, Python 3.14.7 | 242 passed, 57.65 s |
| Player pytest, Python 3.13.3 | 426 passed, 94.57 s |
| Player pytest, Python 3.14.7 | 426 passed, 94.92 s |
| Ruff lint and formatting, both products | Passed |
| ShellCheck, both installers and uninstallers | Passed |
| Radio wheel and sdist builds | Passed; version unchanged at 0.1.0a1 |
| Clean Radio wheel/configuration smoke, Python 3.14.7 | Passed; Player/keyring absent |
| Native darwin-arm64 executable build and smoke | Passed; PyInstaller 6.22.3, Python 3.14.7 |
| Installer/configuration/provenance/isolation regressions | Included in passing full suites |

There are 48 new deterministic tests: 27 DSP/configuration, 12 pipeline/lifecycle,
and 9 Textual scenarios (each responsive scenario exercises all presets). Each
application used its own locked environment; the second Python version ran in a
separate temporary environment. Root Player source and lockfile were unchanged.

The final wheel is 36466 bytes and the ARM64 executable is 23789872 bytes. Native
smoke verifies help/version, valid/invalid configuration, stylesheet/resources,
PTY startup and quit; it does not itself verify native-binary audio playback.
The native build's local archive is
`/private/tmp/obelisk-p4-native/obelisk-radio-v0.1.0-alpha.1-darwin-arm64.tar.gz`.
This is a local validation artifact, not a tag or publication. Source/runtime
build provenance is commit `20218e6`; the later verification commit adds this
report only. Native execution required access outside the sandbox for macOS
semaphores/CoreAudio. An initial smoke helper left invalid test configuration;
it was corrected to restore and validate the original file before TUI startup.

Coverage includes original generated silence, bin-centred tones, low/high tones,
impulses, periodic pulses, seeded white noise and abrupt spectral changes; RMS,
FFT normalization, flux/onsets, refractory gating, adaptive noise, attack/decay,
sensitivity, ring bounds, missing/expired samples, mode changes, station reset,
stop/resume, startup failures, stalled pipes, repeated cancellation, stubborn
children, waiting for in-flight DSP, input focus, configuration and isolation.

UI tests exercise every mode at 120×35, 80×24, 60×20 and 40×12 in midnight and
mono, including missing PCM, resize, off, stop and keyboard navigation. Tiny
layouts retain playback/error metadata and at least two station rows. The strict
frontend static audit returned zero findings; project-owned Textual tests provide
runtime evidence. ImageMagick's local SVG renderer did not reproduce all terminal
Unicode glyphs correctly, so raster exports are not treated as font acceptance.

## Actual audio and integration evidence

The initial generated one-second 440 Hz probe produced exactly 192000 clean PCM
bytes and the real default CoreAudio sink exited successfully. The full adapter
then passed simultaneous system output and changing real PCM analysis:

| Check | Generated tone bursts, 8 seconds | Public Groove Salad, 12 seconds |
| --- | ---: | ---: |
| Distinct PCM-derived frames | 234 | 354 |
| Observed analysis rate | 30.02 FPS | 30.00 FPS |
| 95th-percentile frame gap | 34.26 ms | 38.35 ms |
| Detected onsets | 15 | 9 |
| Mean DSP calculation (1000 silent windows) | 0.096 ms | 0.089 ms |
| Python harness CPU snapshot | 6.2% | 4.7% |
| Decoder CPU snapshot | 1.2% | 0.0% |
| Audio sink CPU snapshot | 4.7% | 1.4% |
| Python harness RSS | 117968 KiB | 72208 KiB |
| Decoder RSS | 175504 KiB | 189152 KiB |
| Audio sink RSS | 189280 KiB | 223312 KiB |
| Children reaped on stop | 2/2 | 2/2 |

The script assertions check changing spectrum/waveform values, nonzero measured
RMS, real sink PLAYING state, volume control, off while audio continues and child
reaping. CPU values are `ps` snapshots, not controlled benchmarks; RSS includes
mpv's libraries and the Python harness. Generated input needs external ffmpeg
only for the check; the application does not depend on an ffmpeg executable.

Original property notifications delivered only ~16 analysis FPS; bounded clock
polling fixed this in the final implementation. The recorded 0.02 ms difference
between sampled clock and window end is sample-index rounding, **not measured
audio-to-screen latency**. The window spans 42.67 ms (centre 21.33 ms behind its
end), and rendering adds up to one display interval plus scheduling/IPC delay.
Physical speaker/display synchronization and driver error were not measured.

A separate real-mpv Textual headless pilot exercised all three modes while audio
played, switched between two generated stream identities, stopped/resumed at the
live edge, killed the actual decoder and observed a successful reconnect, resized
to 40×12, turned processing off without stopping playback, played through the
normal one-process route, then enabled PCM from that route. Quit reaped all 11
spawned mpv children. SVG terminal captures for each preset were inspected for
PCM-derived content and layout. This is automated TUI evidence, not a person
watching and hearing synchronization.

The user explicitly confirmed audibility during local checks: “yes i have heard
this”. Human-confirmed audiovisual synchronization remains pending. No underrun
counter instrumentation or long-running CPU/memory soak was performed; successful
short playback does not establish zero underruns or indefinite stability.

## Reproduction and platform acceptance

From the repository root, with mpv installed and a working audio device:

```sh
uv sync --directory apps/radio --frozen --dev --group release
uv run --directory apps/radio ruff check src tests scripts packaging
uv run --directory apps/radio ruff format --check src tests scripts packaging
uv run --directory apps/radio pytest -q
uv run pytest -q
shellcheck scripts/install.sh scripts/uninstall.sh apps/radio/scripts/install.sh apps/radio/scripts/uninstall.sh
uv build --directory apps/radio --no-build-isolation
uv run --directory apps/radio python scripts/smoke_wheel.py dist/obelisk_radio-0.1.0a1-py3-none-any.whl
uv run --directory apps/radio python scripts/check_visualizer.py --seconds 12
uv run --directory apps/radio python scripts/check_visualizer.py --seconds 12 --url https://ice1.somafm.com/groovesalad-128-mp3
```

Native macOS Intel and Linux execution was unavailable. On each target, repeat
the checks in clean Python 3.13 and 3.14 environments; build natively with
`scripts/build_binary.py --tag radio-v0.1.0-alpha.1 --target darwin-amd64` or
`--target linux-amd64` through Radio's `uv run --directory apps/radio python`.
That argument validates the existing version for a local archive; do not create
a git tag, upload an artifact or publish. Unix sockets and `/dev/stdout` are
required; Windows is outside this application's supported platform set.

For human acceptance, launch `obelisk-radio`, choose a public direct MP3/AAC
station and enable with `t`. Watch spectrum/bass pulse/waveform while listening;
use `z` for presets, `+/-` for audible volume, Space for stop/resume, Enter for a
different station, and resize through the required terminal sizes in both themes.
Check that errors remain readable, navigation stays responsive, stopped data
expires, off clears the strip, and quitting leaves none of this run's child PIDs.
Repeat with a denied/unreachable stream and a disconnected audio device. Confirm
font support for block and Braille glyphs in the actual terminal. Record actual
per-device offset/underruns; do not infer physical synchronization from FFT tests.

## Remaining limits and readiness

Ready for a development PR review; not ready for publication. Linux/Intel
acceptance, human visual synchronization and
extended resource/underrun testing remain outstanding. Two mpv processes consume
substantially more memory than default playback. Opposite-phase stereo content
can cancel in the mono analysis mix while audible stereo remains intact. Font
coverage and mpv driver buffering vary by terminal/platform. No known failing
functional regression remains in the completed local checks. The existing Radio
publication hold still applies independently of this implementation.

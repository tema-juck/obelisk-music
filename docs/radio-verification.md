# Issue #16 verification record

Local verification on 2026-11-11, macOS ARM64, branch
`feat/16-internet-radio`. Distribution/import/CLI identities are unchanged:
`obelisk-music`, `obelisk_music`, `obelisk`. Package version stays `0.3.0a2`.
No tag, release, push, merge, or package-version change was made.

## Automated gates

| Check | Actual result |
| --- | --- |
| Python 3.13.18 full pytest | **491 passed**, 110.76 seconds |
| Python 3.14.8 full pytest | **491 passed**, 110.75 seconds |
| Ruff check and format check | Passed, 59 Python files formatted |
| ShellCheck 0.11.0, install/uninstall | Passed |
| Wheel and sdist builds | Passed, version `0.3.0a2` in both metadata files |
| Clean wheel smoke, Python 3.13.18 | Passed outside checkout; Radio and Spotify mocks, CLI/config/resources |
| Clean wheel smoke, Python 3.14.8 | Passed outside checkout; Radio and Spotify mocks, CLI/config/resources |
| Native PyInstaller executable | macOS ARM64 build passed with existing release build flags |
| Native executable smoke | Passed: target, help, version, config, stylesheet, 80×24 PTY startup/quit outside checkout |
| UI static audit | Strict audit: zero findings |
| UI layouts | Headless tests: midnight/mono, 120×35, 80×24, 60×20, 40×12 |

The 416 original tests remain; **75 cases** were added (73 radio regressions and
two missing-radio-resource packaging cases). CI blocks network/keychain/browser
access and uses fake HTTP, DNS and audio processes. Tests cover URL/redirect/DNS
boundaries, station parsing, pagination, directory failures/cooldown, corrupt and
symlinked state, process startup/IPC/volume/switching, missing mpv, bounded retries,
startup cancellation, repeated cancellation during child acquisition/reaping,
forced kill, ICY metadata, input scoping, stale reads, focus, custom/favorite flows,
saved selection without autoplay, compact controls, and source isolation.

Actual commands used the frozen lockfile and cached dependencies, including:

```sh
uv run --frozen --offline ruff check .
uv run --frozen --offline ruff format --check .
uv run --frozen --offline pytest -q
shellcheck scripts/install.sh scripts/uninstall.sh
uv build --offline --no-build-isolation --python /path/to/release-venv/bin/python
uv run python scripts/smoke_wheel.py dist/obelisk_music-0.3.0a2-py3-none-any.whl
python scripts/smoke_binary.py /path/to/native/obelisk
```

The Python 3.14 run used a separate environment with the same frozen dependencies.
The native executable was built directly with the PyInstaller arguments in
`scripts/build_binary.py`, without creating a release archive associated with an
existing Git tag. Existing installer/release/publisher regressions ran in both
full suites. Workflow files and permissions are unchanged; no mpv dependency is
needed for CI or ordinary application startup. These are **local results**, not a
GitHub-hosted CI run or cross-platform binary verification.

## Real backend and directory checks

mpv **0.42.0** with FFmpeg **9.0.2** was installed for local verification.

- Real synthetic MP3 and AAC/ADTS audio decoded through mpv with null output;
  JSON IPC reported PLAYING, volume updates succeeded, and stopping reaped the
  child. These probes establish decoder/IPC behavior, not audibility.
- A real Radio Browser search (`tag=jazz`, `countrycode=US`) through public-IP
  pinning/TLS returned 29 supported stations, one excluded entry, and offset 30.
- The broadcaster's [documented direct stream](https://somafm.com/groovesalad/directstreamlinks.html),
  `https://ice5.somafm.com/groovesalad-128-mp3`, reached PLAYING through the real
  backend and system audio output; volume adjustment and graceful stop passed.
  No stream audio was written to disk.
- The final frozen binary was driven through an 80×24 PTY from a clean temporary
  working directory with isolated XDG paths and no Spotify environment variables:
  library → R → Custom → Enter → PLAYING → q. It exited successfully. This checks
  the frozen application's external mpv path, independent radio operation and
  graceful quit, beyond the usual binary startup smoke.

System audio output was opened successfully, but audibility was not independently
confirmed by a human listener. No native Linux or macOS Intel audio test was run.
Only this mpv version was tested; use an up-to-date backend and verify the
platform's codecs/output drivers. Source/headless tests do not replace that step.

## PCM extension feasibility (synthetic only)

A one-second synthetic sine signal was generated in memory and supplied to mpv.
A private 0600 FIFO in a temporary directory received output with these options:

```text
--ao=pcm
--ao-pcm-file=<private FIFO>
--ao-pcm-waveheader=no
--audio-format=s16
--audio-samplerate=48000
--audio-channels=stereo
```

The FIFO reader received **192,000 nonzero bytes**, exactly one second × 48,000
samples/second × two channels × two bytes/sample, without a WAV header. mpv exited
successfully. No broadcaster audio was used in this probe. See the
[mpv PCM audio-output documentation](https://mpv.io/manual/stable/#audio-output-drivers).
This verifies an actual decoded-sample extension path. It replaces normal audio
output; a future sink must both play samples and supply bounded visualizer data.
JSON IPC does not expose PCM. No visualizer or PCM output runs in Obelisk today.

## Review and release gates

The [radio guide](radio.md#manual-acceptance-macos-and-linux) contains the manual
Issue #16 acceptance checklist for macOS and Linux. Outstanding acceptance:
audible output and prolonged live network-loss/recovery on each native platform,
plus real Spotify regression checks with an authorized account/device.

The current [Spotify Developer Policy](https://developer.spotify.com/policy)
§III.5 restriction on other services' content remains a **public-release blocker**.
Mode separation does not resolve that policy. Code is prepared for review, with
no claim that the combined product is approved for distribution. No release or
issue mutation was performed.

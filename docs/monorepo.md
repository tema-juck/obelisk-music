# Independent Obelisk applications

The monorepo keeps Player at the root and Radio under `apps/radio`. They are
independent uv projects, not workspace members: separate lockfiles, virtual
environments, pytest roots, wheels, sdists, commands, resources and release jobs.
There is no common runtime dependency, source selector, cross-product launcher,
shared authentication, playback control or metadata handoff.

| Contract | Player (unchanged) | Radio |
| --- | --- | --- |
| Distribution | obelisk-music 0.3.0a2 | obelisk-radio 0.1.0a1 (development) |
| Namespace | obelisk_music | obelisk_radio |
| Command | obelisk | obelisk-radio |
| Configuration | XDG config / obelisk/config.toml | XDG config / obelisk-radio/config.toml |
| State/credentials | Existing OS keychain and settings | XDG data / obelisk-radio/radio.json |
| Runtime dependencies | Textual, HTTPX, Pydantic, keyring | Textual, HTTPX, Pydantic; external mpv for playback |
| Tags | vX.Y.Z[-alpha.N/beta.N/rc.N] | radio-vX.Y.Z[-alpha.N/beta.N/rc.N] |
| Executable archives | obelisk-vX.Y.Z-TARGET.tar.gz | obelisk-radio-vX.Y.Z-TARGET.tar.gz |

`XDG_CONFIG_HOME` defaults to `$HOME/.config`; `XDG_DATA_HOME` defaults to
`$HOME/.local/share`. Radio ignores SPOTIFY_CLIENT_ID and Player environment
settings. Only OBELISK_RADIO_THEME and OBELISK_RADIO_VIM_KEYS override its TOML.
Both products keep `config init/edit/path/validate`, --help and --version.
Radio starts in Favorites without contacting any service; discovery and playback
require explicit keyboard actions. No credentials, keyring, or Player installation
are required. The normal mpv adapter has no audio-analysis/visualizer dependency.

## Compatibility and provenance

Player's runtime source, namespace, CLI entry point, version, lockfile, keychain
identity and configuration remain in place. Root pytest collects only `tests`.
Player wheels include only its existing namespace; Player sdists exclude `apps`
and the repository-only boundary test. Radio builds run within `apps/radio` and
collect only `obelisk_radio` plus its own stylesheet and package metadata.

The original seven commits remain unchanged on `feat/16-internet-radio`, ending
at 27d88c2c2fc2b670443206f748d16abce0758b70. They are not merged into this branch.
`apps/radio/PROVENANCE.json` records their IDs and original module SHA256 values.
The audio engine is byte-for-byte preserved, with a regression checking its hash.
Generic UI primitives and descriptor-based file safety code derive from Player;
original MIT notices are retained in the Radio LICENSE. No Spotify models, OAuth,
session, API client, catalog, now-playing or keyring code is copied into Radio.

Experimental radio state at XDG data / obelisk/radio.json is not moved or deleted.
If needed, with both applications stopped, explicitly copy only that Radio JSON
to the new private Radio state directory, without overwriting an existing file.
The schema remains version 1 and validates before use; malformed data remains
untouched and disables saving. Never copy Player TOML or credentials into Radio.
Concurrent writers and automatic cross-product migration are unsupported.
The small UI and safe-I/O primitives are copied intentionally; security fixes to
these application-owned copies must be considered in both projects.

## CI, release validation and installers

Player keeps `.github/workflows/ci.yml` and `release.yml`. Their checks operate on
Player paths and its existing lockfile. Its historical v* tag namespace remains
permanent; no published tag or asset is renamed. Namespaces are disjoint, so a
radio-v* tag cannot trigger the Player release workflow or pass its validator.

Radio uses `radio-ci.yml` and `radio-release.yml`. Both have explicit read-only
permissions and pinned actions. CI tests Ubuntu/macOS with Python 3.13/3.14. The
release validation workflow builds natively on macOS ARM64, macOS Intel and Linux
x86_64, then validates the complete archive set and generates SHA256SUMS. It uploads
workflow artifacts for review only after the policy gate is cleared. The current
workflow deliberately fails its initial policy check before building/uploading
release artifacts. Ordinary Radio CI builds and tests locally on the runner
without uploading binaries. **No Radio GitHub Release publishing job
exists, and the Radio publisher command unconditionally refuses publication.**
Enabling publication requires a separate reviewed change after policy review;
there is no environment variable that bypasses this block. Do not create Radio
release tags while it is held. After approval, merge the reviewed publishing
change before creating a fresh tag: rerunning a held tag uses its original code. Builds do not bundle
mpv, credentials, Spotify data or local configuration.

Player's installer retains all options and receipt formats. Release discovery
filters canonical JSON fields by product tag and draft/prerelease status, ignoring
quoted release-body text and the other product. It checks up to 1000 API entries;
use --version TAG beyond that bound. Radio's installer has the same safeguards but
its own tag namespace, archive names, executable, receipt schema, lock and uninstall
script. Neither installer executes downloaded code or modifies shell profiles.
Both default to user-owned XDG_BIN_HOME / $HOME/.local/bin and can coexist there.
No Radio installer invocation is advertised until Radio has approved releases.

## Local checks

```sh
uv sync --frozen --dev --group release
uv run ruff check src tests scripts packaging
uv run ruff format --check src tests scripts packaging
uv run pytest -q
uv build --no-build-isolation
uv run python scripts/smoke_wheel.py dist/obelisk_music-0.3.0a2-py3-none-any.whl

uv sync --directory apps/radio --frozen --dev --group release
uv run --directory apps/radio ruff check src tests scripts packaging
uv run --directory apps/radio ruff format --check src tests scripts packaging
uv run --directory apps/radio pytest -q
uv build --directory apps/radio --no-build-isolation
uv run --directory apps/radio python scripts/smoke_wheel.py dist/obelisk_radio-0.1.0a1-py3-none-any.whl

shellcheck scripts/install.sh scripts/uninstall.sh apps/radio/scripts/install.sh apps/radio/scripts/uninstall.sh
```

Use clean environments for each product; do not install Player into Radio's
virtual environment. Tests block network and user-data access. Clean wheel smoke
runs outside the checkout; Radio smoke asserts that Player and keyring cannot be
imported. Native smoke tests verify help/version/config/resources and PTY startup
and quit with timeouts; they do not need real audio or any external service.

## Policy review remains required

One repository and independent packaging do not establish legal compliance.
The current [Spotify Developer Policy](https://developer.spotify.com/policy)
section III.5 addresses integration with other services; III.4, III.6 and III.7
also require review for webcasting, visual synchronization and overlapping audio.
The [Developer Terms](https://developer.spotify.com/terms) II.14 include controlling
a background Spotify application in Streaming. No non-streaming exemption is
claimed for Player. Neither application should be described as approved by Spotify. Radio never
queries or pauses Spotify; independently started applications may produce OS
audio concurrently. Whether that fits the overlap provision requires review.

Unresolved questions for written clarification/legal review:

- Whether separate products, distributions and workflows under one repository and
  brand meet the intended product boundary; no technical split guarantees that.
- How the webcasting and overlap provisions apply to independently operated apps.
- Whether retaining the original combined experimental branch raises development
  or enabling concerns; historical commits are preserved, not republished as binaries.
- Broadcaster permissions, geographic limits, directory conditions and any rights
  needed for local audio analysis/visualization or future mpv redistribution.

Do not enable Radio publishing until these questions and the product design have
been reviewed. Player's existing functionality is preserved without a claim that
this monorepo decision resolves its contractual obligations.

## Future radio-only PCM analysis

JSON IPC exposes mpv events/properties, not decoded samples. The previous synthetic
probe verified `--ao=pcm --ao-pcm-file=<private FIFO> --ao-pcm-waveheader=no` with
s16 stereo at 48 kHz, receiving 192000 bytes for a one-second synthetic signal.
This output replaces mpv's system audio output. A future adapter must feed a real
audio sink and a bounded analysis buffer, handle backpressure and synchronize
lifetime with the existing engine. No PCM output or visualizer is enabled here,
and no future analysis path may consume Spotify audio, state or metadata.

Actual local results are recorded in [monorepo verification](monorepo-verification.md).

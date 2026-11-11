# Monorepo local verification

Verified locally on macOS ARM64, 2026-11-11. Branch:
`feat/independent-player-radio`, based on freshly fetched main
`13dbefd15086a0f3afc42f1e33da59129a8b724e`.

## Test matrix

| Product | Python 3.13.18 | Python 3.14.8 |
| --- | --- | --- |
| Player | **426 passed**, 90.62s | **426 passed**, 91.76s |
| Radio | **194 passed**, 44.24s | **194 passed**, 43.84s |

Player retains all 416 original cases plus ten boundary/installer regressions.
Radio ports the transport, storage, audio and browsing coverage, replaces the two
combined-product handoff tests with genuine isolation checks, and adds its own
configuration, installer, archive and policy-gate coverage. The complete original
492-case implementation remains on the untouched radio branch.

| Other check | Actual result |
| --- | --- |
| Ruff check and format check | Passed for root and independent Radio configuration |
| ShellCheck 0.11.0 | Passed on all four install/uninstall scripts |
| Player wheel and sdist | Built; no Radio source/package included |
| Radio wheel and sdist | Built; no Player source/package included |
| Clean wheel smoke, both interpreters | Passed for both products outside checkout |
| Native macOS ARM64 Player | Built; help/version/config/resources and PTY startup/quit passed |
| Native macOS ARM64 Radio | Built; help/version/config/resources and PTY startup/quit passed |
| Frozen module inspection | Player excludes Radio; Radio excludes Player and keyring |
| Workflow YAML | All four workflow files parsed successfully |
| Radio release publication | Deliberately blocked before I/O and before workflow artifact upload |

These are local results, not GitHub-hosted CI results. No native macOS Intel or
Linux executable/audio test was possible here. Fake native archive tests cover
all three platform identities; they do not replace native platform smoke tests.

## Isolation and compatibility evidence

- Player runtime source and uv.lock are unchanged from main. Its distribution,
  CLI, version 0.3.0a2, import namespace, keychain service and config path remain.
- Radio has a separate uv.lock and environment, distribution version 0.1.0a1,
  executable, namespace, stylesheet, config and state paths. No uv workspace or
  shared runtime package was introduced.
- Radio source tests and clean installed-wheel tests prove that neither keyring
  nor obelisk_music is installed/importable. Startup is offline in Favorites and
  ignores deliberately invalid Player settings and environment values.
- Wheel/sdist member inspection and recursive PyInstaller module listings confirm
  product separation and retained MIT license notices.
- Installer regressions cover mixed-product release pages, quoted/nested/malformed
  JSON fields, drafts, stable/prerelease/version selection, checksums, corrupt or
  unsafe archives, symlinks, receipts, permissions, and cross-product coexistence.
- The original seven commit objects remain reachable from feat/16-internet-radio,
  whose tip is unchanged at 27d88c2c2fc2b670443206f748d16abce0758b70. The original
  audio engine hash matches the extracted file byte-for-byte. PROVENANCE.json
  records all seven IDs and original source hashes; MIT attribution is retained.

## Native live radio

The standalone Radio executable was launched from a private temporary directory,
with private XDG config/data and no Spotify environment. A custom broadcaster-
provided SomaFM Groove Salad MP3 stream reached PLAYING through real mpv, then q
quit successfully with process cleanup. No audio was saved. This uses Radio's own
namespace/state and the real frozen binary, rather than a fake audio backend.
Human-confirmed audibility was not established. The previous synthetic MP3/AAC
and PCM FIFO probes are preserved as provenance; no visualizer is implemented.

## Remaining gates

- Obtain the policy clarification/legal review described in monorepo.md. One repo,
  independent packages, shared branding and independent OS audio are not proof of
  compliance. Radio release workflows intentionally stop at the policy hold.
- Run GitHub-hosted CI after an explicitly authorized push/PR; verify required
  status checks and native runner availability.
- Run native Intel/macOS and Linux smokes, audible MP3/AAC acceptance, prolonged
  network-loss/recovery, and a live Spotify account/device regression check.
- Review copied generic file/UI primitives together when applying future fixes.
  Migration of experimental Radio JSON is manual and non-overwriting.

No push, merge, Git tag, GitHub Release, published asset replacement, Homebrew tap,
additional repository, Spotify credential or token was created by this work.

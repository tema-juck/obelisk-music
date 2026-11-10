# Historical release operations

Archived from the original README. Repository URLs and product names below are
historical references; GitHub redirects the former repository name. Follow the
[current release procedure](../releasing.md) for future releases.

## Failed alpha.2 attempt

The [alpha.2 workflow run 42723294518](https://github.com/tema-juck/obelisk-music/actions/runs/42723294518)
ran the immutable commit `23f63ea5e6a045518bac86f9ec8649ad541732b6`. Its
[pyproject.toml](https://github.com/tema-juck/obelisk-music/blob/23f63ea5e6a045518bac86f9ec8649ad541732b6/pyproject.toml)
contains `version = "0.2.0a1"`, while tag `v0.2.0-alpha.2` requires `0.2.0a2`.
The version check correctly failed before native builds or publication; that run
produced no artifacts. Keep the annotated alpha.2 tag object
`7d1ac7f99de7a6af585f1821a1820a150c261955` unchanged.

This attempt was followed by alpha.3, described below, and the published alpha.4.
Use the [current release procedure](../releasing.md) for a new release. Do not delete, move,
recreate, or reuse alpha.2.
A rerun of the old workflow still reads the old tagged commit, even after this
fix is merged into main. Preserve the strict tag/version guard.

## Failed alpha.3 attempt and alpha.4 preparation

The [alpha.3 release run 42725312503](https://github.com/tema-juck/obelisk-music/actions/runs/42725312503)
used commit `9a0525119336f05457c2a35da4d86d257314aed6`. Its `0.2.0a3` metadata
passed tag validation, but pytest reported **314 passed, 1 failed** because
ShellCheck SC2015 flagged the installer/uninstaller's mixed conditional chains.
Native builds and publication were skipped; this run produced no artifacts.

Commit `31cb52e` replaced those guards with explicit POSIX conditionals, preserving
symlink and receipt checks. It is included in main commit
`4a866eb2a274dac911b2b4540140da7942656794`, whose
[Ubuntu/macOS Python 3.13/3.14 CI run](https://github.com/tema-juck/obelisk-music/actions/runs/42727870548)
passed. Alpha.4 was subsequently published as `0.2.0a4`. Keep that release
unchanged; each later candidate requires its own reviewed commit and CI.

Keep the alpha.3 tag object `143bf4a1206ab2876482e0f1252fe84ead3c92ee` and its
original commit unchanged. Rerunning alpha.3 uses the old scripts even after the
fix is merged into main. It was released in alpha.4; use a new version for later
changes. Do not delete, move, recreate, or reuse alpha.3.

## Recover the first `v0.2.0-alpha.1` publication

Historical recovery instructions: alpha.1 was subsequently published. The commands
below require the fixed publisher snapshot `cc4fcbd` with metadata `0.2.0a1`.
The current checkout intentionally rejects v0.2.0-alpha.1 archives/tags; do not
use it for this historical procedure or republish an existing release.

The original [release run 42706661576](https://github.com/tema-juck/obelisk-music/actions/runs/42706661576)
passed quality checks and all three native build/smoke jobs. Its publish job created
a draft, then failed by trying to find that draft through the published-release
endpoint. The log confirms `Contents: write`; the 404 does not indicate missing
publishing permission. The three build artifacts were unexpired when inspected
on 2026-11-09, with expiry on 2026-11-23 UTC. Check their availability again before
recovery. The existing annotated tag resolves to
`290cfe4d70201b498ed3d4dc30e5b1e5a03b6a3c`; keep it unchanged.

**Rerunning that tag-triggered workflow still executes the old tagged code.**
Merging this fix into main does not update the old run. The safest recovery is to
review/merge the fixed publisher, require green CI, then use that publisher with
the original run's three artifacts and original source commit. This changes no
application binary or tag and preserves all archive and publication gates.

On a trusted machine with GitHub CLI authenticated with repository write access,
from the reviewed fixed checkout, stage and validate without publishing:

```bash
export GH_HOST=github.com
export UV_PYTHON=3.13.18
recovery_dir="$(mktemp -d)"
gh run view 42706661576 --repo github.com/tema-juck/obelisk-music --json headSha,jobs
# Confirm the original commit above and success of quality + all three native jobs.
gh run download 42706661576 --repo github.com/tema-juck/obelisk-music --name native-darwin-arm64 --dir "$recovery_dir"
gh run download 42706661576 --repo github.com/tema-juck/obelisk-music --name native-darwin-amd64 --dir "$recovery_dir"
gh run download 42706661576 --repo github.com/tema-juck/obelisk-music --name native-linux-amd64 --dir "$recovery_dir"
uv run --no-project python scripts/release.py validate --tag v0.2.0-alpha.1 --commit 290cfe4d70201b498ed3d4dc30e5b1e5a03b6a3c --directory "$recovery_dir"
```

**Only after explicit publication approval**, in the same shell and fixed checkout:

```bash
uv run --no-project python scripts/release.py publish --tag v0.2.0-alpha.1 --commit 290cfe4d70201b498ed3d4dc30e5b1e5a03b6a3c --repository tema-juck/obelisk-music --directory "$recovery_dir"
```

This resumes the existing draft if visible to the authenticated publisher, verifies
its assets against the original archives and regenerated checksums, and publishes
only the complete set. It does not delete or move the tag. If artifacts have
expired, stop and review a rebuild of the **original tagged commit** on all three
native platforms; fresh binaries may not match existing draft assets. Do not
clobber mismatches or substitute binaries built from current main. A new version
is the safer option if recovering the original set is no longer possible.

This repository does not publish to PyPI, create Homebrew taps, or build Linux
ARM64 in this workflow. Platform verification claims require an actual native
smoke result; local Apple Silicon checks do not verify Intel macOS or Linux.


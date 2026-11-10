# Release engineering

Obelisk Player retains the `obelisk-music` distribution, `obelisk_music` namespace,
`obelisk` executable, and existing archive names. Branding changes apply to future
release titles; already published titles, tags, binaries, and assets are immutable.
The workflow uses `github.repository`, so publishing follows the repository rename.

## Version and build

`pyproject.toml` is the version authority. Installed package metadata supplies
`__version__`, the TUI footer, and `obelisk --version`. PEP 440 `0.3.0a2` maps to
`v0.3.0-alpha.2`; beta and rc tags similarly map to `bN` and `rcN`. Malformed,
mismatched, dev, and local-version tags are rejected.

**v0.3.0-alpha.2 is already published.** The commands below validate and build the
current version locally; they do not create a tag or publish anything. For a new
release, first obtain approval for the version, update `[project].version`, run
`uv lock`, and commit `pyproject.toml` and `uv.lock` together. Use that new version's
tag and wheel path in the build commands.

Release tools are frozen in `uv.lock`, including PyInstaller 6.22.3 and Hatchling
1.33.0. The release workflow pins uv 0.13.7 and Python 3.13.18. On the target OS:

```sh
export UV_PYTHON=3.13.18
uv sync --frozen --dev --group release
uv run python scripts/release.py verify-tag --tag v0.3.0-alpha.2
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv build
uv run python scripts/smoke_wheel.py dist/obelisk_music-0.3.0a2-py3-none-any.whl
# Use the target matching this machine; cross-compilation is refused.
uv run python scripts/build_binary.py --tag v0.3.0-alpha.2 --target darwin-arm64
```

See [supported targets](installation.md) and [development checks](development.md).

## Validation and publication

The binary smoke copies `obelisk` outside the checkout, clears Spotify/Python
configuration variables, checks help/version, waits for the real styled TUI to
render in an 80×24 PTY, sends **Q**, and requires successful exit. Help/version
have 30-second timeouts; startup/quit has 45 seconds; the build has 15 minutes.
No Spotify credentials are required. Archives contain only `obelisk`, `LICENSE`,
and version/platform/commit/checksum build metadata. Tar ordering, ownership,
timestamps, and gzip headers are normalized. Bit-for-bit binary reproducibility
across changing runner images, SDKs, or ad-hoc signatures is not guaranteed.

After review and merge, require green CI on the exact main commit to be tagged.
Tag creation, tag pushing, and publication each require explicit maintainer approval.
Use a new version for application changes; never recreate an existing tag or
republish an existing release. Preserve all existing tags and assets.

The installer constructs `obelisk-{tag}-{target}.tar.gz` names from the selected
tag and verifies `SHA256SUMS`; there is no installer version constant to update.
Do not advertise downloads until all assets are published and verified.

The tag-only release workflow first runs quality/wheel checks, then all three
native regression/build/smoke jobs with read-only repository permissions. Only
the publishing job receives `contents: write`. It validates the complete archive
set without extracting or executing downloads, checks platform headers, package
version, source commit, pinned tool versions, and binary checksums, then generates
`SHA256SUMS`. It verifies the remote tag still points to the build commit.

Publishing discovers drafts through the authenticated, paginated release list,
then creates a **draft** only if no matching release exists. It uploads missing
archives/checksums, verifies every asset by release ID, rechecks the tag, and only
then makes the release public. Alpha/beta/rc releases are marked prerelease and
not Latest. A failed build produces no release; a failed upload or verification leaves only an
unpublished draft. Retrying with the same validated artifacts reuses that draft,
skips identical uploaded assets, and uploads only missing files without clobbering.
Conflicting, unexpected, duplicate, or unfinished (`starter`) assets require manual
investigation; the script neither deletes nor replaces them. An already published
release is an unchanged success only if all expected assets match exactly;
otherwise the script refuses changes. Avoid concurrent manual edits while it runs.

A release is considered absent only after a successful complete release listing
contains no match. Authentication/permission errors, repository 404s, rate limits,
server errors, malformed responses, and network timeouts abort; they are never
converted to "release missing." The published-release-by-tag endpoint cannot find
drafts and is deliberately unused. See [GitHub's release API contracts](https://docs.github.com/en/rest/releases/releases).
Do not move a published tag; use a new version for application changes.

## Historical operations

[Historical release operations](releases/history.md) preserves the failed alpha
attempts and original recovery instructions. These are records, not instructions
to reuse an existing tag. Historical release notes remain under [releases/](releases/).

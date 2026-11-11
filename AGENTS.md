# AGENTS.md — Obelisk monorepo

## Project Overview

One repository contains two independent applications:

- Player stays at the root: distribution `obelisk-music`, namespace
  `obelisk_music`, executable `obelisk`, existing Spotify Connect behavior.
- Radio lives in `apps/radio`: distribution `obelisk-radio`, namespace
  `obelisk_radio`, executable `obelisk-radio`, external mpv playback.

Use separate lockfiles, environments, test roots and release workflows. This is
not a uv workspace. Neither application may import, launch, authenticate for,
control playback in, or share provider metadata with the other. Keep Player's
published compatibility identifiers and configuration paths unchanged.

The original seven radio commits remain on `feat/16-internet-radio`; provenance
is recorded in `apps/radio/PROVENANCE.json`. Preserve MIT attribution and the
proven engine. No combined application or source-switching UI is permitted.

## Tech Stack

- Python 3.13+
- Textual and Rich
- Spotify Web API with OAuth 2.0 PKCE
- Spotify Connect for playback control
- HTTPX for async HTTP requests
- Pydantic v2 for data validation
- uv for dependency management
- pytest for testing
- Ruff for linting and formatting

## Architecture

Keep each application's UI, orchestration, provider I/O and persistence separate.
Radio must not import keyring, Spotify modules or Player settings. Start Radio
offline; make directory and playback requests only on user action.

Follow these principles:

- Use asynchronous I/O for Spotify communication.
- Never block the Textual event loop.
- Prefer small, typed, testable components.
- Keep dependencies minimal.
- Handle Spotify API errors, rate limits, and token expiration.
- Never log or commit authentication tokens.
- Respect Spotify API terms and playback restrictions.

## UI Guidelines

The interface must be minimalist and keyboard-first.

- No traditional buttons or heavy panel borders.
- Compact layouts with clean spacing.
- Subtle colors and strong typography hierarchy.
- Vim-style navigation where appropriate.
- Responsive terminal layouts.
- Support small terminal windows.
- Use reusable custom Textual widgets.
- Prioritize accessibility and readable contrast.

## Development Workflow

For each feature:

1. Inspect the existing implementation.
2. Propose a short implementation plan.
3. Implement the smallest maintainable solution.
4. Add or update tests.
5. Run Ruff and pytest.
6. Review the changes for regressions.
7. Summarize changes and remaining risks.

Do not claim tests passed unless they were actually executed.

## Commands

```bash
uv sync --dev
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run obelisk

uv sync --directory apps/radio --frozen --dev --group release
uv run --directory apps/radio pytest -q
uv run --directory apps/radio obelisk-radio
```

## Project Roadmap

### v0.1 — Foundation
- Spotify OAuth PKCE
- Basic playback controls
- Minimal Textual interface

### v0.2 — Library
- Spotify playlists
- Search
- Track browsing
- Device selection

### v0.3 — Experience
- Custom themes
- Keyboard navigation
- Responsive layouts
- Album artwork where supported
- Queue management

### v0.4 — Release
- Documentation
- CI/CD
- GitHub Releases
- macOS and Linux support

## Git Conventions

Use Conventional Commits:

- feat:
- fix:
- refactor:
- test:
- docs:
- chore:

Avoid unrelated changes in the same commit.

## Releases and policy

Player retains v* tags and historical artifacts. Radio reserves radio-v* tags and
obelisk-radio-* archives. Do not rename existing tags, versions or published assets.
Radio's release workflow and publisher intentionally block publication/artifact
upload pending a separate policy-reviewed change. Do not bypass that gate or
claim legal compliance from packaging separation. See docs/monorepo.md for current
policy questions, commands and compatibility guarantees. Audio analysis may only
consume Radio audio; no visualizer or PCM output is enabled in this restructuring.

# AGENTS.md — Obelisk Music

## Project Overview

Obelisk Music is an open-source, keyboard-first Spotify terminal controller built with Python and Textual.

The goal is to create a fast, minimalist, visually polished terminal music experience inspired by Cliamp, without copying its source code or design assets.

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

Keep UI, business logic, Spotify API communication, and authentication separate.

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
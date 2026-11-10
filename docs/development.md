# Development

Use [uv](https://docs.astral.sh/uv/) and Python 3.11 or newer. CI verifies Python
3.13 and 3.14; native releases use Python 3.13.18.

```sh
git clone https://github.com/tema-juck/obelisk-player.git
cd obelisk-player
uv sync --frozen --dev
uv run obelisk
```

Configure a Spotify Client ID using `uv run obelisk config init` and
`uv run obelisk config edit`. See [Configuration](configuration.md).

## Tests

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

## CI checks locally

GitHub Actions runs on pull requests targeting `main` and pushes to `main`, with
Ubuntu and macOS jobs for Python 3.13 and 3.14. Actions are pinned to commit SHAs;
the workflow pins uv to 0.13.7 and uses read-only repository permissions.

Run the equivalent checks from the repository root:

```bash
export UV_FROZEN=true
uv sync --frozen --dev
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv build
uv run python scripts/smoke_wheel.py dist/*.whl
```

Set `UV_PYTHON=3.13` or `UV_PYTHON=3.14` before these commands to select a matrix
version. uv must be on `PATH`; CI uses 0.13.7. If `dist` contains older wheels,
pass the freshly built wheel's exact path instead of the wildcard.

The smoke check verifies the stylesheet and runtime modules inside the wheel,
creates a temporary virtual environment outside the checkout, installs hashed
runtime dependencies exported from `uv.lock`, and installs the wheel without
development dependencies. It then checks dependency consistency, the `obelisk`
entry point, and headless startup/quit from the installed package in Python
isolated mode. The temporary environment is removed afterward.

Tests use mocked Spotify responses and block network, browser, and keychain
access. No Spotify credentials are needed. Dependency installation and building
can download packages from PyPI; the tests and installed-app probe do not contact
Spotify. Local checks do not establish that GitHub-hosted jobs have passed;
those results are available only after the workflow runs on GitHub.

## Packaging

`uv build` produces the `obelisk-music` source distribution and wheel in `dist/`.
The public name is **Obelisk Player**; the distribution name, `obelisk_music`
namespace, and `obelisk` entry point stay compatible with existing installations.
See [Release engineering](releasing.md) for native executable builds.

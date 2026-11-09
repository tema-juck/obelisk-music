@a_default:
    just --list

@dev:
    uv run uvicorn app.main:app --reload --log-config log_conf.yaml

@check:
    uv run ruff check
    uv run ruff format --check

@lint:
    uv run ruff check --fix

@format:
    uv run ruff format
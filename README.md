# fleetflow-api

FleetFlow backend: Django + DRF modular monolith. Start with [CLAUDE.md](CLAUDE.md) and [architecture.md](architecture.md).

## Requirements

Docker with Compose, and [uv](https://docs.astral.sh/uv/) for running tools on the host.

## Run locally

```sh
cp .env.example .env          # then set DJANGO_SECRET_KEY
docker compose up -d --build  # api, worker, beat, db (PostGIS), redis
curl localhost:8000/health/ready
```

API docs (local only): http://localhost:8000/api/docs/

## Checks

These run on the host against the compose database and Redis, and match CI:

```sh
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy .
uv run python manage.py makemigrations --check --dry-run
uv run pytest --cov
uv run pip-audit
uv run python manage.py spectacular --file openapi/schema.yaml --validate --fail-on-warn
```

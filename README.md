# fleetflow-api

FleetFlow backend: Django + DRF modular monolith. Start with [CLAUDE.md](CLAUDE.md) and [architecture.md](architecture.md).

## Requirements

Docker with Compose. [uv](https://docs.astral.sh/uv/) on the host is only needed to change dependencies (`uv lock`).

## Run locally

```sh
cp .env.example .env          # then set DJANGO_SECRET_KEY
docker compose up -d --build  # api, worker, beat, db (PostGIS), redis, storage (SeaweedFS)
curl localhost:8000/health/ready
```

API docs (local only): http://localhost:8000/api/docs/

## Checks

Run every check inside the API container: GeoDjango needs GDAL and GEOS, which the image provides and the host may not (memory D-API-21). These match CI. `.env` selects local settings, so tests and the schema use the test settings explicitly:

```sh
docker compose build api
alias ffrun='docker compose run --rm -e DJANGO_SETTINGS_MODULE=config.settings.test api'
ffrun ruff check . && ffrun ruff format --check .
ffrun mypy .
ffrun python manage.py makemigrations --check --dry-run
ffrun pytest --cov --cov-fail-under=85
ffrun pip-audit
ffrun python manage.py spectacular --file openapi/schema.yaml --validate --fail-on-warn
```

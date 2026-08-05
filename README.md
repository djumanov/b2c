# B2C backend

Backend for a white-label travel platform: website, mobile app and admin panel
sell flights, rail, insurance, eSIM and transfers sourced from the
[GTS](docs/GTS.md) B2B API.

One installation belongs to one client. There is no tenant column — clients
run on separate servers with separate databases. Everything a client can
change (branding, languages, currencies, payment providers, GTS credentials)
lives in the database and is edited from the panel, **without a redeploy**.

FastAPI · PostgreSQL · Redis · Celery · SQLAlchemy 2.0 async · Alembic.

## Documentation

Read in this order:

| Document | Contents |
|---|---|
| [docs/PROJECT.md](docs/PROJECT.md) | Product and project: scope, decisions, phases, operations |
| [docs/GTS.md](docs/GTS.md) | The upstream B2B platform we consume |
| [docs/API.md](docs/API.md) | The REST contract — conventions and every endpoint |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Internal structure: modules, layers, DB, saga, adapters |

Working rules for this repository: [CLAUDE.md](CLAUDE.md).

## Getting started

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13 (uv installs it).

```bash
cp .env.sample .env          # fill in POSTGRES_*, JWT_SECRET_KEY, ENCRYPTION_KEYS
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000
```

Interactive docs at <http://localhost:8000/api/v1/docs>, liveness at
`/healthz`.

Or bring up the whole stack — api, worker, beat, PostgreSQL, Redis — with
migrations applied automatically on boot:

```bash
docker compose up --build
```

## Quality gates

All three must pass before every commit:

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy app
uv run pytest
```

## Current state

Phase 1 foundation: application skeleton, response envelope, error catalogue,
cross-cutting dependencies, database layer and delivery setup. Domain modules
(auth, settings, content, orders, payments, …) are scaffolded but not yet
implemented — see [docs/PROJECT.md §15](docs/PROJECT.md) for the phase plan.

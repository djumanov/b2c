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
| [docs/order-system/README.md](docs/order-system/README.md) | The order system — orders, payments, ticketing, support desk (Uzbek; the authority for that scope) |
| `/api/v1/docs` (Swagger, run the API) | The REST contract: every endpoint with its description, the envelope, the error catalogue, the two token schemes. Generated from the code — the descriptions live next to the routes and schemas |
| [postman/](postman/) | A collection that walks the flows end to end |

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

The suite needs PostgreSQL and nothing else — Redis is faked. It creates a
`b2c_test` database beside the working one, brings it to the migration head,
and cleans the tables after every test.

## Current state

Live end to end: staff and customer authentication, settings and branding,
content, leads, catalog, the flight search → verify → booking flow against
GTS, the two-step card payment (Payme Subscribe API behind the port; a
sandbox in `DEBUG`), ticketing through GTS, and the support desk
(`/admin/orders/`). `docs/order-system/README.md` §7 lists what comes next.

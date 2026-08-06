# CLAUDE.md

Guidance for Claude Code working in this repository.

## Documents come first

`docs/` is the source of truth and is written in Uzbek. Code, comments,
docstrings and commit messages are in English.

| Document | Authority |
|---|---|
| `docs/API.md` | The REST **contract**. Wins any conflict about what the outside sees |
| `docs/ARCHITECTURE.md` | Internal structure: modules, layers, DB, saga, adapters |
| `docs/PROJECT.md` | Product and project: scope, decisions, phases, operations |
| `docs/GTS.md` | Background on GTS, the upstream B2B platform |
| `docs/PHASES.md` | **Not an authority.** The execution plan: which `API.md` section belongs to which phase, and how each phase breaks down. `PROJECT.md` §15 wins any conflict about phase boundaries |
| `docs/STATUS.md` | **Not an authority.** Where the build has got to and what is next — read it first when picking the work back up |

OpenAPI (`/api/v1/openapi.json`) is an **artefact** of `API.md`, never the
other way round. When the contract changes, `API.md` is edited first.

Picking up work: `STATUS.md` says where the build stopped, `PHASES.md` says
what comes next and why it belongs to that phase.

## Workflow rules

1. **Never commit straight to `main`** once the service is in real use.
   Branch first: `<type>/<short-kebab-desc>`, `<type>` ∈
   `feat|fix|refactor|chore|docs|test`.
2. **All quality gates green before every commit** — no exceptions:
   ```bash
   uv run ruff check . && uv run ruff format .
   uv run mypy app          # strict; must be 0 errors
   uv run pytest
   ```
3. **No `git push` and no PRs without explicit approval.**

## Commands

Dependencies via **uv**, Python **3.13+**. Local PostgreSQL (:5432) and Redis
(:6379), or `docker compose up`. Copy `.env.sample` → `.env`.

```bash
uv sync                                             # install deps
uv run uvicorn app.main:app --reload --port 8000    # API (docs at /api/v1/docs)
uv run celery -A app.tasks.celery_app:celery_app worker -l INFO
uv run celery -A app.tasks.celery_app:celery_app beat -l INFO
uv run alembic upgrade head                         # migrations
uv run alembic revision --autogenerate -m "..."     # new migration
docker compose up --build                           # api + worker + beat + pg + redis
```

## The one rule that keeps this a modular monolith

Modules talk to each other **only through `service` functions**. A module
never imports another module's `models.py` or repository. Break this and the
tree turns into one large tangle (ARCHITECTURE.md §4).

Inside a module the layering holds: `router → service → repository/model`.
Everything outside the process is reached through `providers/` — GTS, payment
providers, notifications and storage each sit behind a port.

## Hard invariants (do not violate)

- **Config, not code, and not env.** Anything a client could want different
  lives in the database behind the panel. `.env` holds infrastructure only:
  database, Redis, JWT secret, encryption key, log level. Adding a setting?
  First question: "could two clients want different values?" (PROJECT.md §7)
- **Handlers never build the envelope.** They return a plain model or a
  `Page`; `api/envelope.py`'s route class wraps it into
  `{status, data, errors, meta}`. The one exception is `/api/v1/webhooks/*`,
  where the response shape is the provider's protocol (API.md §40).
- **One error catalogue.** Raise an `AppError` subclass from
  `api/errors.py`; the single handler maps it to the code and HTTP status in
  API.md §3. No bare `except:`, no silent `return None` on failure.
- **Every path ends in a slash** (API.md §1). A contract test sweeps the route
  table and fails otherwise.
- **Two token subjects that never cross.** `aud: public` for customers,
  `aud: admin` for staff. A customer token on `/admin/*` gets **403**, not 401
  (API.md §4).
- **Cross-cutting work is a dependency, not handler code**: envelope, errors,
  auth, RBAC, pagination, idempotency, audit. This is the only way ~150
  endpoints across two surfaces stay consistent (ARCHITECTURE.md §13.4).
- **Money is `Decimal`, never `float`** — `NUMERIC(18,2)` plus a separate
  `CHAR(3)` currency, serialised as a string (API.md §1).
- **Datetimes are timezone-aware and stored in UTC.** Day-grouping in reports
  converts to the installation timezone, which is a database setting.
- **Search results are never stored** — not in Postgres, not in Redis. GTS
  keeps its own cache keyed by `request_id`; `offers/` is a passthrough
  (ARCHITECTURE.md D2, §9). A regression test guards this.
- **Card numbers are never stored and never logged** (PROJECT.md §13).
- No `print()` — use the structlog logger.

## Layout

Folder tree, module responsibilities and the reasoning behind them:
ARCHITECTURE.md §4 and §5. The tree maps 1:1 onto the document's sections, so
a change described in the docs points at exactly one package.

# The artefact handed to the client (PROJECT.md §14). One image runs the API,
# the worker and beat — the command decides which.
#
# Nothing client-specific is baked in: branding, credentials and settings live
# in the database, so every client runs the same image (PROJECT.md §7).

# uv is pinned to an exact version and copied in as a binary. The floating
# `uv:python3.13-*` tags lag behind and ship a uv old enough to reject a lock
# file written by a newer one — which would turn a client's rebuild into a
# resolution failure with no obvious cause.
FROM ghcr.io/astral-sh/uv:0.11.13 AS uv

FROM python:3.13-slim-bookworm AS builder

COPY --from=uv /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, in their own layer: they change far less often than the
# source, so a code edit does not reinstall the world.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.13-slim-bookworm AS runtime

# curl is here for the container healthcheck; postgresql-client for the
# entrypoint's readiness wait.
RUN apt-get update \
    && apt-get install --no-install-recommends -y curl postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 app

WORKDIR /app

COPY --from=builder --chown=app:app /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN chmod +x /app/docker/entrypoint.sh \
    && mkdir -p /app/uploads && chown -R app:app /app/uploads

USER app

EXPOSE 8000

# No HEALTHCHECK here on purpose. The same image runs the API, the worker and
# beat, and only the API serves HTTP — an image-level HTTP probe would mark the
# other two permanently unhealthy, which is exactly the signal a client needs
# to stay meaningful. Each role declares its own check in docker-compose.yml.

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["api"]

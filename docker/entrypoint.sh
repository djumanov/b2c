#!/usr/bin/env bash
# Container entrypoint. One image, three roles: api | worker | beat.
#
# The API role migrates the database and creates the first owner before it
# starts serving. The client runs upgrades themselves and cannot be asked to
# run a command afterwards, so both steps have to happen on boot
# (PROJECT.md §14, ARCHITECTURE.md §12).
set -euo pipefail

ROLE="${1:-api}"

wait_for_postgres() {
    local attempts=${POSTGRES_WAIT_ATTEMPTS:-60}
    echo "entrypoint: waiting for postgres at ${POSTGRES_HOST:-localhost}:${POSTGRES_PORT:-5432}"
    for _ in $(seq "$attempts"); do
        if pg_isready \
            -h "${POSTGRES_HOST:-localhost}" \
            -p "${POSTGRES_PORT:-5432}" \
            -U "${POSTGRES_USER:-postgres}" \
            -q; then
            return 0
        fi
        sleep 1
    done
    echo "entrypoint: postgres did not become ready in ${attempts}s" >&2
    exit 1
}

case "$ROLE" in
    api)
        wait_for_postgres
        # Migrations run here and only here. If the worker migrated too, a
        # scaled-out deployment would race two upgrades against one database.
        echo "entrypoint: alembic upgrade head"
        alembic upgrade head

        # First boot only: creates the owner from the environment, then never
        # again. It must fail loudly — an installation whose bootstrap was
        # skipped has nobody who can sign in, and `set -e` stops the container
        # rather than serving an API no one can administer.
        echo "entrypoint: bootstrap first owner"
        python /app/docker/bootstrap.py

        # --no-access-log: the access line is written by RequestIdMiddleware,
        # which is the only one that can attach the request id to it.
        exec uvicorn app.main:app \
            --host 0.0.0.0 \
            --port 8000 \
            --no-access-log \
            --proxy-headers \
            --forwarded-allow-ips '*'
        ;;
    worker)
        wait_for_postgres
        exec celery -A app.tasks.celery_app:celery_app worker \
            --loglevel "${LOG_LEVEL:-INFO}" \
            --concurrency "${CELERY_CONCURRENCY:-4}"
        ;;
    beat)
        wait_for_postgres
        # Keep the schedule file on a writable path inside the container.
        exec celery -A app.tasks.celery_app:celery_app beat \
            --loglevel "${LOG_LEVEL:-INFO}" \
            --schedule /tmp/celerybeat-schedule
        ;;
    *)
        # Anything else is run verbatim, which keeps `docker compose run` and
        # one-off debugging possible.
        exec "$@"
        ;;
esac

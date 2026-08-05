"""Async Alembic environment. The URL comes from app settings, never a file.

Migrations run automatically when the container starts, on a schedule we do not
control: the client upgrades when they choose and may skip several versions
(PROJECT.md D10). Two things follow, and both are the migration author's job
rather than something this file can enforce:

* each revision must stand on its own and be reversible;
* the chain must stay single-headed — a branch cannot be resolved by a client
  who has never heard of Alembic.
"""

import asyncio
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import settings
from app.db.base import Base

# Import every model module here so its table registers on Base.metadata and
# autogenerate can see it. One line per module, added as modules are built.
# (none yet)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def _configure(**kwargs: Any) -> None:
    context.configure(
        target_metadata=target_metadata,
        # Detect column type changes; without it a widened column silently
        # never migrates.
        compare_type=True,
        compare_server_default=True,
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(
        url=settings.database_url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())

"""Alembic environment (ATLAS-18)."""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from atlas.storage.db import ensure_sqlite_parent, resolve_url
from atlas.storage.tables import Base

config = context.config
target_metadata = Base.metadata

# Precedence: explicit -x/config URL (tests), then ATLAS_DATABASE_URL,
# then the default local SQLite file.
url = config.get_main_option("sqlalchemy.url") or resolve_url()

# The default .atlas/ directory is gitignored and absent on a fresh
# checkout; create it so online migrations can open the SQLite file.
ensure_sqlite_parent(url)


def run_migrations_offline() -> None:
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = url
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

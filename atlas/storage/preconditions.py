"""Storage-backed command preconditions."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

from atlas.storage.db import Database

MIGRATION_FIX_COMMAND = "uv run alembic upgrade head"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATIONS_DIR = _REPO_ROOT / "atlas" / "storage" / "migrations"


class SchemaDriftError(RuntimeError):
    """The store is stamped below the migration revision shipped by the code."""

    def __init__(self, *, store_revision: str, code_head: str) -> None:
        self.store_revision = store_revision
        self.code_head = code_head
        super().__init__(
            "SCHEMA_DRIFT: database alembic revision "
            f"{store_revision} does not match code head {code_head}; "
            f"run `{MIGRATION_FIX_COMMAND}`."
        )


def _alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS_DIR))
    return config


def _database_schema_heads(database: Database) -> tuple[str, ...]:
    with database.engine.connect() as connection:
        context = MigrationContext.configure(connection)
        return tuple(context.get_current_heads())


def database_schema_revision(database: Database) -> str | None:
    """Return the Alembic revision stamped on the store, if any."""
    store_heads = _database_schema_heads(database)
    return ", ".join(store_heads) if store_heads else None


def assert_schema_at_head(database: Database) -> None:
    """Fail when a stamped store is behind the migration head.

    Test fixtures commonly build schemas with ``Database.create_all()``, which
    creates no ``alembic_version`` row. A cold live database likewise has no
    stamp. Both cases intentionally fall through so their existing command
    contracts remain unchanged; this helper owns only the stamped-drift case.
    """
    store_heads = _database_schema_heads(database)
    if not store_heads:
        return

    script = ScriptDirectory.from_config(_alembic_config())
    code_heads = tuple(script.get_heads())
    if set(store_heads) == set(code_heads):
        return

    raise SchemaDriftError(
        store_revision=", ".join(store_heads),
        code_head=", ".join(code_heads),
    )

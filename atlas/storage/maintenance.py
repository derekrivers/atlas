"""Storage maintenance primitives (dev/operator tooling support).

``clear_all_data`` empties every Atlas model table while preserving the schema
and the migration head. It backs ``scripts/reset_db.py`` but is a tested,
package-internal function in its own right — the script is thin glue over it.
"""

from __future__ import annotations

import sqlalchemy as sa

from atlas.storage.db import Database
from atlas.storage.tables import Base


def clear_all_data(db: Database) -> dict[str, int]:
    """Delete every row from all Atlas model tables, returning {table: rows}.

    Deletes in REVERSED ``Base.metadata.sorted_tables`` order (children before
    parents) inside one transaction, so it is FK-safe on both SQLite and
    Postgres. Iterating the model metadata means ``alembic_version`` is never in
    the set — the schema and the migration head are preserved by construction.
    Empties data; it does NOT drop or recreate tables.

    Each table's count is read with ``SELECT count(*)`` BEFORE the delete rather
    than from ``result.rowcount``: rowcount is unreliable for an unqualified
    ``DELETE`` on SQLite (the truncate optimisation) while accurate on Postgres,
    so the returned counts would otherwise be dialect-dependent.
    """
    counts: dict[str, int] = {}
    with db.engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            count = conn.execute(
                sa.select(sa.func.count()).select_from(table)
            ).scalar_one()
            conn.execute(table.delete())
            counts[table.name] = int(count)
    return counts

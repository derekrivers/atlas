"""split lesson provenance from citation history (ATLAS-172)

Lessons used ``related_ticket_ids[0]`` as provenance and later appended
completed-ticket citations to the same list. This migration preserves the old
source value in ``source_ticket_id`` and leaves ``related_ticket_ids`` as
citations only. Downgrade restores the old positional shape by prepending the
source back to the citation list.

Revision ID: 0020
Revises: 0019
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from alembic import op

from atlas.storage.tables import JSONB

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


lessons = sa.table(
    "lessons",
    sa.column("id", sa.Uuid()),
    sa.column("source_ticket_id", sa.Uuid()),
    sa.column("related_ticket_ids", JSONB),
)


def _json_list(value: Any, *, lesson_id: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        raise RuntimeError(
            f"lesson {lesson_id} has non-list related_ticket_ids; cannot migrate"
        )
    return [str(item) for item in value]


def upgrade() -> None:
    with op.batch_alter_table("lessons") as batch:
        batch.add_column(sa.Column("source_ticket_id", sa.Uuid(), nullable=True))

    connection = op.get_bind()
    rows = connection.execute(
        sa.select(lessons.c.id, lessons.c.related_ticket_ids)
    ).mappings()
    for row in rows:
        related_ticket_ids = _json_list(row["related_ticket_ids"], lesson_id=row["id"])
        if not related_ticket_ids:
            raise RuntimeError(
                f"lesson {row['id']} has no positional source ticket to migrate"
            )
        source_ticket_id = UUID(related_ticket_ids[0])
        citation_ticket_ids = related_ticket_ids[1:]
        connection.execute(
            lessons.update()
            .where(lessons.c.id == row["id"])
            .values(
                source_ticket_id=source_ticket_id,
                related_ticket_ids=citation_ticket_ids,
            )
        )

    with op.batch_alter_table("lessons") as batch:
        batch.alter_column(
            "source_ticket_id",
            existing_type=sa.Uuid(),
            nullable=False,
        )


def downgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(
            lessons.c.id,
            lessons.c.source_ticket_id,
            lessons.c.related_ticket_ids,
        )
    ).mappings()
    for row in rows:
        citation_ticket_ids = _json_list(row["related_ticket_ids"], lesson_id=row["id"])
        old_related_ticket_ids = [str(row["source_ticket_id"]), *citation_ticket_ids]
        connection.execute(
            lessons.update()
            .where(lessons.c.id == row["id"])
            .values(related_ticket_ids=old_related_ticket_ids)
        )

    with op.batch_alter_table("lessons") as batch:
        batch.drop_column("source_ticket_id")

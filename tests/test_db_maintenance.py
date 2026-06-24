"""clear_all_data (dev DB reset primitive): empties every model table FK-safely
while leaving the schema and migration head untouched.

The wipe deletes in REVERSED Base.metadata.sorted_tables order so children go
before parents. That order is load-bearing only under FK enforcement — and the
SQLite the suite uses has foreign_keys OFF by default, so a forward-order delete
would pass vacuously. The FK-safety test therefore enables PRAGMA foreign_keys=ON
on its OWN engine and adds a negative control proving forward order DOES raise.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from test_models_validation import (
    dependency_kwargs,
    epic_kwargs,
    product_kwargs,
    ticket_kwargs,
)

from atlas.core.models import Epic, Product, Ticket, TicketDependency
from atlas.storage import (
    Database,
    EpicRepo,
    ProductRepo,
    TicketDependencyRepo,
    TicketRepo,
    clear_all_data,
)
from atlas.storage.tables import Base

# The four tables the coherent seed below populates (one row each); every other
# model table stays empty.
_SEEDED_TABLES = {"products", "epics", "tickets", "ticket_dependencies"}


def _db(tmp_path: Path, *, enforce_fk: bool = False) -> Database:
    """A provisioned temp SQLite DB. With enforce_fk, register a connect
    listener turning on PRAGMA foreign_keys BEFORE the first connection (created
    by create_all) — local to this engine, so it does not affect the suite."""
    db = Database(f"sqlite:///{tmp_path / 'atlas.db'}")
    if enforce_fk:

        @event.listens_for(db.engine, "connect")
        def _enable_fk(dbapi_conn: object, _record: object) -> None:
            cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    db.create_all()
    return db


def _seed_coherent_graph(db: Database) -> None:
    """A Product -> Epic -> Ticket -> TicketDependency chain whose FKs all
    resolve, so it inserts cleanly even with FK enforcement on."""
    product = Product(**product_kwargs())
    epic = Epic(**epic_kwargs() | {"product_id": product.id})
    ticket = Ticket(**ticket_kwargs() | {"product_id": product.id, "epic_id": epic.id})
    dependency = TicketDependency(
        **dependency_kwargs()
        | {"source_ticket_id": ticket.id, "target_entity_id": ticket.id}
    )
    ProductRepo(db).add(product)
    EpicRepo(db).add(epic)
    TicketRepo(db).add(ticket)
    TicketDependencyRepo(db).add(dependency)


def test_clear_all_data_empties_every_model_table(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed_coherent_graph(db)

    counts = clear_all_data(db)

    # Every table is reported, the four seeded ones held exactly one row, the
    # rest zero. A table left non-empty or a count mismatch fails here.
    assert {table for table, rows in counts.items() if rows} == _SEEDED_TABLES
    assert all(count == 1 for table, count in counts.items() if table in _SEEDED_TABLES)
    assert sum(counts.values()) == len(_SEEDED_TABLES)
    # And the tables really are empty afterwards (counts came from before-delete).
    with db.engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            assert conn.execute(table.select()).first() is None


def test_clear_all_data_is_fk_safe_and_forward_order_is_not(tmp_path: Path) -> None:
    db = _db(tmp_path, enforce_fk=True)
    _seed_coherent_graph(db)

    # Reversed order (children first) completes under real FK enforcement.
    counts = clear_all_data(db)
    assert counts["ticket_dependencies"] == 1

    # Negative control: with the SAME FK enforcement on, a FORWARD-order delete
    # (parents first) MUST raise — proving the enforcement is actually live and
    # the reversed order above is load-bearing, not vacuously passing.
    _seed_coherent_graph(db)
    with pytest.raises(IntegrityError), db.engine.begin() as conn:
        for table in Base.metadata.sorted_tables:  # forward: parents first
            conn.execute(table.delete())


def test_clear_all_data_touches_only_model_tables(tmp_path: Path) -> None:
    # The cleared set is exactly the model metadata's tables — alembic_version is
    # never among them, so the schema and migration head are preserved by
    # construction.
    db = _db(tmp_path)
    counts = clear_all_data(db)
    assert set(counts) == {table.name for table in Base.metadata.sorted_tables}
    assert "alembic_version" not in counts


def test_clear_all_data_is_idempotent_on_an_empty_db(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _seed_coherent_graph(db)
    clear_all_data(db)

    # A second wipe returns all-zero counts and raises nothing.
    counts = clear_all_data(db)
    assert set(counts) == {table.name for table in Base.metadata.sorted_tables}
    assert all(count == 0 for count in counts.values())

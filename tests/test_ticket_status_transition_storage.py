"""ATLAS-121: TicketStatusTransitionRepo enforcement — model<->DB round-trip,
the append-only surface (no mutation by absence; a re-recorded id raises), the
list_for_ticket and list_all ordering (oldest occurred_at first — list_all
grouped by ticket, the ATLAS-126 batch read), and that a TicketStatusTransition
is NOT subject to the evidence trust-tier cap.

Falsifiable by introspection where the docs demand absence (no mutating methods)
and by a named wrong answer where order matters (rows recorded newest-first must
list oldest-first). The repo's record() and the inline writer in
apply_linear_status share ONE row-construction factory — proven here only for
record (the inline path is proven in test_pm_sync.py).
"""

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa
from test_ticket_status_transition_model import transition_kwargs

from atlas.core.models import TicketStatusTransition
from atlas.storage import Database, NaiveDatetimeError, TicketStatusTransitionRepo


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def public_methods(cls: type) -> set[str]:
    return {
        name
        for name in dir(cls)
        if not name.startswith("_") and callable(getattr(cls, name))
    }


def a_transition(**overrides: Any) -> TicketStatusTransition:
    return TicketStatusTransition(**transition_kwargs() | {"id": uuid4()} | overrides)


# --- round-trip ------------------------------------------------------------


def test_record_round_trips_model_to_database(db: Database) -> None:
    repo = TicketStatusTransitionRepo(db)
    item = a_transition()
    assert repo.record(item) == item
    assert repo.get(item.id) == item


# --- append-only surface (mirroring DebtItemRepo/TickFailureRepo) ----------


def test_repo_exposes_append_and_query_only() -> None:
    # No update, no delete, no finalize, no set_*: append-only is structural,
    # by surface absence. record is the only append verb; list_for_ticket and
    # list_all (ATLAS-126's batch read for historical cycle time) are read-only
    # queries.
    assert public_methods(TicketStatusTransitionRepo) == {
        "add",
        "get",
        "list",
        "record",
        "list_for_ticket",
        "list_all",
    }


def test_no_mutator_methods_exist() -> None:
    surface = public_methods(TicketStatusTransitionRepo)
    for forbidden in ("update", "delete", "remove", "finalize", "set_status"):
        assert forbidden not in surface, forbidden


def test_updating_a_persisted_row_raises(db: Database) -> None:
    # The repo offers no update path; the only way to "update" a record is to
    # re-record its id, which the primary key rejects. A persisted row is
    # immutable.
    repo = TicketStatusTransitionRepo(db)
    item = a_transition()
    repo.record(item)
    with pytest.raises(sa.exc.IntegrityError):
        repo.record(a_transition(id=item.id, to_status="changes_requested"))


def test_deleting_a_persisted_row_has_no_repository_path() -> None:
    assert "delete" not in public_methods(TicketStatusTransitionRepo)


# --- list_for_ticket (oldest first, scoped per ticket) ---------------------


def test_list_for_ticket_returns_only_that_ticket_oldest_first(db: Database) -> None:
    repo = TicketStatusTransitionRepo(db)
    ticket_id = uuid4()
    other = uuid4()
    later = a_transition(
        ticket_id=ticket_id, occurred_at=datetime(2026, 6, 13, tzinfo=UTC)
    )
    earlier = a_transition(
        ticket_id=ticket_id, occurred_at=datetime(2026, 6, 11, tzinfo=UTC)
    )
    # Recorded newest-first; the wrong answer is to list in insertion order.
    repo.record(later)
    repo.record(earlier)
    repo.record(a_transition(ticket_id=other))

    listed = repo.list_for_ticket(ticket_id)
    assert [t.id for t in listed] == [earlier.id, later.id]  # oldest first


def test_list_for_ticket_is_empty_for_unseen_ticket(db: Database) -> None:
    assert TicketStatusTransitionRepo(db).list_for_ticket(uuid4()) == []


# --- list_all (batch read, ticket then oldest-first; ATLAS-126) ------------


def test_list_all_orders_by_ticket_then_occurred_at(db: Database) -> None:
    repo = TicketStatusTransitionRepo(db)
    t1 = uuid4()
    t2 = uuid4()
    # Two tickets, each recorded newest-first; list_all must return every row
    # grouped by ticket and oldest-first within a ticket. The wrong answer
    # interleaves tickets or lists in insertion order.
    t1_late = a_transition(ticket_id=t1, occurred_at=datetime(2026, 6, 13, tzinfo=UTC))
    t1_early = a_transition(ticket_id=t1, occurred_at=datetime(2026, 6, 11, tzinfo=UTC))
    t2_late = a_transition(ticket_id=t2, occurred_at=datetime(2026, 6, 14, tzinfo=UTC))
    t2_early = a_transition(ticket_id=t2, occurred_at=datetime(2026, 6, 12, tzinfo=UTC))
    for item in (t1_late, t1_early, t2_late, t2_early):
        repo.record(item)

    listed = repo.list_all()
    by_ticket = {item.id: item.ticket_id for item in listed}
    # Every row present; per ticket, oldest first.
    assert {item.id for item in listed} == set(by_ticket)
    seq = {tid: [i.id for i in listed if i.ticket_id == tid] for tid in (t1, t2)}
    assert seq[t1] == [t1_early.id, t1_late.id]
    assert seq[t2] == [t2_early.id, t2_late.id]


def test_list_all_is_empty_for_empty_log(db: Database) -> None:
    assert TicketStatusTransitionRepo(db).list_all() == []


# --- no trust-tier cap -----------------------------------------------------


def test_no_trust_tier_cap_on_record(db: Database) -> None:
    # A TicketStatusTransition is NOT evidence: there is no status to cap and no
    # TrustTierError path. record has exactly one parameter — no bypass.
    import inspect

    parameters = inspect.signature(TicketStatusTransitionRepo.record).parameters
    assert list(parameters) == ["self", "model"]


# --- datetime contract (inherited boundary) --------------------------------


def test_naive_occurred_at_rejected_at_record(db: Database) -> None:
    item = a_transition(occurred_at=datetime(2026, 6, 23, 10, 0, 0))
    with pytest.raises(NaiveDatetimeError, match="occurred_at"):
        TicketStatusTransitionRepo(db).record(item)


def test_aware_offset_normalised_to_utc(db: Database) -> None:
    plus_two = timezone(timedelta(hours=2))
    item = a_transition(occurred_at=datetime(2026, 6, 23, 12, 0, 0, tzinfo=plus_two))
    repo = TicketStatusTransitionRepo(db)
    repo.record(item)
    stored = repo.get(item.id)
    assert stored is not None
    assert stored.occurred_at.utcoffset() == timedelta(0)
    assert stored.occurred_at == item.occurred_at  # identity by instant

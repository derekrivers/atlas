"""ATLAS-116: DebtItemRepo enforcement — model<->DB round-trip, the
append-only surface (no mutation by absence; a re-recorded id raises),
the recurrence boundary (seeded off-by-one guard), and that a DebtItem is
NOT subject to the evidence trust-tier cap.

Falsifiable by introspection where the docs demand absence (no mutating
methods, no trust-tier bypass) and by a named wrong answer where the
boundary matters (the 2-row case is asserted NOT recurring).
"""

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa
from test_debt_item_model import debt_item_kwargs

from atlas.core.models import AnomalyType, DebtItem
from atlas.storage import Database, DebtItemRepo, NaiveDatetimeError


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


def an_item(**overrides: Any) -> DebtItem:
    return DebtItem(**debt_item_kwargs() | {"id": uuid4()} | overrides)


# --- round-trip ------------------------------------------------------------


def test_record_round_trips_model_to_database(db: Database) -> None:
    repo = DebtItemRepo(db)
    item = an_item()
    assert repo.record(item) == item
    assert repo.get(item.id) == item


# --- append-only surface (decision D4, mirroring Evidence) -----------------


def test_repo_exposes_append_and_queries_only() -> None:
    # No update, no delete, no finalize, no set_*: append-only is
    # structural, by surface absence, exactly as EvidenceRepo.
    assert public_methods(DebtItemRepo) == {
        "add",
        "get",
        "list",
        "record",
        "list_for_ticket",
        "recurring",
    }


def test_no_mutator_methods_exist() -> None:
    # Names the wrong answer directly: any mutation verb appearing on the
    # repo is a defect.
    surface = public_methods(DebtItemRepo)
    for forbidden in ("update", "delete", "remove", "finalize", "set_status"):
        assert forbidden not in surface, forbidden


def test_updating_a_persisted_row_raises(db: Database) -> None:
    # The repo offers no update path; the only honest way to "update" an
    # observation is to re-record its id, which the primary key rejects.
    # A persisted DebtItem is immutable.
    repo = DebtItemRepo(db)
    item = an_item()
    repo.record(item)
    with pytest.raises(sa.exc.IntegrityError):
        repo.record(an_item(id=item.id, summary="rewritten"))


def test_deleting_a_persisted_row_has_no_repository_path() -> None:
    # Deletion is precluded by surface absence: there is no delete verb to
    # call. (Asserted alongside the update guard so both halves of
    # append-only are shown.)
    assert "delete" not in public_methods(DebtItemRepo)


# --- recurrence predicate (decision D3; off-by-one guard) ------------------


def test_recurrence_boundary_two_is_not_recurring_three_is(db: Database) -> None:
    repo = DebtItemRepo(db)
    ticket_id = uuid4()
    kind = AnomalyType.OUT_OF_OWNERSHIP_TRANSITION

    repo.record(an_item(ticket_id=ticket_id, anomaly_type=kind))
    repo.record(an_item(ticket_id=ticket_id, anomaly_type=kind))
    # The seeded wrong answer: with the default threshold of 3, TWO rows
    # must NOT recur. An off-by-one (>=2, or > used as >=) fails here.
    assert repo.recurring(ticket_id, kind) is False

    repo.record(an_item(ticket_id=ticket_id, anomaly_type=kind))
    # The third row flips it.
    assert repo.recurring(ticket_id, kind) is True


def test_recurrence_threshold_is_parameterised(db: Database) -> None:
    repo = DebtItemRepo(db)
    ticket_id = uuid4()
    kind = AnomalyType.REVIEW_CYCLE
    repo.record(an_item(ticket_id=ticket_id, anomaly_type=kind))
    repo.record(an_item(ticket_id=ticket_id, anomaly_type=kind))
    assert repo.recurring(ticket_id, kind, threshold=2) is True
    assert repo.recurring(ticket_id, kind, threshold=3) is False


def test_recurrence_is_scoped_per_ticket_and_per_type(db: Database) -> None:
    repo = DebtItemRepo(db)
    ticket_id = uuid4()
    other_ticket = uuid4()
    # Three rows for this ticket but spread across types: no single type
    # recurs, and a different ticket's count is not borrowed.
    repo.record(
        an_item(
            ticket_id=ticket_id, anomaly_type=AnomalyType.OUT_OF_OWNERSHIP_TRANSITION
        )
    )
    repo.record(an_item(ticket_id=ticket_id, anomaly_type=AnomalyType.REVIEW_CYCLE))
    repo.record(an_item(ticket_id=ticket_id, anomaly_type=AnomalyType.DWELL_BREACH))
    repo.record(an_item(ticket_id=other_ticket, anomaly_type=AnomalyType.REVIEW_CYCLE))
    for kind in AnomalyType:
        assert repo.recurring(ticket_id, kind) is False


def test_recurrence_reads_nothing_for_unseen_ticket(db: Database) -> None:
    repo = DebtItemRepo(db)
    assert repo.recurring(uuid4(), AnomalyType.DWELL_BREACH) is False


# --- list_for_ticket -------------------------------------------------------


def test_list_for_ticket_returns_only_that_ticket_in_observation_order(
    db: Database,
) -> None:
    repo = DebtItemRepo(db)
    ticket_id = uuid4()
    other = uuid4()
    later = an_item(ticket_id=ticket_id, observed_at=datetime(2026, 6, 13, tzinfo=UTC))
    earlier = an_item(
        ticket_id=ticket_id, observed_at=datetime(2026, 6, 11, tzinfo=UTC)
    )
    repo.record(later)
    repo.record(earlier)
    repo.record(an_item(ticket_id=other))

    listed = repo.list_for_ticket(ticket_id)
    assert [d.id for d in listed] == [earlier.id, later.id]  # oldest first


# --- no trust-tier cap (decision D2) ---------------------------------------


def test_no_trust_tier_cap_on_record(db: Database) -> None:
    # A DebtItem is NOT evidence: there is no status to cap and no
    # TrustTierError path. record has exactly one parameter — no bypass,
    # because there is nothing to bypass.
    import inspect

    parameters = inspect.signature(DebtItemRepo.record).parameters
    assert list(parameters) == ["self", "model"]


# --- datetime contract (inherited boundary) --------------------------------


def test_naive_observed_at_rejected_at_record(db: Database) -> None:
    item = an_item(observed_at=datetime(2026, 6, 12, 10, 0, 0))
    with pytest.raises(NaiveDatetimeError, match="observed_at"):
        DebtItemRepo(db).record(item)


def test_aware_offset_normalised_to_utc(db: Database) -> None:
    plus_two = timezone(timedelta(hours=2))
    item = an_item(observed_at=datetime(2026, 6, 12, 12, 0, 0, tzinfo=plus_two))
    repo = DebtItemRepo(db)
    repo.record(item)
    stored = repo.get(item.id)
    assert stored is not None
    assert stored.observed_at.utcoffset() == timedelta(0)
    assert stored.observed_at == item.observed_at  # identity by instant

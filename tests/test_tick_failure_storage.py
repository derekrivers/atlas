"""ATLAS-125: TickFailureRepo enforcement — model<->DB round-trip, the
append-only surface (no mutation by absence; a re-recorded id raises), the
recorded_since dedup boundary (inclusive, per-signature, writes nothing),
and that a TickFailure is NOT subject to the evidence trust-tier cap.

Falsifiable by introspection where the docs demand absence (no mutating
methods) and by a named wrong answer where the boundary matters (a row one
hour before the window is asserted NOT recorded-since; a different signature
never satisfies). The writes-nothing guard (row count unchanged after the
call) is what proves recorded_since is a pure query-time predicate.
"""

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa
from test_tick_failure_model import tick_failure_kwargs

from atlas.core.models import TickFailure
from atlas.storage import Database, NaiveDatetimeError, TickFailureRepo


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


def a_failure(**overrides: Any) -> TickFailure:
    return TickFailure(**tick_failure_kwargs() | {"id": uuid4()} | overrides)


def _row_count(db: Database) -> int:
    with db.session() as session:
        return (
            session.scalar(
                sa.select(sa.func.count()).select_from(sa.table("tick_failures"))
            )
            or 0
        )


# --- round-trip ------------------------------------------------------------


def test_record_round_trips_model_to_database(db: Database) -> None:
    repo = TickFailureRepo(db)
    item = a_failure()
    assert repo.record(item) == item
    assert repo.get(item.id) == item


# --- append-only surface (mirroring DebtItemRepo/EvidenceRepo) -------------


def test_repo_exposes_append_and_query_only() -> None:
    # No update, no delete, no finalize, no set_*: append-only is structural,
    # by surface absence. record + recorded_since are the only domain verbs.
    assert public_methods(TickFailureRepo) == {
        "add",
        "get",
        "list",
        "record",
        "recorded_since",
    }


def test_no_mutator_methods_exist() -> None:
    surface = public_methods(TickFailureRepo)
    for forbidden in ("update", "delete", "remove", "finalize", "set_status"):
        assert forbidden not in surface, forbidden


def test_updating_a_persisted_row_raises(db: Database) -> None:
    # The repo offers no update path; the only way to "update" a record is to
    # re-record its id, which the primary key rejects. A persisted row is
    # immutable.
    repo = TickFailureRepo(db)
    item = a_failure()
    repo.record(item)
    with pytest.raises(sa.exc.IntegrityError):
        repo.record(a_failure(id=item.id, detail="rewritten"))


def test_deleting_a_persisted_row_has_no_repository_path() -> None:
    assert "delete" not in public_methods(TickFailureRepo)


# --- recorded_since dedup predicate (inclusive boundary, per-signature) -----


def test_recorded_since_is_inclusive_and_scoped_per_signature(db: Database) -> None:
    repo = TickFailureRepo(db)
    sig = "LinearTimeoutError@sync_tick.pull"
    other_sig = "ValueError@sync_tick.promote"
    window = datetime(2026, 6, 23, tzinfo=UTC)

    # No rows yet: nothing recorded since the window opened.
    assert repo.recorded_since(sig, window) is False

    # A row exactly AT the boundary counts (the boundary is inclusive: a crash
    # recorded at the window instant is "already recorded this window").
    repo.record(a_failure(failure_signature=sig, occurred_at=window))
    assert repo.recorded_since(sig, window) is True

    # The window advances past the prior row: a fresh window reports nothing
    # recorded yet. Wrong answer: "any crash of this signature ever".
    advanced = window + timedelta(hours=1)
    assert repo.recorded_since(sig, advanced) is False

    # Scoped per signature: a different signature never satisfies, even within
    # the same window.
    assert repo.recorded_since(other_sig, window) is False


def test_recorded_since_writes_nothing(db: Database) -> None:
    # The proof that recorded_since is a pure query-time predicate: the row
    # count is unchanged across a True call and a False call.
    repo = TickFailureRepo(db)
    sig = "LinearTimeoutError@sync_tick.pull"
    window = datetime(2026, 6, 23, tzinfo=UTC)
    repo.record(a_failure(failure_signature=sig, occurred_at=window))

    before = _row_count(db)
    assert repo.recorded_since(sig, window) is True  # a matching call
    assert repo.recorded_since("never-seen", window) is False  # a non-matching call
    assert _row_count(db) == before == 1


def test_recorded_since_rejects_naive_boundary(db: Database) -> None:
    with pytest.raises(NaiveDatetimeError, match="occurred_at"):
        TickFailureRepo(db).recorded_since("sig", datetime(2026, 6, 23, 10, 0, 0))


def test_recorded_since_is_false_for_unseen_signature(db: Database) -> None:
    repo = TickFailureRepo(db)
    assert repo.recorded_since("never-seen", datetime(2026, 6, 23, tzinfo=UTC)) is False


# --- no trust-tier cap -----------------------------------------------------


def test_no_trust_tier_cap_on_record(db: Database) -> None:
    # A TickFailure is NOT evidence: there is no status to cap and no
    # TrustTierError path. record has exactly one parameter — no bypass,
    # because there is nothing to bypass.
    import inspect

    parameters = inspect.signature(TickFailureRepo.record).parameters
    assert list(parameters) == ["self", "model"]


# --- datetime contract (inherited boundary) --------------------------------


def test_naive_occurred_at_rejected_at_record(db: Database) -> None:
    item = a_failure(occurred_at=datetime(2026, 6, 23, 10, 0, 0))
    with pytest.raises(NaiveDatetimeError, match="occurred_at"):
        TickFailureRepo(db).record(item)


def test_aware_offset_normalised_to_utc(db: Database) -> None:
    plus_two = timezone(timedelta(hours=2))
    item = a_failure(occurred_at=datetime(2026, 6, 23, 12, 0, 0, tzinfo=plus_two))
    repo = TickFailureRepo(db)
    repo.record(item)
    stored = repo.get(item.id)
    assert stored is not None
    assert stored.occurred_at.utcoffset() == timedelta(0)
    assert stored.occurred_at == item.occurred_at  # identity by instant

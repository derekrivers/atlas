"""ATLAS-25: KeyCounterRepo — the stateful half (gaps 1 and 3).

Transactional reserve on a caller-supplied session, structural no-reuse
across a simulated archive (AT-6 shadow), default-empty behaviour, prefix
separation, agreement with the pure assignment function, and the
append-only surface asserted by introspection (no setter, no decrement).
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from atlas.planning.key_authority import assign_keys
from atlas.planning.reconciler import ADD, DiffEntry, PlanDiff
from atlas.storage import Database, KeyCounterError, KeyCounterRepo


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


# --- transactional reserve (gap 3) ------------------------------------------


def test_reserve_advances_and_commits_with_caller(db: Database) -> None:
    repo = KeyCounterRepo(db)
    with db.session() as session, session.begin():
        reservation = repo.reserve(session, "ATLAS", 3)
    assert (reservation.first, reservation.last, reservation.high_water) == (1, 3, 3)
    assert repo.high_water_marks() == {"ATLAS": 3}


def test_reserve_rolls_back_with_the_caller_transaction(db: Database) -> None:
    # reserve opens no transaction of its own: rolling back the caller's
    # transaction undoes both reservations, so it composes atomically with
    # ATLAS-27's render writes and PlanRun finalise.
    repo = KeyCounterRepo(db)
    session = db.session()
    transaction = session.begin()
    repo.reserve(session, "ATLAS", 3)
    repo.reserve(session, "ATLAS-E", 2)
    transaction.rollback()
    session.close()
    assert repo.high_water_marks() == {}


# --- default-empty (gap 1) --------------------------------------------------


def test_unseen_prefix_starts_at_one(db: Database) -> None:
    repo = KeyCounterRepo(db)
    assert repo.high_water_marks() == {}  # no rows until first reserve
    with db.session() as session, session.begin():
        reservation = repo.reserve(session, "ATLAS", 1)
    assert reservation.first == 1


# --- no-reuse across a simulated archive (AT-6 shadow) ----------------------


def test_counter_is_monotonic_across_archives(db: Database) -> None:
    repo = KeyCounterRepo(db)
    with db.session() as session, session.begin():
        first = repo.reserve(session, "ATLAS", 5)
    assert (first.first, first.last) == (1, 5)
    # Simulate archiving ATLAS-2 and ATLAS-3: the counter has no link to
    # backlog membership, so archival cannot lower it — there is nothing to
    # delete. The next reservation continues from 6 and never reissues 2/3.
    with db.session() as session, session.begin():
        second = repo.reserve(session, "ATLAS", 2)
    assert (second.first, second.last) == (6, 7)
    assert repo.high_water_marks() == {"ATLAS": 7}


# --- prefix separation ------------------------------------------------------


def test_prefixes_have_independent_counters(db: Database) -> None:
    repo = KeyCounterRepo(db)
    with db.session() as session, session.begin():
        repo.reserve(session, "ATLAS", 3)
        repo.reserve(session, "ATLAS-E", 1)
        repo.reserve(session, "ATLAS", 2)
    assert repo.high_water_marks() == {"ATLAS": 5, "ATLAS-E": 1}


# --- agreement with the pure function ---------------------------------------


def test_reserve_matches_pure_assignment(db: Database) -> None:
    repo = KeyCounterRepo(db)
    marks = repo.high_water_marks()
    diff = PlanDiff(
        entries=(
            DiffEntry(entry_type=ADD, kind="ticket", identity="new:0", title="a"),
            DiffEntry(entry_type=ADD, kind="ticket", identity="new:1", title="b"),
            DiffEntry(entry_type=ADD, kind="ticket", identity="new:2", title="c"),
        )
    )
    assignment = assign_keys(
        diff, ticket_high_water=marks.get("ATLAS", 0), epic_high_water=0
    )
    with db.session() as session, session.begin():
        reservation = repo.reserve(session, "ATLAS", 3)
    # Both derive the numbers from (high_water, count): the assigned range
    # and the persisted mark agree by construction.
    assert (reservation.first, reservation.last) == (1, 3)
    assert reservation.high_water == assignment.ticket_high_water


# --- negative: non-positive count -------------------------------------------


@pytest.mark.parametrize("count", [0, -1])
def test_non_positive_count_rejected(db: Database, count: int) -> None:
    repo = KeyCounterRepo(db)
    session = db.session()
    session.begin()
    with pytest.raises(KeyCounterError):
        repo.reserve(session, "ATLAS", count)
    session.rollback()
    session.close()
    assert repo.high_water_marks() == {}


# --- append-only surface (no out-of-band mutator) ---------------------------


def test_surface_is_read_and_reserve_only() -> None:
    assert public_methods(KeyCounterRepo) == {"high_water_marks", "reserve"}


def test_reserve_takes_a_caller_supplied_session() -> None:
    parameters = list(inspect.signature(KeyCounterRepo.reserve).parameters)
    assert parameters == ["self", "session", "prefix", "count"]

"""ATLAS-71: VerificationCheckRepo enforcement — model<->DB round-trip
(including evidence_ids JSON survival), the append-only surface (no mutation
by absence; a re-recorded id raises), and that a VerificationCheck is NOT
subject to the evidence trust-tier cap (no bypass, because there is nothing
to bypass).

Falsifiable by introspection where the docs demand absence (no mutating
methods, no trust-tier path) and by a named wrong answer where round-trip
fidelity matters (evidence_ids must survive as UUIDs, not be dropped).
"""

import inspect
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa
from test_verification_check_model import check_kwargs

from atlas.core.models import VerificationCheck
from atlas.storage import Database, NaiveDatetimeError, VerificationCheckRepo


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


def a_check(**overrides: Any) -> VerificationCheck:
    return VerificationCheck(**check_kwargs() | {"id": uuid4()} | overrides)


# --- round-trip ------------------------------------------------------------


def test_add_round_trips_model_to_database(db: Database) -> None:
    repo = VerificationCheckRepo(db)
    check = a_check()
    assert repo.add(check) == check
    assert repo.get(check.id) == check


def test_evidence_ids_json_survives_round_trip(db: Database) -> None:
    # The named wrong answer: a dropped or string-coerced evidence_ids fails
    # here. The list of UUIDs must round-trip as UUIDs, in order.
    repo = VerificationCheckRepo(db)
    ids = [uuid4(), uuid4(), uuid4()]
    check = a_check(evidence_ids=ids, completed_at=datetime(2026, 6, 28, tzinfo=UTC))
    repo.add(check)
    stored = repo.get(check.id)
    assert stored is not None
    assert stored.evidence_ids == ids
    assert all(isinstance(e, type(ids[0])) for e in stored.evidence_ids)
    assert stored.completed_at == check.completed_at


def test_list_returns_added_checks(db: Database) -> None:
    repo = VerificationCheckRepo(db)
    one, two = a_check(), a_check()
    repo.add(one)
    repo.add(two)
    assert {c.id for c in repo.list()} == {one.id, two.id}


# --- append-only surface (mirroring Evidence/DebtItem) ---------------------


def test_repo_exposes_add_and_queries_only() -> None:
    # No update, no delete, no finalize, no set_*: append-only is structural,
    # by surface absence, exactly as EvidenceRepo.
    assert public_methods(VerificationCheckRepo) == {"add", "get", "list"}


def test_no_mutator_methods_exist() -> None:
    # Names the wrong answer directly: any mutation verb appearing on the repo
    # is a defect.
    surface = public_methods(VerificationCheckRepo)
    for forbidden in ("update", "delete", "remove", "finalize", "set_status"):
        assert forbidden not in surface, forbidden


def test_updating_a_persisted_row_raises(db: Database) -> None:
    # The repo offers no update path; the only way to "update" is to re-add the
    # id, which the primary key rejects. A persisted check is immutable.
    repo = VerificationCheckRepo(db)
    check = a_check()
    repo.add(check)
    with pytest.raises(sa.exc.IntegrityError):
        repo.add(a_check(id=check.id, summary="rewritten"))


# --- not evidence: no trust-tier cap (ADR-0008) ----------------------------


def test_no_trust_tier_cap_on_add(db: Database) -> None:
    # A VerificationCheck is NOT evidence: there is no PENDING cap and no
    # TrustTierError path. A PASSED check stores verbatim — contrast
    # EvidenceRepo, which would reject an agent-tier non-PENDING record.
    repo = VerificationCheckRepo(db)
    check = a_check(status="passed")
    repo.add(check)
    stored = repo.get(check.id)
    assert stored is not None
    assert stored.status.value == "passed"


def test_add_has_no_bypass_parameter() -> None:
    # add has exactly one parameter — no trust-tier bypass, because there is
    # nothing to bypass (no cap, no commit-pin guard).
    parameters = inspect.signature(VerificationCheckRepo.add).parameters
    assert list(parameters) == ["self", "model"]


# --- datetime contract (inherited boundary) --------------------------------


def test_naive_created_at_rejected_at_add(db: Database) -> None:
    check = a_check(created_at=datetime(2026, 6, 28, 10, 0, 0))
    with pytest.raises(NaiveDatetimeError, match="created_at"):
        VerificationCheckRepo(db).add(check)


def test_aware_offset_normalised_to_utc(db: Database) -> None:
    plus_two = timezone(timedelta(hours=2))
    check = a_check(created_at=datetime(2026, 6, 28, 12, 0, 0, tzinfo=plus_two))
    repo = VerificationCheckRepo(db)
    repo.add(check)
    stored = repo.get(check.id)
    assert stored is not None
    assert stored.created_at.utcoffset() == timedelta(0)
    assert stored.created_at == check.created_at  # identity by instant


def test_created_by_attribution_absent_by_design() -> None:
    # A VerificationCheck carries no created_by_* fields (it is not an attributed
    # operational record like DebtItem) — no attribution column to round-trip.
    assert "created_by_type" not in VerificationCheck.model_fields
    assert "created_by_id" not in VerificationCheck.model_fields

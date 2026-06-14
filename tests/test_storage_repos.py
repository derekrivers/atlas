"""ATLAS-18: repository enforcement — append-only surfaces, the
trust-tier cap, finalise-once, and the datetime contract. Falsifiable by
introspection where the docs demand absence (no mutating methods, no
bypass parameter)."""

import inspect
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from test_evidence_model import evidence_kwargs
from test_plan_run_model import plan_run_kwargs

from atlas.core.models import Evidence, PlanRun, PlanRunStatus
from atlas.storage import (
    Database,
    EvidenceRepo,
    NaiveDatetimeError,
    PlanRunRepo,
    PlanRunStateError,
    TrustTierError,
)


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


# --- append-only surfaces (knowledge-core, falsifiable) -------------------


def test_evidence_repo_exposes_add_and_queries_only() -> None:
    assert public_methods(EvidenceRepo) == {"add", "get", "list"}


def test_plan_run_repo_exposes_documented_surface_only() -> None:
    assert public_methods(PlanRunRepo) == {
        "add",
        "get",
        "list",
        "finalize",
        "latest_proposed",
        "latest_applied",
    }


def test_second_add_for_same_logical_event_creates_new_row(db: Database) -> None:
    repo = EvidenceRepo(db)
    base = evidence_kwargs() | {"commit_sha": "abc123", "external_run_id": "run-1"}
    repo.add(Evidence(**base | {"id": uuid4()}))
    repo.add(Evidence(**base | {"id": uuid4()}))
    assert len(repo.list()) == 2


# --- trust-tier cap (ADR-0008) ---------------------------------------------


@pytest.mark.parametrize("status", ["passed", "failed", "warning"])
def test_agent_tier_above_pending_rejected(db: Database, status: str) -> None:
    repo = EvidenceRepo(db)
    record = Evidence(
        **evidence_kwargs() | {"created_by_type": "agent", "status": status}
    )
    with pytest.raises(TrustTierError):
        repo.add(record)
    assert repo.list() == []


def test_agent_tier_pending_stored(db: Database) -> None:
    repo = EvidenceRepo(db)
    record = Evidence(
        **evidence_kwargs() | {"created_by_type": "agent", "status": "pending"}
    )
    assert repo.add(record) == record
    assert repo.get(record.id) == record


@pytest.mark.parametrize("actor", ["system", "human"])
@pytest.mark.parametrize("status", ["passed", "failed", "warning", "pending"])
def test_system_and_human_tier_any_status(
    db: Database, actor: str, status: str
) -> None:
    repo = EvidenceRepo(db)
    record = Evidence(
        **evidence_kwargs() | {"created_by_type": actor, "status": status}
    )
    assert repo.add(record) == record


def test_add_has_no_bypass_parameter() -> None:
    parameters = inspect.signature(EvidenceRepo.add).parameters
    assert list(parameters) == ["self", "model"]


# --- finalise-once (ADR-0007, knowledge-core) -------------------------------


def proposed(repo: PlanRunRepo) -> PlanRun:
    run = PlanRun(**plan_run_kwargs() | {"id": uuid4()})
    repo.add(run)
    return run


def test_finalize_applies_and_writes_only_permitted_fields(db: Database) -> None:
    repo = PlanRunRepo(db)
    run = proposed(repo)
    applied_at = datetime(2026, 6, 12, 12, tzinfo=UTC)
    final = repo.finalize(
        run.id,
        PlanRunStatus.APPLIED,
        approved_by="operator",
        applied_at=applied_at,
    )
    assert final.status is PlanRunStatus.APPLIED
    assert final.approved_by == "operator"
    assert final.applied_at == applied_at
    assert final.failure_reason is None
    # Field-by-field: everything else is untouched.
    untouched = set(PlanRun.model_fields) - {
        "status",
        "approved_by",
        "applied_at",
        "failure_reason",
    }
    for name in untouched:
        assert getattr(final, name) == getattr(run, name), name


@pytest.mark.parametrize("status", [PlanRunStatus.REJECTED, PlanRunStatus.FAILED])
def test_finalize_rejected_and_failed_legs(db: Database, status: PlanRunStatus) -> None:
    repo = PlanRunRepo(db)
    run = proposed(repo)
    final = repo.finalize(run.id, status, failure_reason="gate 2: cycle")
    assert final.status is status
    assert final.failure_reason == "gate 2: cycle"


def test_finalize_rejects_non_finalising_status(db: Database) -> None:
    repo = PlanRunRepo(db)
    run = proposed(repo)
    with pytest.raises(PlanRunStateError, match="proposed"):
        repo.finalize(run.id, PlanRunStatus.PROPOSED)


def test_second_finalize_fails(db: Database) -> None:
    repo = PlanRunRepo(db)
    run = proposed(repo)
    repo.finalize(run.id, PlanRunStatus.REJECTED)
    with pytest.raises(PlanRunStateError, match="exactly once"):
        repo.finalize(run.id, PlanRunStatus.APPLIED, approved_by="operator")


@pytest.mark.parametrize(
    "status", [PlanRunStatus.APPLIED, PlanRunStatus.REJECTED, PlanRunStatus.FAILED]
)
def test_finalize_rejects_rows_not_in_proposed(
    db: Database, status: PlanRunStatus
) -> None:
    repo = PlanRunRepo(db)
    row = PlanRun(**plan_run_kwargs() | {"id": uuid4(), "status": status})
    repo.add(row)
    with pytest.raises(PlanRunStateError, match="exactly once"):
        repo.finalize(row.id, PlanRunStatus.APPLIED)


def test_finalize_missing_row_fails(db: Database) -> None:
    with pytest.raises(PlanRunStateError, match="no PlanRun"):
        PlanRunRepo(db).finalize(uuid4(), PlanRunStatus.APPLIED)


def test_latest_proposed_returns_most_recent(db: Database) -> None:
    repo = PlanRunRepo(db)
    older = PlanRun(
        **plan_run_kwargs()
        | {"id": uuid4(), "created_at": datetime(2026, 6, 11, tzinfo=UTC)}
    )
    newer = PlanRun(
        **plan_run_kwargs()
        | {"id": uuid4(), "created_at": datetime(2026, 6, 12, tzinfo=UTC)}
    )
    repo.add(older)
    repo.add(newer)
    repo.finalize(newer.id, PlanRunStatus.REJECTED)
    latest = repo.latest_proposed()
    assert latest is not None
    assert latest.id == older.id


def test_latest_applied_returns_most_recent_applied(db: Database) -> None:
    repo = PlanRunRepo(db)
    assert repo.latest_applied() is None  # none applied yet
    older = PlanRun(**plan_run_kwargs() | {"id": uuid4()})
    newer = PlanRun(**plan_run_kwargs() | {"id": uuid4()})
    repo.add(older)
    repo.add(newer)
    repo.finalize(
        older.id,
        PlanRunStatus.APPLIED,
        approved_by="operator",
        applied_at=datetime(2026, 6, 12, tzinfo=UTC),
    )
    repo.finalize(
        newer.id,
        PlanRunStatus.APPLIED,
        approved_by="operator",
        applied_at=datetime(2026, 6, 13, tzinfo=UTC),
    )
    latest = repo.latest_applied()
    assert latest is not None
    assert latest.id == newer.id  # ordered by applied_at
    # Provenance reads back through the retrieval surface.
    assert latest.status is PlanRunStatus.APPLIED
    assert latest.input_doc_shas == older.input_doc_shas  # same kwargs payload


# --- datetime contract ------------------------------------------------------


def test_naive_datetime_rejected_at_add(db: Database) -> None:
    record = Evidence(
        **evidence_kwargs() | {"created_at": datetime(2026, 6, 12, 10, 0, 0)}
    )
    with pytest.raises(NaiveDatetimeError, match="created_at"):
        EvidenceRepo(db).add(record)


def test_naive_applied_at_rejected_at_finalize(db: Database) -> None:
    repo = PlanRunRepo(db)
    run = proposed(repo)
    with pytest.raises(NaiveDatetimeError, match="applied_at"):
        repo.finalize(
            run.id,
            PlanRunStatus.APPLIED,
            applied_at=datetime(2026, 6, 12, 12, 0, 0),
        )


def test_aware_offset_normalised_to_utc_same_instant(db: Database) -> None:
    plus_two = timezone(timedelta(hours=2))
    record = Evidence(
        **evidence_kwargs()
        | {"created_at": datetime(2026, 6, 12, 12, 0, 0, tzinfo=plus_two)}
    )
    repo = EvidenceRepo(db)
    repo.add(record)
    stored = repo.get(record.id)
    assert stored is not None
    assert stored.created_at.tzinfo is not None
    assert stored.created_at.utcoffset() == timedelta(0)  # returned as UTC
    assert stored.created_at == record.created_at  # identity by instant

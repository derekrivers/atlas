"""ATLAS-63: normalised CI -> Evidence mapping.

Falsifiable coverage of the acceptance criteria: the job-name -> EvidenceType
contract (seeded with ONLY the test prefix today), the pure check -> Evidence
mapper (status verbatim, the commit-pin triple carried, no DB touched), and the
thin ingest path through the ATLAS-61 system-tier pinning guard.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from atlas.core.enums import ActorType, EvidenceStatus
from atlas.core.models.evidence import EvidenceType
from atlas.evidence import (
    GITHUB_ACTIONS_ACTOR_ID,
    evidence_type_for_job,
    ingest_checks,
    map_check_to_evidence,
)
from atlas.github import NormalisedCheck
from atlas.storage import Database, EvidenceRepo

NOW = datetime(2026, 6, 26, tzinfo=UTC)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def _check(
    name: str,
    *,
    status: EvidenceStatus = EvidenceStatus.PASSED,
    raw_payload: dict[str, Any] | None = None,
) -> NormalisedCheck:
    """A frozen NormalisedCheck with a full commit-pin triple — the shape
    ATLAS-62 hands the mapper."""
    return NormalisedCheck(
        name=name,
        status=status,
        external_run_id="run-42",
        commit_sha="a" * 40,
        payload_hash="sha256:" + "0" * 64,
        source_uri="https://github.com/acme/atlas/runs/42",
        raw_payload=raw_payload if raw_payload is not None else {"id": 42},
    )


# --- criterion 1: test-prefixed check -> system-tier TEST_RESULT, round-trips --


def test_test_check_maps_to_system_tier_test_result_and_round_trips(
    db: Database,
) -> None:
    # status FAILED, but a payload conclusion of "success": a mapper that
    # re-derived status from the payload would wrongly produce PASSED. The
    # record must take check.status VERBATIM.
    check = _check(
        "test (3.12)",
        status=EvidenceStatus.FAILED,
        raw_payload={"id": 42, "conclusion": "success"},
    )
    evidence = map_check_to_evidence(check, product_id=uuid4(), now=NOW)

    assert evidence is not None
    assert evidence.evidence_type is EvidenceType.TEST_RESULT
    assert evidence.created_by_type is ActorType.SYSTEM
    assert evidence.created_by_id == GITHUB_ACTIONS_ACTOR_ID
    # status taken verbatim (FAILED), not re-derived from the payload
    assert evidence.status is EvidenceStatus.FAILED
    # the commit-pin triple is carried straight from the check
    assert evidence.commit_sha == check.commit_sha
    assert evidence.external_run_id == check.external_run_id
    assert evidence.payload_hash == check.payload_hash
    assert evidence.created_at == NOW

    # round-trips through the ATLAS-61 system-tier pinning guard and back
    repo = EvidenceRepo(db)
    assert repo.add(evidence) == evidence
    assert repo.get(evidence.id) == evidence


# --- criterion 2: a non-test check maps to None and persists nothing ----------


def test_non_test_check_maps_to_none_and_persists_nothing(db: Database) -> None:
    repo = EvidenceRepo(db)
    persisted = ingest_checks(
        [_check("lint / ruff")], repo=repo, product_id=uuid4(), now=NOW
    )
    assert persisted == []
    assert repo.list() == []


def test_ingest_persists_only_recognised_checks(db: Database) -> None:
    repo = EvidenceRepo(db)
    persisted = ingest_checks(
        [_check("test"), _check("build / wheel"), _check("Test Suite")],
        repo=repo,
        product_id=uuid4(),
        now=NOW,
    )
    # both test rows ingested; the build row skipped (ATLAS-64, not today)
    assert {e.evidence_type for e in persisted} == {EvidenceType.TEST_RESULT}
    assert len(repo.list()) == 2


# --- criterion 3: the job-name contract (seeded defect lives here) ------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("test", EvidenceType.TEST_RESULT),
        ("test (3.12)", EvidenceType.TEST_RESULT),
        ("Test Suite", EvidenceType.TEST_RESULT),
        ("build", None),
        ("lint", None),
        ("coverage", None),
        ("deploy", None),
    ],
)
def test_evidence_type_for_job(name: str, expected: EvidenceType | None) -> None:
    assert evidence_type_for_job(name) == expected


# --- criterion 4: the mapper is pure (no Database touched) --------------------


def test_map_check_to_evidence_is_pure() -> None:
    # No Database/repo parameter: persistence is the ingest function's job.
    params = set(inspect.signature(map_check_to_evidence).parameters)
    assert "db" not in params
    assert "repo" not in params
    # And it runs to completion with no storage in sight.
    evidence = map_check_to_evidence(_check("test"), product_id=uuid4(), now=NOW)
    assert evidence is not None

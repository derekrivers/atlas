"""ATLAS-63/64: normalised CI -> Evidence mapping.

Falsifiable coverage of the acceptance criteria: the job-name -> EvidenceType
contract (test/lint/build/coverage prefixes; unrecognised -> None), the total
check -> Evidence mapper (status verbatim, the commit-pin triple carried, no DB
touched, unrecognised jobs fall back to BUILD_RESULT with a warning), and the
thin ingest path through the ATLAS-61 system-tier pinning guard.
"""

from __future__ import annotations

import inspect
import logging
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


# --- criterion 2: unrecognised job falls back to BUILD_RESULT, persists -------


def test_unrecognised_check_falls_back_to_build_result_and_persists(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    # "deploy / staging" has no recognised prefix: the mapper must NOT drop it
    # (the old ATLAS-63 behaviour) but fall back to BUILD_RESULT with a warning.
    repo = EvidenceRepo(db)
    with caplog.at_level(logging.WARNING, logger="atlas.evidence.mapping"):
        persisted = ingest_checks(
            [_check("deploy / staging")], repo=repo, product_id=uuid4(), now=NOW
        )
    assert {e.evidence_type for e in persisted} == {EvidenceType.BUILD_RESULT}
    assert len(repo.list()) == 1
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "deploy / staging" in warnings[0].getMessage()


def test_ingest_persists_every_check(db: Database) -> None:
    repo = EvidenceRepo(db)
    persisted = ingest_checks(
        [_check("test"), _check("build / wheel"), _check("Test Suite")],
        repo=repo,
        product_id=uuid4(),
        now=NOW,
    )
    # nothing is dropped now: both test rows -> TEST_RESULT, the build row ->
    # BUILD_RESULT via the table (ATLAS-64).
    assert {e.evidence_type for e in persisted} == {
        EvidenceType.TEST_RESULT,
        EvidenceType.BUILD_RESULT,
    }
    assert len(repo.list()) == 3


# --- criterion 3: the job-name contract (seeded defect lives here) ------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("test", EvidenceType.TEST_RESULT),
        ("test (3.12)", EvidenceType.TEST_RESULT),
        ("Test Suite", EvidenceType.TEST_RESULT),
        # ATLAS-64 rows, including matrix/suffixed forms.
        ("lint", EvidenceType.LINT_RESULT),
        ("lint (mypy)", EvidenceType.LINT_RESULT),
        ("build", EvidenceType.BUILD_RESULT),
        ("build / wheel", EvidenceType.BUILD_RESULT),
        ("coverage", EvidenceType.COVERAGE_REPORT),
        # the lookup stays honest about unrecognised prefixes (D2).
        ("deploy", None),
    ],
)
def test_evidence_type_for_job(name: str, expected: EvidenceType | None) -> None:
    assert evidence_type_for_job(name) == expected


# --- criterion 2 (mapper level): unrecognised -> BUILD_RESULT + one warning ----


def test_map_check_to_evidence_unrecognised_falls_back_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    check = _check("deploy / staging")
    with caplog.at_level(logging.WARNING, logger="atlas.evidence.mapping"):
        evidence = map_check_to_evidence(check, product_id=uuid4(), now=NOW)
    assert evidence.evidence_type is EvidenceType.BUILD_RESULT
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "deploy / staging" in warnings[0].getMessage()


# --- criterion 4: a recognised non-test check round-trips, NO warning ---------


def test_build_check_round_trips_as_system_tier_build_result(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    check = _check("build / wheel")
    with caplog.at_level(logging.WARNING, logger="atlas.evidence.mapping"):
        evidence = map_check_to_evidence(check, product_id=uuid4(), now=NOW)

    assert evidence.evidence_type is EvidenceType.BUILD_RESULT
    assert evidence.created_by_type is ActorType.SYSTEM
    assert evidence.created_by_id == GITHUB_ACTIONS_ACTOR_ID
    # the commit-pin triple is carried, so it satisfies the ATLAS-61 guard
    assert evidence.commit_sha == check.commit_sha
    assert evidence.external_run_id == check.external_run_id
    assert evidence.payload_hash == check.payload_hash
    # mapped via the table, NOT the fallback: the warning is the only
    # observable difference since both paths yield BUILD_RESULT.
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []

    # round-trips through the ATLAS-61 system-tier pinning guard and back
    repo = EvidenceRepo(db)
    assert repo.add(evidence) == evidence
    assert repo.get(evidence.id) == evidence


# --- criterion 4: the mapper is pure (no Database touched) --------------------


def test_map_check_to_evidence_is_pure() -> None:
    # No Database/repo parameter: persistence is the ingest function's job.
    params = set(inspect.signature(map_check_to_evidence).parameters)
    assert "db" not in params
    assert "repo" not in params
    # And it runs to completion with no storage in sight.
    evidence = map_check_to_evidence(_check("test"), product_id=uuid4(), now=NOW)
    assert evidence is not None

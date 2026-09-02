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
from test_evidence_model import evidence_kwargs

from atlas.core.enums import ActorType, EvidenceStatus
from atlas.core.models import Evidence
from atlas.core.models.evidence import EvidenceType
from atlas.evidence import (
    GITHUB_ACTIONS_ACTOR_ID,
    build_merge_evidence,
    evidence_type_for_job,
    ingest_checks,
    ingest_docs,
    ingest_reviews,
    map_check_to_evidence,
    map_docs_to_evidence,
    map_review_to_evidence,
)
from atlas.github import NormalisedCheck, NormalisedDocs, NormalisedReview
from atlas.github.normaliser import payload_hash
from atlas.storage import Database, EvidenceRepo, TrustTierError

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
    payload = raw_payload if raw_payload is not None else {"id": name}
    return NormalisedCheck(
        name=name,
        status=status,
        external_run_id=f"run-{name}",
        commit_sha="a" * 40,
        payload_hash=payload_hash(payload),
        source_uri="https://github.com/acme/atlas/runs/42",
        raw_payload=payload,
        source_event_at=NOW,
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
    assert evidence.job_name == check.name
    assert evidence.source_event_at == check.source_event_at
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


def test_ingest_skips_unchanged_check_payload(db: Database) -> None:
    repo = EvidenceRepo(db)
    check = _check("test")

    first = ingest_checks([check], repo=repo, product_id=uuid4(), now=NOW)
    second = ingest_checks([check], repo=repo, product_id=uuid4(), now=NOW)

    assert len(first) == 1
    assert second == []
    assert repo.count() == 1


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


# --- ATLAS-65: review -> system-tier PR_REVIEW Evidence -----------------------


def _review(
    *,
    reviewer: str = "octocat",
    status: EvidenceStatus = EvidenceStatus.PASSED,
) -> NormalisedReview:
    """A frozen NormalisedReview with a full commit-pin triple — the shape
    ATLAS-65's normaliser hands the mapper (always commit-pinned)."""
    raw_payload = {"id": reviewer, "user": {"login": reviewer}, "state": "APPROVED"}
    return NormalisedReview(
        reviewer=reviewer,
        status=status,
        external_run_id=f"review-{reviewer}",
        commit_sha="b" * 40,
        payload_hash=payload_hash(raw_payload),
        source_uri="https://github.com/acme/atlas/pull/1#pullrequestreview-99",
        raw_payload=raw_payload,
    )


def test_review_maps_to_system_tier_pr_review_and_round_trips(db: Database) -> None:
    # CHANGES_REQUESTED -> the review's status is FAILED; the mapper takes it
    # VERBATIM and never re-derives from raw_payload (whose state is "APPROVED").
    review = _review(reviewer="alice", status=EvidenceStatus.FAILED)
    evidence = map_review_to_evidence(review, product_id=uuid4(), now=NOW)

    assert evidence.evidence_type is EvidenceType.PR_REVIEW
    assert evidence.created_by_type is ActorType.SYSTEM
    # the ingesting actor is the poller, NOT the human reviewer (D4).
    assert evidence.created_by_id == GITHUB_ACTIONS_ACTOR_ID
    assert evidence.created_by_id != review.reviewer
    # status verbatim from the NormalisedReview
    assert evidence.status is EvidenceStatus.FAILED
    # the commit-pin triple is carried straight from the review
    assert evidence.commit_sha == review.commit_sha
    assert evidence.external_run_id == review.external_run_id
    assert evidence.payload_hash == review.payload_hash
    assert evidence.created_at == NOW
    # the reviewer lives in raw_payload and the summary, never in created_by_id
    assert evidence.raw_payload["user"]["login"] == "alice"
    assert "alice" in evidence.summary
    assert "failed" in evidence.summary

    # round-trips through the ATLAS-61 system-tier pinning guard and back
    repo = EvidenceRepo(db)
    assert repo.add(evidence) == evidence
    assert repo.get(evidence.id) == evidence


def test_map_review_to_evidence_is_pure() -> None:
    # No Database/repo parameter: persistence is ingest_reviews' job.
    params = set(inspect.signature(map_review_to_evidence).parameters)
    assert "db" not in params
    assert "repo" not in params


def test_ingest_reviews_persists_every_review(db: Database) -> None:
    repo = EvidenceRepo(db)
    persisted = ingest_reviews(
        [
            _review(reviewer="alice", status=EvidenceStatus.PASSED),
            _review(reviewer="bob", status=EvidenceStatus.WARNING),
        ],
        repo=repo,
        product_id=uuid4(),
        now=NOW,
    )
    assert {e.evidence_type for e in persisted} == {EvidenceType.PR_REVIEW}
    assert all(e.created_by_id == GITHUB_ACTIONS_ACTOR_ID for e in persisted)
    assert len(repo.list()) == 2


# --- ATLAS-66: docs -> system-tier DOCUMENTATION_UPDATE Evidence --------------


def _docs(
    *, paths: tuple[str, ...] = ("docs/guide.md", "docs/index.md")
) -> NormalisedDocs:
    """A frozen NormalisedDocs with a full synthesised pin triple — the shape
    ATLAS-66's normaliser hands the mapper (always PASSED, always pinned)."""
    return NormalisedDocs(
        status=EvidenceStatus.PASSED,
        docs_paths=paths,
        external_run_id="docs:v2:" + "c" * 40,
        commit_sha="c" * 40,
        payload_hash="sha256:" + "d" * 64,
        source_uri=None,
        raw_payload={"files": [{"filename": p} for p in paths]},
    )


def test_docs_maps_to_system_tier_documentation_update_and_round_trips(
    db: Database,
) -> None:
    docs = _docs()
    evidence = map_docs_to_evidence(docs, product_id=uuid4(), now=NOW)

    assert evidence.evidence_type is EvidenceType.DOCUMENTATION_UPDATE
    assert evidence.created_by_type is ActorType.SYSTEM
    assert evidence.created_by_id == GITHUB_ACTIONS_ACTOR_ID
    # status taken verbatim (always PASSED for a docs change)
    assert evidence.status is EvidenceStatus.PASSED
    # the synthesised commit-pin triple is carried straight from the docs record
    assert evidence.commit_sha == docs.commit_sha
    assert evidence.external_run_id == docs.external_run_id
    assert evidence.payload_hash == docs.payload_hash
    assert evidence.source_uri == docs.source_uri
    assert evidence.raw_payload == docs.raw_payload
    assert evidence.docs_paths == docs.docs_paths
    assert evidence.created_at == NOW
    # the summary names the path count
    assert "2 path(s)" in evidence.summary

    # round-trips through the ATLAS-61 system-tier pinning guard and back: proves
    # the synthesised docs:v2:<sha> pin satisfies the guard.
    repo = EvidenceRepo(db)
    assert repo.add(evidence) == evidence
    assert repo.get(evidence.id) == evidence


def test_map_docs_to_evidence_is_pure() -> None:
    # No Database/repo parameter: persistence is ingest_docs' job.
    params = set(inspect.signature(map_docs_to_evidence).parameters)
    assert "db" not in params
    assert "repo" not in params


def test_ingest_docs_persists_the_one_record(db: Database) -> None:
    repo = EvidenceRepo(db)
    persisted = ingest_docs(_docs(), repo=repo, product_id=uuid4(), now=NOW)
    assert {e.evidence_type for e in persisted} == {EvidenceType.DOCUMENTATION_UPDATE}
    assert len(persisted) == 1
    assert len(repo.list()) == 1


def test_docs_v2_appends_once_beside_legacy_identity_then_deduplicates(
    db: Database,
) -> None:
    repo = EvidenceRepo(db)
    docs = _docs(paths=("docs/atlas/evidence-pipeline.md",))
    legacy = Evidence(
        id=uuid4(),
        product_id=uuid4(),
        evidence_type=EvidenceType.DOCUMENTATION_UPDATE,
        status=EvidenceStatus.PASSED,
        summary="legacy capped docs observation",
        commit_sha=docs.commit_sha,
        external_run_id=f"docs:{docs.commit_sha}",
        payload_hash=docs.payload_hash,
        raw_payload={"_truncated": True},
        created_by_type=ActorType.SYSTEM,
        created_by_id=GITHUB_ACTIONS_ACTOR_ID,
        created_at=NOW,
    )
    repo.add(legacy)

    [fresh] = ingest_docs(docs, repo=repo, product_id=legacy.product_id, now=NOW)
    repeated = ingest_docs(docs, repo=repo, product_id=legacy.product_id, now=NOW)

    assert fresh.external_run_id == f"docs:v2:{docs.commit_sha}"
    assert fresh.docs_paths == docs.docs_paths
    assert repeated == []
    assert {record.id for record in repo.list()} == {legacy.id, fresh.id}
    assert repo.get(legacy.id) == legacy


def test_ingest_docs_none_persists_nothing(db: Database) -> None:
    # The absence-based guarantee (criterion 2): no docs change -> None ->
    # nothing persisted. The wrong answer this guards is manufacturing a record.
    repo = EvidenceRepo(db)
    persisted = ingest_docs(None, repo=repo, product_id=uuid4(), now=NOW)
    assert persisted == []
    assert repo.list() == []


# --- ATLAS-70 milestone anchor: tier rules end-to-end (ADR-0008) --------------


def test_atlas70_milestone_ci_pinned_system_agent_passed_capped(db: Database) -> None:
    """ATLAS-70 / ADR-0008 milestone, asserted end-to-end in one place. The tier
    rules already exist — the mappers stamp ingested CI as system-tier (ATLAS-63
    /64) and EvidenceRepo.add enforces the agent-PENDING cap and the system-tier
    commit-pin guard (ATLAS-61). This is a regression anchor, not new behaviour:
    no production code. It pins both halves of the milestone together so neither
    can silently regress without this failing."""
    repo = EvidenceRepo(db)
    product_id = uuid4()

    # (a) A CI check ingested through the REAL map + add path persists as a
    #     SYSTEM-tier, commit-pinned row — the pipeline pins it, the test does
    #     not hand-build it. It is in storage because it survived the pin guard.
    [persisted] = ingest_checks(
        [_check("test (3.12)")], repo=repo, product_id=product_id, now=NOW
    )
    assert persisted.created_by_type is ActorType.SYSTEM
    assert persisted.commit_sha is not None
    assert persisted.external_run_id is not None
    assert persisted.payload_hash is not None
    assert repo.get(persisted.id) == persisted

    # (b) An agent-authored PASSED record is REJECTED and NOT persisted: the cap
    #     is enforce-by-rejection, never coercion. PENDING is the agent ceiling.
    agent_passed = Evidence(
        **evidence_kwargs() | {"created_by_type": "agent", "status": "passed"}
    )
    with pytest.raises(TrustTierError):
        repo.add(agent_passed)
    assert agent_passed.id not in {e.id for e in repo.list()}

    # ...and the SAME agent record at PENDING is accepted and stored, so the
    #    anchor pins "PENDING is the ceiling", not merely "PASSED fails".
    agent_pending = Evidence(
        **evidence_kwargs() | {"created_by_type": "agent", "status": "pending"}
    )
    assert repo.add(agent_pending) == agent_pending
    assert agent_pending.id in {e.id for e in repo.list()}


# --- ATLAS-134: build_merge_evidence (the merge-record builder) --------------


def _merge(
    merged: object,
    *,
    head_commit: str = "f" * 40,
    extra: dict[str, Any] | None = None,
) -> Evidence | None:
    pull_request: dict[str, Any] = {"merged": merged}
    if merged is True:
        pull_request.update(
            {
                "number": 42,
                "state": "closed",
                "merge_commit_sha": "e" * 40,
                "head": {
                    "sha": head_commit,
                    "repo": {"full_name": "acme/atlas"},
                },
                "base": {
                    "ref": "main",
                    "repo": {"full_name": "acme/atlas"},
                },
            }
        )
    if extra is not None:
        pull_request.update(extra)
    return build_merge_evidence(
        pull_request,
        head_commit=head_commit,
        ticket_id=uuid4(),
        product_id=uuid4(),
        evidence_id=uuid4(),
        now=NOW,
    )


def test_build_merge_evidence_returns_none_for_unmerged_pr() -> None:
    """AC-3: an unmerged PR -> None (no record). Falsy/absent merged are both None."""
    assert _merge(False) is None
    # wrong answer: a record built for an unmerged PR
    assert (
        build_merge_evidence(
            {},  # no "merged" key at all
            head_commit="f" * 40,
            ticket_id=uuid4(),
            product_id=uuid4(),
            evidence_id=uuid4(),
            now=NOW,
        )
        is None
    )


def test_build_merge_evidence_shapes_a_system_tier_record_for_a_merged_pr() -> None:
    """AC-3: a merged PR -> a system-tier PR_MERGED record pinned to C, PASSED,
    with the full pin triple EvidenceRepo.add demands."""
    head = "f" * 40
    record = _merge(True, head_commit=head)

    assert record is not None  # wrong answer: None for a merged PR
    assert record.evidence_type is EvidenceType.PR_MERGED
    assert record.status is EvidenceStatus.PASSED
    assert record.created_by_type is ActorType.SYSTEM
    assert record.created_by_id == GITHUB_ACTIONS_ACTOR_ID
    assert record.commit_sha == head
    # pin triple populated so the system-tier guard accepts it (ADR-0008).
    assert record.external_run_id is not None
    assert record.payload_hash is not None


def test_build_merge_evidence_is_persistable_through_the_system_tier_guard(
    db: Database,
) -> None:
    """AC-3 (round-trip into storage): the built record satisfies EvidenceRepo.add's
    system-tier commit-pin guard and reads back identically."""
    record = _merge(True)
    assert record is not None
    repo = EvidenceRepo(db)
    assert repo.add(record) == record
    assert repo.get(record.id) == record


def test_build_merge_evidence_never_raises_on_a_degenerate_dict() -> None:
    """AC-3: a degenerate pull_request dict never raises -- absence is data."""
    assert _merge(None) is None  # merged=None -> falsy -> None
    # Truthy guesses and incomplete provider identity fail closed.
    record = _merge("yes")
    assert record is None


def test_build_merge_evidence_is_pure_no_db_in_signature() -> None:
    """AC-3: the builder takes no Database -- it is a pure constructor (mirrors the
    sibling mappers)."""
    params = set(inspect.signature(build_merge_evidence).parameters)
    assert "db" not in params and "database" not in params

"""ATLAS-74: the documentation-check evaluator decides the ``documentation``
check against pre-loaded evidence, using system-tier DOCUMENTATION_UPDATE
evidence pinned to the head commit, covering a required path, and NEVER raising.

Each behavioural assertion names the wrong answer it would catch. The milestone
crux is guarded explicitly (AC4): an agent-tier doc record at the head commit
cannot manufacture a pass, AND the (d) reason must actually say the agent claim
is ignored — a status-only assertion would stay green if that message silently
vanished (the dead-guard trap). Coverage is exact normalised path equality, so
a record touching only non-required paths (AC2) or a record at a different
commit (AC3) is PENDING, never PASSED. The ``(created_at, id)`` tiebreak is
exercised both by time (AC8) and by a shared timestamp (AC8b) so the id
tiebreak is a real falsifiable guard, not dead code. Malformed and again-capped
payloads (AC7) contribute no coverage and never raise.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from atlas.core.enums import ActorType, EvidenceStatus
from atlas.core.models import Evidence, EvidenceType, VerificationCheckType
from atlas.verification import (
    DocumentationEvaluation,
    evaluate_documentation_check,
)

NOW = datetime(2026, 6, 28, tzinfo=UTC)
HEAD = "c0ffee0000000000000000000000000000000000"
OTHER = "dead000000000000000000000000000000000000"
REQUIRED = "docs/atlas/verification-engine.md"
OTHER_PATH = "docs/atlas/evidence-pipeline.md"
ES = EvidenceStatus
ET = EvidenceType


def make_docs_evidence(
    *,
    filenames: list[str] | None = None,
    raw_payload: dict[str, Any] | None = None,
    created_by_type: ActorType = ActorType.SYSTEM,
    commit_sha: str | None = HEAD,
    created_at: datetime = NOW,
    evidence_type: EvidenceType = ET.DOCUMENTATION_UPDATE,
    id: UUID | None = None,
) -> Evidence:
    """A DOCUMENTATION_UPDATE record shaped like the ATLAS-66 mapper's output.

    ``filenames`` builds the canonical ``{"files": [{"filename": ...}]}`` payload;
    ``raw_payload`` overrides it wholesale for the malformed/capped cases.
    """
    if raw_payload is None:
        files = [{"filename": name} for name in (filenames or [])]
        raw_payload = {"files": files}
    return Evidence(
        id=id or uuid4(),
        product_id=uuid4(),
        evidence_type=evidence_type,
        status=ES.PASSED,  # DOCUMENTATION_UPDATE is always PASSED-by-presence
        summary="docs updated",
        commit_sha=commit_sha,
        raw_payload=raw_payload,
        created_by_type=created_by_type,
        created_by_id="github-actions"
        if created_by_type == ActorType.SYSTEM
        else "claude",
        created_at=created_at,
    )


# --- AC1: system-tier doc record at C covering a required path -> PASSED + id.
def test_system_doc_covering_required_path_passes() -> None:
    record = make_docs_evidence(filenames=[REQUIRED, OTHER_PATH])
    result = evaluate_documentation_check(
        [REQUIRED], head_commit=HEAD, evidence=[record]
    )

    # wrong answer: PENDING — a covered required doc at C is not pending.
    assert result.status == ES.PASSED
    assert result.evidence_id == record.id  # wrong answer: None / a different record
    assert result.check_type == VerificationCheckType.DOCUMENTATION


# --- AC2: a record covering only NON-required paths -> PENDING.
def test_system_doc_covering_only_non_required_path_is_pending() -> None:
    record = make_docs_evidence(filenames=[OTHER_PATH])
    result = evaluate_documentation_check(
        [REQUIRED], head_commit=HEAD, evidence=[record]
    )

    # wrong answer: PASSED — the required doc was not touched, only an unrelated one.
    assert result.status == ES.PENDING
    assert result.evidence_id is None


# --- AC3: a covering record at a DIFFERENT commit -> PENDING (commit pin).
def test_covering_record_at_other_commit_is_pending() -> None:
    stale = make_docs_evidence(filenames=[REQUIRED], commit_sha=OTHER)
    result = evaluate_documentation_check(
        [REQUIRED], head_commit=HEAD, evidence=[stale]
    )

    # wrong answer: PASSED — a doc update at a stale commit leaked past the pin.
    assert result.status == ES.PENDING
    assert result.evidence_id is None
    # Not an agent record, so the reason must NOT claim agent evidence was ignored.
    assert "agent" not in result.reason.lower()


# --- AC4 (crux): agent-tier doc record at C covering required -> PENDING, and
# the reason MUST name the ignored agent claim (P1: assert the text, not status).
def test_agent_only_doc_at_head_is_pending_and_reason_names_agent() -> None:
    agent_doc = make_docs_evidence(
        filenames=[REQUIRED], created_by_type=ActorType.AGENT
    )
    result = evaluate_documentation_check(
        [REQUIRED], head_commit=HEAD, evidence=[agent_doc]
    )

    # wrong answer: PASSED — an agent forged a doc pass.
    assert result.status == ES.PENDING
    assert result.evidence_id is None
    # The (d) guard must bite: a status-only check would stay green if this
    # message silently vanished. Assert both the actor and the ignore language.
    reason = result.reason.lower()
    assert "agent" in reason
    assert "ignored" in reason


# --- AC5: empty requirements -> existence mode (D6).
def test_empty_requirements_passes_on_any_system_doc_at_head() -> None:
    # Any system-tier doc at C, even one touching no "required" path, passes.
    record = make_docs_evidence(filenames=[OTHER_PATH])
    result = evaluate_documentation_check([], head_commit=HEAD, evidence=[record])

    # wrong answer: PENDING — the matrix made the check required; a touched doc passes.
    assert result.status == ES.PASSED
    assert result.evidence_id == record.id


def test_empty_requirements_pending_when_no_doc_at_head() -> None:
    # A system doc, but at a different commit -> none at C -> PENDING (not pass).
    stale = make_docs_evidence(filenames=[OTHER_PATH], commit_sha=OTHER)
    result = evaluate_documentation_check([], head_commit=HEAD, evidence=[stale])

    # wrong answer: PASSED — "no doc touched at all" must not silently pass (D6).
    assert result.status == ES.PENDING
    assert result.evidence_id is None


# --- AC6: no doc record at all -> PENDING, never FAILED.
def test_no_doc_record_is_pending_never_failed() -> None:
    test_result = Evidence(
        id=uuid4(),
        product_id=uuid4(),
        evidence_type=ET.TEST_RESULT,  # not a doc record
        status=ES.PASSED,
        summary="ci",
        commit_sha=HEAD,
        created_by_type=ActorType.SYSTEM,
        created_by_id="github-actions",
        created_at=NOW,
    )
    result = evaluate_documentation_check(
        [REQUIRED], head_commit=HEAD, evidence=[test_result]
    )

    # wrong answer: FAILED — a missing doc is unproven, not failing.
    assert result.status == ES.PENDING
    assert result.evidence_id is None
    assert "agent" not in result.reason.lower()  # no agent doc -> no ignore note


# --- AC7: malformed / again-capped payloads contribute no coverage, never raise.
@pytest.mark.parametrize(
    "raw_payload",
    [
        {},  # missing "files" entirely
        {"files": "docs/atlas/verification-engine.md"},  # "files" not a list
        {  # a retention-cap marker payload (ATLAS-69): no "files" key
            "_truncated": True,
            "_original_bytes": 99999,
            "_payload_hash": "abc",
            "_source_uri": None,
        },
        {"files": ["docs/atlas/verification-engine.md"]},  # entry not a mapping
        {"files": [{"status": "modified"}]},  # entry lacks "filename"
        {"files": [{"filename": 123}]},  # filename not a string
    ],
)
def test_malformed_payload_contributes_no_coverage_and_is_pending(
    raw_payload: dict[str, Any],
) -> None:
    record = make_docs_evidence(raw_payload=raw_payload)
    result = evaluate_documentation_check(
        [REQUIRED], head_commit=HEAD, evidence=[record]
    )

    # wrong answer: PASSED (coverage read from a malformed payload) or a raise.
    assert result.status == ES.PENDING
    assert result.evidence_id is None


# --- AC8: latest by (created_at, id) among matching records decides.
def test_latest_matching_record_by_created_at_wins() -> None:
    older = make_docs_evidence(filenames=[REQUIRED], created_at=NOW)
    newer = make_docs_evidence(
        filenames=[REQUIRED], created_at=NOW + timedelta(minutes=5)
    )
    result = evaluate_documentation_check(
        [REQUIRED], head_commit=HEAD, evidence=[older, newer]
    )

    assert result.status == ES.PASSED
    # wrong answer: older.id — the stale record was picked.
    assert result.evidence_id == newer.id


# --- AC8b: id tiebreak when created_at ties (real guard, not dead code).
def test_id_tiebreaks_equal_created_at() -> None:
    low_id = UUID(int=1)
    high_id = UUID(int=2)
    cover_low = make_docs_evidence(filenames=[REQUIRED], created_at=NOW, id=low_id)
    cover_high = make_docs_evidence(filenames=[REQUIRED], created_at=NOW, id=high_id)
    # Pass in reverse so a no-op tiebreak (first-wins) would pick low_id.
    result = evaluate_documentation_check(
        [REQUIRED], head_commit=HEAD, evidence=[cover_high, cover_low]
    )

    # wrong answer: low_id — the (created_at, id) tiebreak is dead code.
    assert result.evidence_id == high_id


# --- AC10: never raises across empty evidence, empty requirements, malformed.
def test_empty_evidence_is_pending_not_raise() -> None:
    result = evaluate_documentation_check([REQUIRED], head_commit=HEAD, evidence=[])

    assert result.status == ES.PENDING  # wrong answer: an exception, or FAILED
    assert result.evidence_id is None


def test_empty_requirements_and_empty_evidence_is_pending_not_raise() -> None:
    result = evaluate_documentation_check([], head_commit=HEAD, evidence=[])

    assert result.status == ES.PENDING
    assert result.evidence_id is None


def test_blank_only_requirements_route_to_existence_mode() -> None:
    # Whitespace-only requirements normalise to empty -> existence mode, not a
    # spurious required path that could never match.
    record = make_docs_evidence(filenames=[OTHER_PATH])
    result = evaluate_documentation_check(
        ["   ", ""], head_commit=HEAD, evidence=[record]
    )

    assert result.status == ES.PASSED
    assert result.evidence_id == record.id


def test_required_path_matches_after_strip_normalisation() -> None:
    # The required entry carries surrounding whitespace; the covered filename is
    # clean. str/strip on both sides makes them equal.
    record = make_docs_evidence(filenames=[REQUIRED])
    result = evaluate_documentation_check(
        [f"  {REQUIRED}  "], head_commit=HEAD, evidence=[record]
    )

    assert result.status == ES.PASSED
    assert result.evidence_id == record.id


# --- Result shape and exported-surface guards.
def test_result_is_frozen_dataclass() -> None:
    result = evaluate_documentation_check([REQUIRED], head_commit=HEAD, evidence=[])
    assert isinstance(result, DocumentationEvaluation)
    assert result.check_type == VerificationCheckType.DOCUMENTATION
    with pytest.raises(Exception):  # noqa: B017 — FrozenInstanceError is the point
        result.status = ES.PASSED  # type: ignore[misc]

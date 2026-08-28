"""Pure system-tier evidence classification for the CI-pending handoff."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from atlas.core.enums import EvidenceStatus
from atlas.core.models import Evidence, EvidenceType, Ticket, VerificationCheckType
from atlas.core.models.ci_handoff_reconciliation import (
    CIHandoffCheckResult,
    CIHandoffClassification,
    CIHandoffReason,
)
from atlas.core.trust import evidence_tier
from atlas.verification.documentation_check import (
    documentation_path_authority,
    evaluate_documentation_check,
)
from atlas.verification.machine_checks import (
    MACHINE_CHECK_EVIDENCE,
    MACHINE_CHECK_TYPES,
    evaluate_machine_check,
)
from atlas.verification.rules import required_checks

_CI_EVIDENCE_CHECKS = MACHINE_CHECK_TYPES | frozenset(
    {VerificationCheckType.DOCUMENTATION}
)


@dataclass(frozen=True)
class CIHandoffAssessment:
    """One pure, bounded composition over the required system-tier checks."""

    classification: CIHandoffClassification
    reason: CIHandoffReason
    check_results: tuple[CIHandoffCheckResult, ...]


def _system_records(
    evidence: Iterable[Evidence], evidence_type: EvidenceType
) -> list[Evidence]:
    return [
        record
        for record in evidence
        if record.evidence_type is evidence_type
        and evidence_tier(record.created_by_type) == "system"
    ]


def _has_contradiction(records: Iterable[Evidence]) -> bool:
    """Detect tied current observations that disagree without inventing order."""

    statuses: dict[tuple[str | None, object], set[EvidenceStatus]] = {}
    for record in records:
        key = (record.job_name, record.source_event_at)
        statuses.setdefault(key, set()).add(record.status)
    return any(len(values) > 1 for values in statuses.values())


def _has_current_head_contradiction(
    evidence: tuple[Evidence, ...], *, head_commit: str
) -> bool:
    return any(
        _has_contradiction(
            record
            for record in evidence
            if record.evidence_type is evidence_type
            and record.commit_sha == head_commit
            and evidence_tier(record.created_by_type) == "system"
        )
        for evidence_type in MACHINE_CHECK_EVIDENCE.values()
    )


def _machine_result(
    check_type: VerificationCheckType,
    *,
    head_commit: str,
    evidence: tuple[Evidence, ...],
) -> CIHandoffCheckResult:
    expected = MACHINE_CHECK_EVIDENCE[check_type]
    all_system = _system_records(evidence, expected)
    at_head = [record for record in all_system if record.commit_sha == head_commit]
    evaluation = evaluate_machine_check(
        check_type, head_commit=head_commit, evidence=evidence
    )

    if evaluation.status is EvidenceStatus.PASSED:
        classification = CIHandoffClassification.PASSED
    elif not at_head:
        classification = (
            CIHandoffClassification.STALE
            if all_system
            else CIHandoffClassification.MISSING
        )
    elif not evaluation.evidence_ids:
        # Exact-head system evidence exists, but canonical per-job composition
        # could not order or identify it.  It is malformed, never merely absent.
        classification = CIHandoffClassification.MALFORMED
    else:
        deciding_by_id = {
            record.id: record
            for record in at_head
            if record.id in evaluation.evidence_ids
        }
        deciding = tuple(deciding_by_id.values())
        statuses = {record.status for record in deciding}
        if any(
            not record.job_name
            or record.external_run_id is None
            or record.source_event_at is None
            for record in deciding
        ):
            classification = CIHandoffClassification.MALFORMED
        elif _has_contradiction(deciding):
            classification = CIHandoffClassification.INDETERMINATE
        elif EvidenceStatus.WARNING in statuses:
            classification = CIHandoffClassification.INFRASTRUCTURE
        elif EvidenceStatus.NOT_APPLICABLE in statuses:
            classification = CIHandoffClassification.INDETERMINATE
        elif EvidenceStatus.PENDING in statuses:
            classification = CIHandoffClassification.PENDING
        elif EvidenceStatus.FAILED in statuses:
            conclusions = {
                record.raw_payload.get("conclusion")
                for record in deciding
                if record.status is EvidenceStatus.FAILED
            }
            if conclusions == {"failure"}:
                classification = CIHandoffClassification.IMPLEMENTATION_FAILURE
            elif conclusions and conclusions <= {"timed_out", "cancelled", "stale"}:
                classification = CIHandoffClassification.INFRASTRUCTURE
            else:
                # The normalised FAILED bit alone cannot prove code ownership.
                # Unknown/mixed provider causes are not model-classified.
                classification = CIHandoffClassification.INDETERMINATE
        else:
            classification = CIHandoffClassification.INDETERMINATE

    return CIHandoffCheckResult(
        check_type=check_type,
        status=evaluation.status,
        classification=classification,
        evidence_ids=evaluation.evidence_ids,
    )


def _documentation_result(
    ticket: Ticket,
    *,
    head_commit: str,
    evidence: tuple[Evidence, ...],
) -> CIHandoffCheckResult:
    all_system = _system_records(evidence, EvidenceType.DOCUMENTATION_UPDATE)
    at_head = [record for record in all_system if record.commit_sha == head_commit]
    evaluation = evaluate_documentation_check(
        ticket.documentation_requirements,
        head_commit=head_commit,
        evidence=evidence,
    )
    if evaluation.status is EvidenceStatus.PASSED:
        classification = CIHandoffClassification.PASSED
    elif not at_head:
        classification = (
            CIHandoffClassification.STALE
            if all_system
            else CIHandoffClassification.MISSING
        )
    elif documentation_path_authority(at_head).malformed:
        classification = CIHandoffClassification.MALFORMED
    else:
        classification = CIHandoffClassification.MISSING
    return CIHandoffCheckResult(
        check_type=VerificationCheckType.DOCUMENTATION,
        status=evaluation.status,
        classification=classification,
        evidence_ids=(
            () if evaluation.evidence_id is None else (evaluation.evidence_id,)
        ),
    )


def evaluate_ci_handoff(
    ticket: Ticket, *, head_commit: str, evidence: Iterable[Evidence]
) -> CIHandoffAssessment:
    """Classify the complete required system-tier set for one ticket and head.

    The repository-owned required-check resolver remains authoritative.  This
    handoff composes only checks whose canonical evaluators require system-tier
    evidence (tests, lint and documentation); acceptance, scope and human
    approval remain later human-review gates.  An empty system-tier subset is
    indeterminate rather than a vacuous pass.
    """

    materialised = tuple(
        record
        for record in evidence
        if record.product_id == ticket.product_id
        and (record.ticket_id is None or record.ticket_id == ticket.id)
    )
    check_types = tuple(
        required.check_type
        for required in required_checks(ticket)
        if required.required and required.check_type in _CI_EVIDENCE_CHECKS
    )
    if not check_types:
        return CIHandoffAssessment(
            classification=CIHandoffClassification.INDETERMINATE,
            reason=CIHandoffReason.NO_CI_REQUIRED_CHECKS,
            check_results=(),
        )

    results = tuple(
        _machine_result(
            check_type,
            head_commit=head_commit,
            evidence=materialised,
        )
        if check_type in MACHINE_CHECK_TYPES
        else _documentation_result(
            ticket,
            head_commit=head_commit,
            evidence=materialised,
        )
        for check_type in check_types
    )
    classes = {result.classification for result in results}
    if classes == {CIHandoffClassification.PASSED}:
        classification = CIHandoffClassification.PASSED
        reason = CIHandoffReason.COMPLETE_REQUIRED_CHECKS_PASSED
    elif (
        classes
        <= {
            CIHandoffClassification.PASSED,
            CIHandoffClassification.IMPLEMENTATION_FAILURE,
        }
        and CIHandoffClassification.IMPLEMENTATION_FAILURE in classes
    ):
        classification = CIHandoffClassification.IMPLEMENTATION_FAILURE
        reason = CIHandoffReason.COMPLETE_IMPLEMENTATION_FAILURE
    else:
        indeterminate_reason = (
            CIHandoffReason.CONTRADICTORY_EVIDENCE
            if _has_current_head_contradiction(materialised, head_commit=head_commit)
            else CIHandoffReason.INDETERMINATE_EVIDENCE
        )
        precedence = (
            (
                CIHandoffClassification.INDETERMINATE,
                indeterminate_reason,
            ),
            (CIHandoffClassification.MALFORMED, CIHandoffReason.MALFORMED_EVIDENCE),
            (
                CIHandoffClassification.INFRASTRUCTURE,
                CIHandoffReason.INFRASTRUCTURE_EVIDENCE,
            ),
            (CIHandoffClassification.STALE, CIHandoffReason.STALE_EVIDENCE),
            (CIHandoffClassification.PENDING, CIHandoffReason.REQUIRED_CHECKS_PENDING),
            (CIHandoffClassification.MISSING, CIHandoffReason.REQUIRED_CHECKS_MISSING),
        )
        classification, reason = next(
            (candidate, candidate_reason)
            for candidate, candidate_reason in precedence
            if candidate in classes
        )
    return CIHandoffAssessment(
        classification=classification,
        reason=reason,
        check_results=results,
    )

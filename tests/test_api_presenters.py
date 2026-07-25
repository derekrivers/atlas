"""Unit tests for pure HTTP response presenters."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from test_apply import _ticket_model_kwargs

from atlas.api.presenters import (
    present_dependency_critical_path,
    present_review_queue,
    present_system_status,
    present_ticket_board,
    present_ticket_dependencies,
    present_ticket_evidence,
)
from atlas.api.schemas import (
    CriticalPathStepSchema,
    DependencyBlockerSchema,
    DependencyCriticalPathResponse,
    NotReadyReasonSchema,
    ReviewCheckSchema,
    ReviewQueueItemSchema,
    ReviewQueueResponse,
    SystemStatusResponse,
    TicketBoardItemSchema,
    TicketBoardResponse,
    TicketDependenciesResponse,
    TicketEvidenceItemSchema,
    TicketEvidenceResponse,
    TicketReadinessSchema,
)
from atlas.core.enums import ActorType, EvidenceStatus, RiskLevel
from atlas.core.models import (
    EvidenceType,
    Ticket,
    TicketStatus,
    TicketType,
    VerificationCheckType,
)
from atlas.dependencies import (
    BlockedResult,
    BlockedTarget,
    CriticalPath,
    CriticalPathStep,
    NotReadyCode,
    NotReadyReason,
    ReadinessResult,
    UnlocksResult,
)
from atlas.orchestration import (
    ReviewCheckState,
    SystemStatus,
    TicketDependencyState,
    TicketEvidenceRecordState,
    TicketReviewState,
)


def test_presenters_do_not_serialise_enums_to_values() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "atlas" / "api" / "presenters.py"
    ).read_text(encoding="utf-8")

    assert ".value" not in source


def test_present_ticket_board_sorts_by_key_and_maps_tickets() -> None:
    product_id = uuid4()
    epic_id = uuid4()
    later = Ticket(
        **(
            _ticket_model_kwargs(product_id, epic_id, key="ATLAS-192")
            | {
                "id": uuid4(),
                "title": "Later ticket",
                "priority": 20,
                "risk_level": "high",
            }
        )
    )
    earlier = Ticket(
        **(
            _ticket_model_kwargs(product_id, epic_id, key="ATLAS-190")
            | {
                "id": uuid4(),
                "title": "Earlier ticket",
                "status": TicketStatus.IN_PROGRESS,
                "ticket_type": TicketType.BUG,
                "priority": 10,
                "risk_level": "medium",
            }
        )
    )

    response = present_ticket_board([later, earlier])

    assert response == TicketBoardResponse(
        tickets=[
            TicketBoardItemSchema(
                key="ATLAS-190",
                title="Earlier ticket",
                status=TicketStatus.IN_PROGRESS,
                ticket_type=TicketType.BUG,
                priority=10,
                risk_level=RiskLevel.MEDIUM,
            ),
            TicketBoardItemSchema(
                key="ATLAS-192",
                title="Later ticket",
                status=later.status,
                ticket_type=later.ticket_type,
                priority=20,
                risk_level=RiskLevel.HIGH,
            ),
        ]
    )


def test_present_ticket_evidence_maps_records_without_payload_fields() -> None:
    response = present_ticket_evidence(
        (
            TicketEvidenceRecordState(
                evidence_type=EvidenceType.TEST_RESULT,
                trust_level=ActorType.SYSTEM,
                status=EvidenceStatus.PASSED,
                has_system_pin_triple=True,
            ),
        )
    )

    assert response == TicketEvidenceResponse(
        evidence=[
            TicketEvidenceItemSchema(
                type=EvidenceType.TEST_RESULT,
                tier=ActorType.SYSTEM,
                status=EvidenceStatus.PASSED,
                has_system_pin_triple=True,
            )
        ]
    )
    assert set(response.evidence[0].model_dump(by_alias=True)) == {
        "type",
        "tier",
        "status",
        "has_system_pin_triple",
    }


def test_present_ticket_dependencies_maps_blockers_blocked_by_and_reasons() -> None:
    response = present_ticket_dependencies(
        TicketDependencyState(
            key="ATLAS-199",
            blockers=BlockedResult(
                "ATLAS-199",
                (BlockedTarget("ATLAS-200", NotReadyCode.DEPENDENCY_NOT_DONE),),
            ),
            blocked_by=UnlocksResult("ATLAS-199", ("ATLAS-201",)),
            readiness=ReadinessResult(
                "ATLAS-199",
                (
                    NotReadyReason(
                        code=NotReadyCode.DEPENDENCY_NOT_DONE,
                        message="depends_on ticket 'ATLAS-200' has status 'planned'",
                        target="ATLAS-200",
                        status="planned",
                    ),
                    NotReadyReason(
                        code=NotReadyCode.NO_ACCEPTANCE_CRITERIA,
                        message="ticket has no acceptance criteria",
                    ),
                ),
            ),
        )
    )

    assert response == TicketDependenciesResponse(
        key="ATLAS-199",
        blockers=[
            DependencyBlockerSchema(
                key="ATLAS-200",
                code=NotReadyCode.DEPENDENCY_NOT_DONE,
            )
        ],
        blocked_by=["ATLAS-201"],
        readiness=TicketReadinessSchema(
            ready=False,
            reasons=[
                NotReadyReasonSchema(
                    code=NotReadyCode.DEPENDENCY_NOT_DONE,
                    message="depends_on ticket 'ATLAS-200' has status 'planned'",
                    target="ATLAS-200",
                    status="planned",
                ),
                NotReadyReasonSchema(
                    code=NotReadyCode.NO_ACCEPTANCE_CRITERIA,
                    message="ticket has no acceptance criteria",
                    target=None,
                    status=None,
                ),
            ],
        ),
    )


def test_present_dependency_critical_path_reuses_dependency_payload_shape() -> None:
    response = present_dependency_critical_path(
        CriticalPath(
            (
                CriticalPathStep("ATLAS-203", effort=2, cumulative_effort=2),
                CriticalPathStep("ATLAS-202", effort=3, cumulative_effort=5),
            )
        )
    )

    assert response == DependencyCriticalPathResponse(
        keys=["ATLAS-203", "ATLAS-202"],
        steps=[
            CriticalPathStepSchema(
                key="ATLAS-203",
                effort=2,
                cumulative_effort=2,
            ),
            CriticalPathStepSchema(
                key="ATLAS-202",
                effort=3,
                cumulative_effort=5,
            ),
        ],
        total_effort=5,
    )


def test_present_review_queue_maps_nested_review_state() -> None:
    state = TicketReviewState(
        key="ATLAS-191",
        title="Extract HTTP presenters",
        status=TicketStatus.REVIEW_REQUIRED,
        ticket_type=TicketType.TECH_DEBT,
        verdict=EvidenceStatus.FAILED,
        checks=(
            ReviewCheckState(
                check_type=VerificationCheckType.TESTS,
                status=EvidenceStatus.PASSED,
            ),
        ),
        has_system_evidence=True,
        has_pr_merged_evidence=False,
    )

    response = present_review_queue((state,))

    assert response == ReviewQueueResponse(
        reviews=[
            ReviewQueueItemSchema(
                key="ATLAS-191",
                title="Extract HTTP presenters",
                status=TicketStatus.REVIEW_REQUIRED,
                ticket_type=TicketType.TECH_DEBT,
                verdict=EvidenceStatus.FAILED,
                checks=[
                    ReviewCheckSchema(
                        check_type=VerificationCheckType.TESTS,
                        status=EvidenceStatus.PASSED,
                    )
                ],
                has_system_evidence=True,
                has_pr_merged_evidence=False,
            )
        ]
    )


def test_present_system_status_maps_snapshot_fields() -> None:
    last_sync = datetime(2026, 7, 25, 10, tzinfo=UTC)
    last_pull = datetime(2026, 7, 25, 11, tzinfo=UTC)

    response = present_system_status(
        SystemStatus(
            package_version="0.1.0",
            schema_revision="0020",
            ticket_count=12,
            evidence_count=34,
            last_linear_sync_at=last_sync,
            last_evidence_pull_at=last_pull,
        )
    )

    assert response == SystemStatusResponse(
        package_version="0.1.0",
        schema_revision="0020",
        ticket_count=12,
        evidence_count=34,
        last_linear_sync_at=last_sync,
        last_evidence_pull_at=last_pull,
    )

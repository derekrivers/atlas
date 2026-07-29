"""Unit tests for pure HTTP response presenters."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from test_apply import _epic_model_kwargs

from atlas.api.presenters import (
    present_dependency_critical_path,
    present_dependency_graph,
    present_epics,
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
    DependencyGraphEdgeSchema,
    DependencyGraphNodeSchema,
    DependencyGraphResponse,
    EpicItemSchema,
    EpicsResponse,
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
    DependencyType,
    Epic,
    EpicStatus,
    EvidenceType,
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
    DependencyGraphEdgeState,
    DependencyGraphNodeState,
    DependencyGraphState,
    ReviewCheckState,
    SystemStatus,
    TicketBoardItemState,
    TicketDependencyState,
    TicketEvidenceRecordState,
    TicketReviewState,
)


def test_presenters_do_not_serialise_enums_to_values() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "atlas" / "api" / "presenters.py"
    ).read_text(encoding="utf-8")

    assert ".value" not in source


def test_present_ticket_board_maps_board_state() -> None:
    response = present_ticket_board(
        [
            TicketBoardItemState(
                key="ATLAS-190",
                title="Earlier ticket",
                status=TicketStatus.IN_PROGRESS,
                ticket_type=TicketType.BUG,
                priority=10,
                risk_level=RiskLevel.MEDIUM,
                epic_key="ATLAS-E1",
            ),
            TicketBoardItemState(
                key="ATLAS-192",
                title="Later ticket",
                status=TicketStatus.PLANNED,
                ticket_type=TicketType.FEATURE,
                priority=20,
                risk_level=RiskLevel.HIGH,
                epic_key=None,
            ),
        ]
    )

    assert response == TicketBoardResponse(
        tickets=[
            TicketBoardItemSchema(
                key="ATLAS-190",
                title="Earlier ticket",
                status=TicketStatus.IN_PROGRESS,
                ticket_type=TicketType.BUG,
                priority=10,
                risk_level=RiskLevel.MEDIUM,
                epic_key="ATLAS-E1",
            ),
            TicketBoardItemSchema(
                key="ATLAS-192",
                title="Later ticket",
                status=TicketStatus.PLANNED,
                ticket_type=TicketType.FEATURE,
                priority=20,
                risk_level=RiskLevel.HIGH,
                epic_key=None,
            ),
        ]
    )


def test_present_epics_sorts_by_natural_key() -> None:
    product_id = uuid4()
    first = Epic(
        **(
            _epic_model_kwargs(product_id, key="ATLAS-E1")
            | {
                "title": "First epic",
                "status": EpicStatus.PLANNED,
            }
        )
    )
    second = Epic(
        **(
            _epic_model_kwargs(product_id, key="ATLAS-E2")
            | {
                "title": "Second epic",
                "status": EpicStatus.IN_PROGRESS,
                "risk_level": "high",
            }
        )
    )
    tenth = Epic(
        **(
            _epic_model_kwargs(product_id, key="ATLAS-E10")
            | {
                "title": "Tenth epic",
                "status": EpicStatus.PLANNED,
            }
        )
    )

    response = present_epics([tenth, second, first])

    assert response == EpicsResponse(
        epics=[
            EpicItemSchema(
                id=first.id,
                product_id=first.product_id,
                key=first.key,
                title="First epic",
                description=first.description,
                objective=first.objective,
                status=EpicStatus.PLANNED,
                priority=first.priority,
                risk_level=first.risk_level,
                source_anchor=first.source_anchor,
                created_by_type=first.created_by_type,
                created_by_id=first.created_by_id,
                created_at=first.created_at,
                updated_at=first.updated_at,
                completed_at=None,
            ),
            EpicItemSchema(
                id=second.id,
                product_id=second.product_id,
                key=second.key,
                title="Second epic",
                description=second.description,
                objective=second.objective,
                status=EpicStatus.IN_PROGRESS,
                priority=second.priority,
                risk_level=RiskLevel.HIGH,
                source_anchor=second.source_anchor,
                created_by_type=second.created_by_type,
                created_by_id=second.created_by_id,
                created_at=second.created_at,
                updated_at=second.updated_at,
                completed_at=None,
            ),
            EpicItemSchema(
                id=tenth.id,
                product_id=tenth.product_id,
                key=tenth.key,
                title="Tenth epic",
                description=tenth.description,
                objective=tenth.objective,
                status=EpicStatus.PLANNED,
                priority=tenth.priority,
                risk_level=tenth.risk_level,
                source_anchor=tenth.source_anchor,
                created_by_type=tenth.created_by_type,
                created_by_id=tenth.created_by_id,
                created_at=tenth.created_at,
                updated_at=tenth.updated_at,
                completed_at=None,
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


def test_present_dependency_graph_maps_nodes_and_edges() -> None:
    response = present_dependency_graph(
        DependencyGraphState(
            nodes=(
                DependencyGraphNodeState(
                    key="ATLAS-2",
                    status="done",
                    node_type="ticket",
                ),
                DependencyGraphNodeState(
                    key="ADR-0008",
                    status="accepted",
                    node_type="adr",
                ),
            ),
            edges=(
                DependencyGraphEdgeState(
                    source="ATLAS-2",
                    target="ADR-0008",
                    dependency_type=DependencyType.DEPENDS_ON,
                ),
            ),
        )
    )

    assert response == DependencyGraphResponse(
        nodes=[
            DependencyGraphNodeSchema(
                key="ATLAS-2",
                status="done",
                node_type="ticket",
            ),
            DependencyGraphNodeSchema(
                key="ADR-0008",
                status="accepted",
                node_type="adr",
            ),
        ],
        edges=[
            DependencyGraphEdgeSchema(
                source="ATLAS-2",
                target="ADR-0008",
                dependency_type=DependencyType.DEPENDS_ON,
            ),
        ],
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
            schema_revision="0021",
            ticket_count=12,
            evidence_count=34,
            last_linear_sync_at=last_sync,
            last_evidence_pull_at=last_pull,
        )
    )

    assert response == SystemStatusResponse(
        package_version="0.1.0",
        schema_revision="0021",
        ticket_count=12,
        evidence_count=34,
        last_linear_sync_at=last_sync,
        last_evidence_pull_at=last_pull,
    )

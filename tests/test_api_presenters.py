"""Unit tests for pure HTTP response presenters."""

from pathlib import Path
from uuid import uuid4

from test_apply import _ticket_model_kwargs

from atlas.api.presenters import (
    present_review_queue,
    present_ticket_board,
    present_ticket_evidence,
)
from atlas.api.schemas import (
    ReviewCheckSchema,
    ReviewQueueItemSchema,
    ReviewQueueResponse,
    TicketBoardItemSchema,
    TicketBoardResponse,
    TicketEvidenceItemSchema,
    TicketEvidenceResponse,
)
from atlas.core.enums import ActorType, EvidenceStatus, RiskLevel
from atlas.core.models import (
    EvidenceType,
    Ticket,
    TicketStatus,
    TicketType,
    VerificationCheckType,
)
from atlas.orchestration import (
    ReviewCheckState,
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

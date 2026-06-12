"""ATLAS-23: parser negatives — each failure class distinct and typed,
all gate-1 attributable (spec §5 attribution)."""

import json

import pytest
from proposal_fixtures import (
    dependency_payload,
    proposal_json,
    proposal_payload,
    ticket_payload,
)

from atlas.planning import (
    ProposalParseError,
    ProposalReferenceError,
    ProposalValidationError,
    parse_proposal,
)


def test_valid_proposal_parses() -> None:
    proposal = parse_proposal(proposal_json())
    assert len(proposal.tickets) == 1
    assert proposal.tickets[0].key is None


def test_malformed_json_is_parse_error() -> None:
    with pytest.raises(ProposalParseError, match="not valid JSON"):
        parse_proposal('{"epics": [')


def test_schema_invalid_payload_is_validation_error() -> None:
    with pytest.raises(ProposalValidationError, match=r"3\.11"):
        parse_proposal(json.dumps({"epics": "not-a-list"}))


def test_system_owned_field_rejected() -> None:
    payload = proposal_payload(tickets=[ticket_payload() | {"id": "7f3e9b2a"}])
    with pytest.raises(ProposalValidationError, match="id"):
        parse_proposal(json.dumps(payload))


def test_out_of_bounds_new_ticket_reference() -> None:
    payload = proposal_payload(
        tickets=[ticket_payload(), ticket_payload()],
        dependencies=[dependency_payload(source="new:0", target="new:5")],
    )
    with pytest.raises(ProposalReferenceError, match=r"new:5.*out of bounds"):
        parse_proposal(json.dumps(payload))


def test_out_of_bounds_new_epic_reference() -> None:
    payload = proposal_payload(tickets=[ticket_payload(epic_ref="new_epic:3")])
    with pytest.raises(ProposalReferenceError, match=r"new_epic:3.*out of bounds"):
        parse_proposal(json.dumps(payload))


@pytest.mark.parametrize("malformed", ["new:abc", "new:", "new:-1"])
def test_malformed_new_reference_forms(malformed: str) -> None:
    payload = proposal_payload(
        tickets=[ticket_payload(), ticket_payload()],
        dependencies=[dependency_payload(target=malformed)],
    )
    with pytest.raises(ProposalReferenceError, match="malformed"):
        parse_proposal(json.dumps(payload))


def test_epic_ref_in_ticket_namespace_rejected() -> None:
    payload = proposal_payload(tickets=[ticket_payload(epic_ref="new:0")])
    with pytest.raises(ProposalReferenceError, match="namespace"):
        parse_proposal(json.dumps(payload))


def test_dependency_referencing_epic_namespace_rejected() -> None:
    # M1 limitation (§3.11): dependencies are ticket-to-ticket only.
    payload = proposal_payload(dependencies=[dependency_payload(target="new_epic:0")])
    with pytest.raises(ProposalReferenceError, match="ticket-to-ticket"):
        parse_proposal(json.dumps(payload))


def test_epic_ref_null_on_non_tech_debt_rejected() -> None:
    payload = proposal_payload(tickets=[ticket_payload(epic_ref=None)])
    with pytest.raises(ProposalValidationError, match="tech_debt"):
        parse_proposal(json.dumps(payload))


def test_epic_ref_null_on_tech_debt_accepted() -> None:
    payload = proposal_payload(
        tickets=[
            ticket_payload(),
            ticket_payload(epic_ref=None, ticket_type="tech_debt"),
        ]
    )
    proposal = parse_proposal(json.dumps(payload))
    assert proposal.tickets[1].epic_ref is None


def test_eight_acceptance_criteria_fail_as_gate_one() -> None:
    # The gap-2 boundary rule: the 1-7 bound is the model's; this is a
    # schema-validity failure, not a gate-7 one.
    payload = proposal_payload(
        tickets=[ticket_payload(acceptance_criteria=[f"c{i}" for i in range(8)])]
    )
    with pytest.raises(ProposalValidationError, match="acceptance_criteria"):
        parse_proposal(json.dumps(payload))


def test_seven_acceptance_criteria_pass() -> None:
    payload = proposal_payload(
        tickets=[ticket_payload(acceptance_criteria=[f"c{i}" for i in range(7)])]
    )
    proposal = parse_proposal(json.dumps(payload))
    assert len(proposal.tickets[0].acceptance_criteria) == 7

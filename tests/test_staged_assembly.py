"""ATLAS-104: pure index-assembly logic (planning-large-corpora.md §4.1).

The §4.1 worked example — two epics, four tickets across both, one
cross-epic new:3 -> new:1 dependency — is the canonical assembly fixture:
its inputs must produce its assembled envelope, byte-for-byte
deterministic. These tests also pin the reference-integrity enforcement
(gap 1): the environment assigns every index, and a model reference the
environment did not assign is a typed error, never silently accepted.
Identity-minting is structurally impossible — the stage models forbid an
index field.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from atlas.core.enums import RiskLevel
from atlas.core.models import DependencyType, TicketType
from atlas.planning.proposal import (
    ProposalDependency,
    ProposalEpic,
    ProposalTicket,
    parse_proposal,
)
from atlas.planning.staged import (
    BatchCountError,
    DependencyReferenceError,
    EpicRefMismatchError,
    StageDependenciesOutput,
    StageEpicsOutput,
    StageTicketsOutput,
    TicketBatch,
    assemble_proposal,
)


def _epic(title: str, **overrides: Any) -> ProposalEpic:
    return ProposalEpic(
        key=None,
        title=title,
        description=f"{title} description.",
        objective=f"{title} objective.",
        priority=10,
        risk_level=RiskLevel.MEDIUM,
        source_anchor="docs/atlas/plan.md#planning",
        **overrides,
    )


def _ticket(title: str, epic_ref: str, **overrides: Any) -> ProposalTicket:
    return ProposalTicket(
        key=None,
        epic_ref=epic_ref,
        title=title,
        objective=f"{title} objective.",
        context="Phase 2.5.",
        ticket_type=TicketType.FEATURE,
        risk_level=RiskLevel.MEDIUM,
        priority=10,
        source_anchor="docs/atlas/plan.md#backlog",
        relevant_docs=[],
        acceptance_criteria=[f"{title} is done."],
        non_goals=["Out of scope."],
        test_requirements=["Tested."],
        implementation_notes=[],
        documentation_requirements=[],
        definition_of_done=["Merged."],
        **overrides,
    )


def worked_example_inputs() -> tuple[
    StageEpicsOutput, list[TicketBatch], StageDependenciesOutput
]:
    """The §4.1 worked example, as the three parsed stage projections."""
    epics = StageEpicsOutput(
        epics=[_epic("Planning Engine"), _epic("Dependency Engine")],
        planner_notes=["epics-note"],
    )
    batch_0 = TicketBatch(
        epic_index="new_epic:0",
        output=StageTicketsOutput(
            tickets=[
                _ticket("Document ingestion", "new_epic:0"),
                _ticket("Deterministic reconciler", "new_epic:0"),
            ],
            planner_notes=["tickets-0-note"],
        ),
    )
    batch_1 = TicketBatch(
        epic_index="new_epic:1",
        output=StageTicketsOutput(
            tickets=[
                _ticket("Graph schema and build", "new_epic:1"),
                _ticket("Readiness detection", "new_epic:1"),
            ],
            planner_notes=["tickets-1-note"],
        ),
    )
    dependencies = StageDependenciesOutput(
        dependencies=[
            ProposalDependency(
                source="new:3",
                target="new:1",
                dependency_type=DependencyType.DEPENDS_ON,
                reason="Readiness detection needs the reconciler first.",
            )
        ],
        planner_notes=["deps-note"],
    )
    return epics, [batch_0, batch_1], dependencies


# --- the §4.1 worked example: byte-for-byte deterministic assembly -----------


def test_worked_example_assembles_to_the_section_4_1_envelope() -> None:
    epics, batches, dependencies = worked_example_inputs()
    proposal = assemble_proposal(epics, batches, dependencies)

    assert [epic.title for epic in proposal.epics] == [
        "Planning Engine",
        "Dependency Engine",
    ]
    # new:<n> by assembled position: the four tickets, contiguous per epic.
    assert [ticket.title for ticket in proposal.tickets] == [
        "Document ingestion",
        "Deterministic reconciler",
        "Graph schema and build",
        "Readiness detection",
    ]
    assert [ticket.epic_ref for ticket in proposal.tickets] == [
        "new_epic:0",
        "new_epic:0",
        "new_epic:1",
        "new_epic:1",
    ]
    # The cross-epic edge: new:3 (Readiness) -> new:1 (reconciler).
    assert len(proposal.dependencies) == 1
    assert proposal.dependencies[0].source == "new:3"
    assert proposal.dependencies[0].target == "new:1"
    # planner_notes concatenated in stage order.
    assert proposal.planner_notes == [
        "epics-note",
        "tickets-0-note",
        "tickets-1-note",
        "deps-note",
    ]


def test_assembly_is_byte_for_byte_deterministic() -> None:
    first = assemble_proposal(*_call_args(worked_example_inputs()))
    second = assemble_proposal(*_call_args(worked_example_inputs()))
    dump_first = json.dumps(first.model_dump(mode="json"), ensure_ascii=False)
    dump_second = json.dumps(second.model_dump(mode="json"), ensure_ascii=False)
    assert dump_first == dump_second


def test_assembled_envelope_parses_as_a_3_11_proposal() -> None:
    epics, batches, dependencies = worked_example_inputs()
    proposal = assemble_proposal(epics, batches, dependencies)
    raw = json.dumps(proposal.model_dump(mode="json"))
    # Round-trips through the unchanged §3.11 parser, bounds-checks and all.
    reparsed = parse_proposal(raw)
    assert len(reparsed.epics) == 2
    assert len(reparsed.tickets) == 4
    assert len(reparsed.dependencies) == 1


def _call_args(
    inputs: tuple[StageEpicsOutput, list[TicketBatch], StageDependenciesOutput],
) -> tuple[StageEpicsOutput, list[TicketBatch], StageDependenciesOutput]:
    return inputs


# --- reference integrity is enforced, never trusted (gap 1) ------------------


def test_ticket_epic_ref_must_match_the_seeded_epic_identity() -> None:
    epics, batches, dependencies = worked_example_inputs()
    # A stage-2 ticket claims a different epic than the batch was seeded with.
    batches[0].output.tickets[1].epic_ref = "new_epic:1"
    with pytest.raises(EpicRefMismatchError, match="not the seeded epic identity"):
        assemble_proposal(epics, batches, dependencies)


def test_batch_seed_must_match_the_assembled_epic_identity() -> None:
    epics, batches, dependencies = worked_example_inputs()
    mis_seeded = TicketBatch(epic_index="new_epic:7", output=batches[0].output)
    with pytest.raises(EpicRefMismatchError, match="seeded with"):
        assemble_proposal(epics, [mis_seeded, batches[1]], dependencies)


def test_dependency_new_index_out_of_range_is_typed_error() -> None:
    epics, batches, dependencies = worked_example_inputs()
    dependencies.dependencies[0].source = "new:99"
    with pytest.raises(DependencyReferenceError, match="out of range"):
        assemble_proposal(epics, batches, dependencies)


def test_dependency_endpoint_may_not_reference_an_epic() -> None:
    epics, batches, dependencies = worked_example_inputs()
    dependencies.dependencies[0].target = "new_epic:0"
    with pytest.raises(DependencyReferenceError, match="references an epic"):
        assemble_proposal(epics, batches, dependencies)


def test_malformed_new_reference_is_typed_error() -> None:
    epics, batches, dependencies = worked_example_inputs()
    dependencies.dependencies[0].source = "new:notanumber"
    with pytest.raises(DependencyReferenceError, match="malformed"):
        assemble_proposal(epics, batches, dependencies)


def test_one_batch_per_epic_is_enforced() -> None:
    epics, batches, dependencies = worked_example_inputs()
    with pytest.raises(BatchCountError, match="one call per epic"):
        assemble_proposal(epics, batches[:1], dependencies)


# --- identity-minting is structurally impossible (extra="forbid") ------------


def test_model_supplied_epic_index_fails_to_parse() -> None:
    # An epic carrying its own new_epic index is rejected at parse: the model
    # never mints identity (ADR-0007); the environment assigns it positionally.
    payload = {
        "epics": [
            {
                "key": None,
                "new_epic": "new_epic:0",  # the forbidden minted index
                "title": "Planning Engine",
                "description": "d",
                "objective": "o",
                "priority": 10,
                "risk_level": "medium",
                "source_anchor": "docs/atlas/plan.md#planning",
            }
        ],
        "planner_notes": [],
    }
    with pytest.raises(ValidationError):
        StageEpicsOutput.model_validate(payload)


def test_model_supplied_ticket_index_fails_to_parse() -> None:
    payload = {
        "tickets": [
            {
                "key": None,
                "new": "new:0",  # the forbidden minted index
                "epic_ref": "new_epic:0",
                "title": "t",
                "objective": "o",
                "context": "c",
                "ticket_type": "feature",
                "risk_level": "medium",
                "priority": 10,
                "source_anchor": "docs/atlas/plan.md#backlog",
                "relevant_docs": [],
                "acceptance_criteria": ["a"],
                "non_goals": ["n"],
                "test_requirements": ["t"],
                "implementation_notes": [],
                "documentation_requirements": [],
                "definition_of_done": ["d"],
            }
        ],
        "planner_notes": [],
    }
    with pytest.raises(ValidationError):
        StageTicketsOutput.model_validate(payload)

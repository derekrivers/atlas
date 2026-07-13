"""Deterministic inbox-stub promotion (ATLAS-146).

Unit-level coverage of ``promote_inbox_stubs``: the pure transform that injects
one ADD ticket per committed inbox stub into a parsed proposal, re-stating a
backlog epic when the model omitted it (A-1). No git, no DB, no model — the
whole point is that promotion is deterministic code.

Every test proves its AC red-first: neuter ``promote_inbox_stubs`` to return the
proposal unchanged (or drop the A-1 re-statement) and each assertion below bites.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from atlas.core.anchors import AnchorIndex, SourceDocument
from atlas.core.enums import ActorType, RiskLevel
from atlas.core.models import Epic
from atlas.core.models.epic import EpicStatus
from atlas.planning.ingestion import durable_alias_documents
from atlas.planning.promotion import (
    _DEFAULT_PRIORITY,
    _DEFAULT_RISK_LEVEL,
    StubPromotionError,
    promote_inbox_stubs,
)
from atlas.planning.proposal import Proposal, ProposalEpic
from atlas.planning.reconciler import Backlog

NOW = datetime(2025, 1, 1, tzinfo=UTC)
CORPUS_PATH = "docs/atlas/plan.md"
CORPUS = "# Planning\n\n## Backlog\n\nThe backlog section.\n"
STUB_PATH = "docs/planning/inbox/smoke-b-fixture.md"
# The stub's durable home (ATLAS-159): the default anchor targets it.
PROCESSED_STUB_PATH = "docs/planning/inbox/processed/smoke-b-fixture.md"


def stub_front_matter(**overrides: Any) -> dict[str, Any]:
    return {
        "title": "Add a delivery-loop marker to the README",
        "objective": "README states changes flow through the Atlas delivery loop.",
        "context": "Smoke B fixture, Phase 1.",
        "ticket_type": "documentation",
        "epic_ref": "ATLAS-E1",
        "acceptance_criteria": [
            "README has a Delivery loop heading with one paragraph."
        ],
        "non_goals": ["No file other than README.md is modified."],
        "test_requirements": ["A doc check asserts the heading and paragraph."],
        "definition_of_done": ["The paragraph names the gate as operator-owned."],
    } | overrides


def stub_document(**overrides: Any) -> SourceDocument:
    fm = overrides.pop("front_matter", stub_front_matter(**overrides))
    body = "# Smoke B fixture\n\nAdd the marker.\n"
    lines = "\n".join(f"{key}: {_yaml_scalar(value)}" for key, value in fm.items())
    content = f"---\n{lines}\n---\n{body}"
    return SourceDocument(path=STUB_PATH, sha="deadbeef", content=content)


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(_yaml_scalar(v) for v in value) + "]"
    if value is None:
        return "null"
    return f'"{value}"' if isinstance(value, str) else str(value)


def anchor_index() -> AnchorIndex:
    # Mirrors the pipeline's index (ATLAS-159): the active stub is indexed at
    # its current path AND its durable processed/ alias, exactly as
    # durable_alias_documents builds it, so the default anchor resolves.
    stub = stub_document()
    return AnchorIndex.build(
        [
            SourceDocument(path=CORPUS_PATH, sha="c0ffee", content=CORPUS),
            stub,
            *durable_alias_documents([stub], []),
        ]
    )


def backlog_epic(key: str = "ATLAS-E1") -> Epic:
    return Epic(
        id=uuid4(),
        product_id=uuid4(),
        key=key,
        title="Bootstrap Repository",
        description="Repo structure and root control documents.",
        objective="A working repository with CI green.",
        status=EpicStatus.PLANNED,
        priority=100,
        risk_level=RiskLevel.MEDIUM,
        source_anchor=f"{CORPUS_PATH}#planning",
        created_by_type=ActorType.AGENT,
        created_by_id="planner",
        created_at=NOW,
        updated_at=NOW,
    )


def empty_proposal(**overrides: Any) -> Proposal:
    payload: dict[str, Any] = {
        "epics": [],
        "tickets": [],
        "dependencies": [],
        "planner_notes": [],
    } | overrides
    return Proposal(**payload)


def keyed_epic_proposal() -> Proposal:
    # A proposal that already re-states ATLAS-E1 with its key (the re-plan case
    # where the model DID echo the epic).
    return empty_proposal(
        epics=[
            ProposalEpic(
                key="ATLAS-E1",
                title="Bootstrap Repository",
                description="Repo structure and root control documents.",
                objective="A working repository with CI green.",
                priority=100,
                risk_level=RiskLevel.MEDIUM,
                source_anchor=f"{CORPUS_PATH}#planning",
            )
        ]
    )


# --- AC-1: promotion produces the ADD -------------------------------------


def test_ac1_promotion_injects_add_with_declared_fields() -> None:
    result = promote_inbox_stubs(
        keyed_epic_proposal(), [stub_document()], Backlog(), anchor_index()
    )
    assert len(result.tickets) == 1
    ticket = result.tickets[0]
    assert ticket.key is None  # an ADD; the reconciler mints the key
    assert ticket.title == "Add a delivery-loop marker to the README"
    assert ticket.epic_ref == "ATLAS-E1"
    assert ticket.acceptance_criteria == [
        "README has a Delivery loop heading with one paragraph."
    ]


def test_ac1_defaults_anchor_and_relevant_docs_to_the_stub() -> None:
    ticket = promote_inbox_stubs(
        keyed_epic_proposal(), [stub_document()], Backlog(), anchor_index()
    ).tickets[0]
    # ATLAS-159: the default anchor is the stub's first heading at its DURABLE
    # processed/ path — the address apply's retirement gives the file — so it
    # resolves at gate 4 from birth and never dangles on retirement.
    assert ticket.source_anchor == f"{PROCESSED_STUB_PATH}#smoke-b-fixture"
    assert ticket.relevant_docs == [PROCESSED_STUB_PATH]


# --- AC-3: empty inbox is a no-op -----------------------------------------


def test_ac3_empty_inbox_is_a_noop() -> None:
    proposal = keyed_epic_proposal()
    result = promote_inbox_stubs(
        proposal, [], Backlog(epics=[backlog_epic()]), anchor_index()
    )
    assert result is proposal
    assert result.tickets == []


# --- AC-5: fail-closed on malformed front-matter --------------------------


def test_ac5_missing_front_matter_block_raises() -> None:
    doc = SourceDocument(path=STUB_PATH, sha="x", content="# no front matter\n\nbody\n")
    with pytest.raises(StubPromotionError) as info:
        promote_inbox_stubs(keyed_epic_proposal(), [doc], Backlog(), anchor_index())
    assert info.value.path == STUB_PATH
    assert info.value.field == "<front-matter>"


def test_ac5_missing_required_field_names_the_field() -> None:
    doc = stub_document(
        front_matter={k: v for k, v in stub_front_matter().items() if k != "objective"}
    )
    with pytest.raises(StubPromotionError) as info:
        promote_inbox_stubs(keyed_epic_proposal(), [doc], Backlog(), anchor_index())
    assert info.value.field == "objective"


def test_ac5_oversized_acceptance_criteria_is_a_typed_error() -> None:
    doc = stub_document(acceptance_criteria=[f"AC {n}" for n in range(8)])  # > 7 cap
    with pytest.raises(StubPromotionError) as info:
        promote_inbox_stubs(keyed_epic_proposal(), [doc], Backlog(), anchor_index())
    assert info.value.path == STUB_PATH


def test_ac5_unknown_epic_is_a_typed_error() -> None:
    doc = stub_document(epic_ref="ATLAS-E99")  # not in proposal, not in backlog
    with pytest.raises(StubPromotionError) as info:
        promote_inbox_stubs(empty_proposal(), [doc], Backlog(), anchor_index())
    assert info.value.field == "epic_ref"


# --- AC-6: deterministic, no model ----------------------------------------


def test_ac6_same_stub_same_backlog_is_byte_identical() -> None:
    first = promote_inbox_stubs(
        keyed_epic_proposal(), [stub_document()], Backlog(), anchor_index()
    )
    second = promote_inbox_stubs(
        keyed_epic_proposal(), [stub_document()], Backlog(), anchor_index()
    )
    assert first.tickets[0].model_dump() == second.tickets[0].model_dump()


# --- AC-7: ADR-0005 — pinned constants, nothing inferred ------------------


def test_ac7_priority_and_risk_default_to_pinned_constants() -> None:
    ticket = promote_inbox_stubs(
        keyed_epic_proposal(), [stub_document()], Backlog(), anchor_index()
    ).tickets[0]
    assert ticket.priority == _DEFAULT_PRIORITY
    assert ticket.risk_level is _DEFAULT_RISK_LEVEL


def test_ac7_front_matter_overrides_the_constants() -> None:
    doc = stub_document(priority=7, risk_level="high")
    ticket = promote_inbox_stubs(
        keyed_epic_proposal(), [doc], Backlog(), anchor_index()
    ).tickets[0]
    assert ticket.priority == 7
    assert ticket.risk_level is RiskLevel.HIGH


# --- AC-9 / A-1: self-contained epic anchoring ----------------------------


def test_ac9_restates_epic_when_proposal_omits_it() -> None:
    # The parsed proposal is MISSING ATLAS-E1 (the model dropped it); promotion
    # must re-state it from the backlog so the ticket stays anchored.
    proposal = empty_proposal()  # no epics at all
    backlog = Backlog(epics=[backlog_epic("ATLAS-E1")])
    result = promote_inbox_stubs(proposal, [stub_document()], backlog, anchor_index())
    assert {e.key for e in result.epics} == {"ATLAS-E1"}
    restated = result.epics[0]
    assert restated.title == "Bootstrap Repository"  # echoed verbatim from backlog
    assert result.tickets[0].epic_ref == "ATLAS-E1"


def test_ac9_does_not_duplicate_an_epic_already_present() -> None:
    result = promote_inbox_stubs(
        keyed_epic_proposal(),
        [stub_document()],
        Backlog(epics=[backlog_epic()]),
        anchor_index(),
    )
    assert [e.key for e in result.epics] == ["ATLAS-E1"]  # not re-added


# --- ATLAS-159: the promoted anchor targets the durable processed/ path ------


def test_promoted_ticket_anchor_targets_processed_path() -> None:
    # The forward fix: the default anchor cites the stub's first heading at
    # its DURABLE processed/ path from birth, and that anchor resolves in the
    # very index the minting run's gate 4 validates against (the pipeline
    # indexes each active stub at both addresses).
    index = anchor_index()
    ticket = promote_inbox_stubs(
        keyed_epic_proposal(), [stub_document()], Backlog(), index
    ).tickets[0]
    assert ticket.source_anchor == f"{PROCESSED_STUB_PATH}#smoke-b-fixture"
    resolved = index.resolve(ticket.source_anchor)
    assert resolved.path == PROCESSED_STUB_PATH
    assert resolved.heading == "Smoke B fixture"

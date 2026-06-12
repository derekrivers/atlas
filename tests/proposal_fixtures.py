"""Shared Proposal fixtures (ATLAS-23). Fixture keys echo plausible
existing-backlog forms (ATLAS-1, ATLAS-E1); they are never new
assignments — keys are assigned by atlas apply, not by fixtures."""

import json
from typing import Any

from atlas.planning import AnchorIndex, SourceDocument

ANCHOR_DOC = SourceDocument(
    path="docs/a.md",
    sha="sha-aaa",
    content="# Alpha\n\n## Beta\n",
)


def anchor_index() -> AnchorIndex:
    return AnchorIndex.build([ANCHOR_DOC])


def epic_payload(**overrides: Any) -> dict[str, Any]:
    return {
        "key": None,
        "title": "Knowledge Core",
        "description": "Models are the contract.",
        "objective": "Round-trip every entity.",
        "priority": 10,
        "risk_level": "medium",
        "source_anchor": "docs/a.md#alpha",
    } | overrides


def ticket_payload(**overrides: Any) -> dict[str, Any]:
    return {
        "key": None,
        "epic_ref": "new_epic:0",
        "title": "Build the thing",
        "objective": "The thing exists.",
        "context": "Phase context.",
        "ticket_type": "feature",
        "risk_level": "medium",
        "priority": 10,
        "source_anchor": "docs/a.md#beta",
        "relevant_docs": [],
        "acceptance_criteria": ["It works."],
        "non_goals": ["Not the other thing."],
        "test_requirements": ["Unit tests."],
        "implementation_notes": [],
        "documentation_requirements": [],
        "definition_of_done": ["Tests pass."],
    } | overrides


def dependency_payload(**overrides: Any) -> dict[str, Any]:
    return {
        "source": "new:0",
        "target": "new:1",
        "dependency_type": "depends_on",
        "reason": "Ordering.",
    } | overrides


def proposal_payload(**overrides: Any) -> dict[str, Any]:
    return {
        "epics": [epic_payload()],
        "tickets": [ticket_payload()],
        "dependencies": [],
        "planner_notes": [],
    } | overrides


def proposal_json(**overrides: Any) -> str:
    return json.dumps(proposal_payload(**overrides))

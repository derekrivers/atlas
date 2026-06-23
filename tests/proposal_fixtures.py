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
        "tags": ["planning"],
        "component": "planning",
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


def fenced(body: str, language: str = "json") -> str:
    """Wrap a JSON body in a markdown code fence, as the model did live."""
    return f"```{language}\n{body}\n```"


def epics_stage_payload(**overrides: Any) -> dict[str, Any]:
    """A StageEpicsOutput projection (epics + planner_notes) — the stage-1
    shape the live staged run emitted."""
    return {
        "epics": [
            epic_payload(),
            epic_payload(title="Dependency Engine", source_anchor="docs/a.md#beta"),
        ],
        "planner_notes": [],
    } | overrides


# Captured from the live staged run (ATLAS-108): the epics stage returned
# complete, valid JSON wrapped in a ```json ... ``` fence, which the parser's
# json.loads(raw) from char 0 rejected ("Expecting value: line 1 column 1
# (char 0)"). The content was correct; only the wrapper broke the parse.
# Committed as a regression fixture — the indented body mirrors the real
# output, which began ```json\n{\n  "epics": [ ...
FENCED_EPICS_OUTPUT = fenced(json.dumps(epics_stage_payload(), indent=2))


def braces_in_strings_epic(**overrides: Any) -> dict[str, Any]:
    """An epic whose free-text fields carry braces and escaped quotes — the
    string/escape-awareness fixture: a } inside a string value must not end
    the object, and a \\" must not close the string."""
    return epic_payload(
        objective='Support the {token: value} form and a "quoted" clause.',
        description="Close brace } then text; a backslash \\ and a brace {.",
        **overrides,
    )

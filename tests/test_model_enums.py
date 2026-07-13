"""ATLAS-12: model-local enums match data-model-and-schemas.md §3.2-3.5.

Expected (member, value) pairs are transcribed from the document, not
derived from the enums, so a drifting enum fails here.
"""

from enum import Enum

import pytest

from atlas.core.models import (
    ADRStatus,
    AnomalyType,
    DependencyType,
    EpicStatus,
    TicketStatus,
    TicketType,
)

DOCUMENTED_MEMBERS: dict[type[Enum], dict[str, str]] = {
    # data-model §3.2
    ADRStatus: {
        "PROPOSED": "proposed",
        "ACCEPTED": "accepted",
        "SUPERSEDED": "superseded",
        "REJECTED": "rejected",
    },
    # data-model §3.3
    EpicStatus: {
        "BACKLOG": "backlog",
        "PLANNED": "planned",
        "IN_PROGRESS": "in_progress",
        "DONE": "done",
        "ARCHIVED": "archived",
    },
    # data-model §3.4
    TicketStatus: {
        "BACKLOG": "backlog",
        "PLANNED": "planned",
        "BLOCKED": "blocked",
        "READY_FOR_AGENT": "ready_for_agent",
        "IN_PROGRESS": "in_progress",
        "PR_OPEN": "pr_open",
        "REVIEW_REQUIRED": "review_required",
        "CHANGES_REQUESTED": "changes_requested",
        "DONE": "done",
        "REJECTED": "rejected",
        "NEEDS_HUMAN_DECISION": "needs_human_decision",
    },
    # data-model §3.4
    TicketType: {
        "FEATURE": "feature",
        "BUG": "bug",
        "TECH_DEBT": "tech_debt",
        "SPIKE": "spike",
        "DOCUMENTATION": "documentation",
        "INFRASTRUCTURE": "infrastructure",
        "RESEARCH": "research",
    },
    # data-model §3.5
    DependencyType: {
        "DEPENDS_ON": "depends_on",
        "RELATES_TO": "relates_to",
        "IMPLEMENTS": "implements",
        "SUPERSEDES": "supersedes",
    },
    # data-model §6.1
    AnomalyType: {
        "OUT_OF_OWNERSHIP_TRANSITION": "out_of_ownership_transition",
        "REVIEW_CYCLE": "review_cycle",
        "DWELL_BREACH": "dwell_breach",
        "STALE_BLOCK": "stale_block",
        "PACK_RENDER_FAILURE": "pack_render_failure",
    },
}


@pytest.mark.parametrize(
    "enum_cls",
    DOCUMENTED_MEMBERS,
    ids=lambda cls: cls.__name__,
)
def test_members_and_values_match_documented(enum_cls: type[Enum]) -> None:
    actual = {member.name: member.value for member in enum_cls}
    assert actual == DOCUMENTED_MEMBERS[enum_cls]


@pytest.mark.parametrize(
    "enum_cls",
    DOCUMENTED_MEMBERS,
    ids=lambda cls: cls.__name__,
)
def test_enums_are_string_valued(enum_cls: type[Enum]) -> None:
    # The doc declares string-valued enums; members must compare equal to
    # their string values so they serialise as plain strings.
    assert issubclass(enum_cls, str)
    for member in enum_cls:
        assert member == member.value


def test_dependency_type_has_no_blocks() -> None:
    # depends_on is the single stored direction (data-model §3.5,
    # ADR-0007); "blocks" is derived at query time in Phase 3, never
    # stored.
    with pytest.raises(ValueError, match="blocks"):
        DependencyType("blocks")
    assert "BLOCKS" not in DependencyType.__members__

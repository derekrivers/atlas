"""ATLAS-11: shared enums match data-model-and-schemas.md §2 verbatim.

The expected (member, value) pairs below are transcribed from the
document, not derived from the enums, so a drifting enum fails here.
"""

from enum import Enum

import pytest

from atlas.core import ActorType, EntityStatus, EvidenceStatus, RiskLevel

DOCUMENTED_MEMBERS: dict[type[Enum], dict[str, str]] = {
    # data-model §2.2
    ActorType: {
        "HUMAN": "human",
        "AGENT": "agent",
        "SYSTEM": "system",
    },
    # data-model §2.3
    EntityStatus: {
        "DRAFT": "draft",
        "ACTIVE": "active",
        "ARCHIVED": "archived",
        "DEPRECATED": "deprecated",
    },
    # data-model §2.4
    RiskLevel: {
        "LOW": "low",
        "MEDIUM": "medium",
        "HIGH": "high",
        "CRITICAL": "critical",
    },
    # data-model §2.5
    EvidenceStatus: {
        "PENDING": "pending",
        "PASSED": "passed",
        "FAILED": "failed",
        "WARNING": "warning",
        "NOT_APPLICABLE": "not_applicable",
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
    # §2 declares string-valued enums (`str, Enum`; StrEnum here): members
    # must compare equal to their string values so they serialise as plain
    # strings.
    assert issubclass(enum_cls, str)
    for member in enum_cls:
        assert member == member.value

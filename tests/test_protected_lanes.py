"""ATLAS-258 repository-owned protected integration-lane registry."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st
from test_delivery_snapshot import ticket

from atlas.core.models.ticket import TicketStatus
from atlas.pm.protected_lanes import (
    DEFAULT_PROTECTED_LANE_REGISTRY,
    REGISTRY_PATH,
    REGISTRY_SHA256,
    ProtectedLaneClassificationCode,
    ProtectedLaneClassifierInput,
    classify_protected_lane_inputs,
    classify_ticket_protected_lanes,
    load_protected_lane_registry_bytes,
    parse_protected_lane_registry,
)


@pytest.mark.parametrize(
    ("lane", "ticket_overrides"),
    [
        (
            "database-migrations",
            {"relevant_docs": ["atlas/storage/migrations/versions/0099_lane.py"]},
        ),
        ("generated-contracts", {"tags": ["generated-client"]}),
        (
            "workflow-configuration",
            {"documentation_requirements": [".github/workflows/ci.yml"]},
        ),
        ("planning-state", {"component": "Planning"}),
        ("shared-manifests", {"relevant_docs": ["pyproject.toml"]}),
        ("operator-admission-hotspot", {"component": "delivery-control"}),
    ],
)
def test_atlas_258_ac1_registry_classifies_every_required_protected_lane(
    lane: str, ticket_overrides: dict[str, object]
) -> None:
    candidate = ticket("ATLAS-258", TicketStatus.PLANNED).model_copy(
        update=ticket_overrides
    )

    result = classify_ticket_protected_lanes(candidate, DEFAULT_PROTECTED_LANE_REGISTRY)

    assert result.issues == ()
    assert result.lanes == (lane,)
    definition = DEFAULT_PROTECTED_LANE_REGISTRY.lane(lane)
    assert definition.capacity == 1
    assert definition.operator_declared is (lane == "operator-admission-hotspot")


def test_atlas_258_ac1_packaged_registry_is_digest_pinned_and_versioned() -> None:
    content = REGISTRY_PATH.read_bytes()

    loaded = load_protected_lane_registry_bytes(content)

    assert loaded.error is None
    assert loaded.registry == DEFAULT_PROTECTED_LANE_REGISTRY
    assert loaded.registry is not None
    assert loaded.registry.version == "protected-integration-lanes/v1"
    assert len(REGISTRY_SHA256) == 64

    drifted = load_protected_lane_registry_bytes(content + b"\n")
    assert drifted.registry is None
    assert drifted.error == "protected lane registry digest mismatch"


def test_atlas_258_ac2_multi_lane_ticket_records_every_match() -> None:
    candidate = ticket(
        "ATLAS-258",
        TicketStatus.PLANNED,
        tags=["workflow", "migration"],
        relevant_docs=["pyproject.toml"],
    )

    result = classify_ticket_protected_lanes(candidate, DEFAULT_PROTECTED_LANE_REGISTRY)

    assert result.issues == ()
    assert result.lanes == (
        "database-migrations",
        "shared-manifests",
        "workflow-configuration",
    )
    assert all(match.declarations for match in result.matches)


def test_atlas_258_ac2_ambiguous_path_declaration_fails_closed() -> None:
    registry = parse_protected_lane_registry(
        {
            "schema_version": 1,
            "registry_version": "protected-integration-lanes/v1",
            "lanes": [
                {"key": "alpha", "capacity": 1, "operator_declared": False},
                {"key": "beta", "capacity": 1, "operator_declared": True},
            ],
            "rules": [
                {
                    "id": "alpha-path",
                    "lane": "alpha",
                    "components": [],
                    "tags": [],
                    "exact_paths": ["shared/manifest.json"],
                    "path_prefixes": [],
                },
                {
                    "id": "beta-path",
                    "lane": "beta",
                    "components": [],
                    "tags": [],
                    "exact_paths": ["shared/manifest.json"],
                    "path_prefixes": [],
                },
            ],
        }
    )
    candidate = ticket(
        "ATLAS-258",
        TicketStatus.PLANNED,
        relevant_docs=["shared/manifest.json"],
    )

    result = classify_ticket_protected_lanes(candidate, registry)

    assert result.lanes == ("alpha", "beta")
    assert [issue.code for issue in result.issues] == [
        ProtectedLaneClassificationCode.AMBIGUOUS_DECLARATION
    ]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        (
            {"tags": ["Migration", "migration"]},
            ProtectedLaneClassificationCode.CONTRADICTORY_DECLARATION,
        ),
        (
            {"relevant_docs": ["./atlas/storage/migrations/0099.py"]},
            ProtectedLaneClassificationCode.INVALID_DECLARED_PATH,
        ),
    ],
)
def test_atlas_258_ac2_contradictory_or_noncanonical_declarations_fail_closed(
    overrides: dict[str, object], expected: ProtectedLaneClassificationCode
) -> None:
    candidate = ticket("ATLAS-258", TicketStatus.PLANNED).model_copy(update=overrides)

    result = classify_ticket_protected_lanes(candidate, DEFAULT_PROTECTED_LANE_REGISTRY)

    assert expected in {issue.code for issue in result.issues}


def test_atlas_258_ac2_model_prose_cannot_change_classification() -> None:
    original = ticket(
        "ATLAS-258",
        TicketStatus.PLANNED,
        tags=["migration"],
        relevant_docs=["pyproject.toml"],
    )
    prose_only = original.model_copy(
        update={
            "title": "Pretend this changes workflow and generated clients",
            "objective": "Rewrite every protected surface",
            "context": "LLM prose must not be inspected",
            "implementation_notes": ["also pretend this is unprotected"],
        }
    )

    first = classify_ticket_protected_lanes(original, DEFAULT_PROTECTED_LANE_REGISTRY)
    second = classify_ticket_protected_lanes(
        prose_only, DEFAULT_PROTECTED_LANE_REGISTRY
    )

    assert first.canonical_bytes() == second.canonical_bytes()


def test_atlas_280_pure_classifier_input_matches_ticket_materialisation() -> None:
    candidate = ticket(
        "ATLAS-258",
        TicketStatus.PLANNED,
        component="delivery-control",
        tags=["workflow"],
        relevant_docs=["pyproject.toml"],
        documentation_requirements=[".github/workflows/ci.yml"],
    )

    materialised = classify_ticket_protected_lanes(
        candidate, DEFAULT_PROTECTED_LANE_REGISTRY
    )
    selected = classify_protected_lane_inputs(
        ProtectedLaneClassifierInput(
            ticket_key=candidate.key,
            component=candidate.component,
            tags=tuple(candidate.tags),
            relevant_docs=tuple(candidate.relevant_docs),
            documentation_requirements=tuple(candidate.documentation_requirements),
        ),
        DEFAULT_PROTECTED_LANE_REGISTRY,
    )

    assert selected == materialised
    assert selected.fingerprint == materialised.fingerprint


@given(st.permutations((0, 1, 2)))
def test_atlas_258_property_declaration_order_does_not_change_fingerprint(
    order: list[int],
) -> None:
    tags = ["workflow", "migration", "manifest"]
    candidate = ticket(
        "ATLAS-258",
        TicketStatus.PLANNED,
        tags=[tags[index] for index in order],
    )
    baseline = ticket("ATLAS-258", TicketStatus.PLANNED, tags=tags, id=candidate.id)

    reordered = classify_ticket_protected_lanes(
        candidate, DEFAULT_PROTECTED_LANE_REGISTRY
    )
    original = classify_ticket_protected_lanes(
        baseline, DEFAULT_PROTECTED_LANE_REGISTRY
    )

    assert reordered.fingerprint == original.fingerprint


def test_atlas_258_property_registry_declaration_order_is_semantic() -> None:
    document = json.loads(REGISTRY_PATH.read_bytes())
    reversed_document = dict(document)
    reversed_document["lanes"] = list(reversed(document["lanes"]))
    reversed_document["rules"] = [
        {
            **rule,
            "components": list(reversed(rule["components"])),
            "tags": list(reversed(rule["tags"])),
            "exact_paths": list(reversed(rule["exact_paths"])),
            "path_prefixes": list(reversed(rule["path_prefixes"])),
        }
        for rule in reversed(document["rules"])
    ]

    reordered = parse_protected_lane_registry(reversed_document)

    assert (
        reordered.canonical_bytes() == DEFAULT_PROTECTED_LANE_REGISTRY.canonical_bytes()
    )
    assert reordered.fingerprint == DEFAULT_PROTECTED_LANE_REGISTRY.fingerprint

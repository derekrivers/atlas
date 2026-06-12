"""ATLAS-23: gates 2-7 — per gate one passing and one failing case,
machine-readable reasons, exact gate-7 boundaries, and the gap
decisions asserted (aggregation; one violation, one attributable
failure)."""

import json
from typing import Any

from proposal_fixtures import (
    anchor_index,
    dependency_payload,
    epic_payload,
    proposal_payload,
    ticket_payload,
)

from atlas.planning import GateFailure, parse_proposal, run_gates

INDEX = anchor_index()


def gates(
    payload: dict[str, Any], backlog: set[str] | None = None
) -> list[GateFailure]:
    proposal = parse_proposal(json.dumps(payload))
    return run_gates(
        proposal,
        current_backlog_keys=backlog or set(),
        anchor_index=INDEX,
    )


def codes(failures: list[GateFailure]) -> list[str]:
    return [failure.code for failure in failures]


def test_clean_proposal_passes_every_gate() -> None:
    assert gates(proposal_payload()) == []


# --- gate 2: acyclicity ------------------------------------------------------


def test_gate2_cycle_detected() -> None:
    payload = proposal_payload(
        tickets=[ticket_payload(), ticket_payload()],
        dependencies=[
            dependency_payload(source="new:0", target="new:1"),
            dependency_payload(source="new:1", target="new:0"),
        ],
    )
    failures = gates(payload)
    assert codes(failures) == ["GATE2_CYCLE"]
    assert failures[0].gate == 2
    assert "cycle" in failures[0].reason


def test_gate2_acyclic_chain_passes() -> None:
    payload = proposal_payload(
        tickets=[ticket_payload(), ticket_payload()],
        dependencies=[dependency_payload(source="new:0", target="new:1")],
    )
    assert gates(payload) == []


# --- gate 3: dependency targets resolve --------------------------------------


def test_gate3_unresolved_echoed_target() -> None:
    payload = proposal_payload(
        dependencies=[dependency_payload(source="new:0", target="ATLAS-404")]
    )
    failures = gates(payload)
    assert codes(failures) == ["GATE3_UNRESOLVED_TARGET"]
    assert "ATLAS-404" in failures[0].reason
    assert "full-state" in failures[0].reason


def test_gate3_echoed_target_resolves_within_proposal() -> None:
    payload = proposal_payload(
        tickets=[ticket_payload(), ticket_payload(key="ATLAS-1")],
        dependencies=[dependency_payload(source="new:0", target="ATLAS-1")],
    )
    assert gates(payload, backlog={"ATLAS-1"}) == []


# --- gate 4: anchors resolve at the recorded SHA -----------------------------


def test_gate4_unresolved_anchor() -> None:
    payload = proposal_payload(tickets=[ticket_payload(source_anchor="docs/a.md#nope")])
    failures = gates(payload)
    assert codes(failures) == ["GATE4_UNRESOLVED_ANCHOR"]
    assert "docs/a.md#nope" in failures[0].reason


def test_gate4_epic_anchor_checked_too() -> None:
    payload = proposal_payload(epics=[epic_payload(source_anchor="docs/ghost.md#x")])
    failures = gates(payload)
    assert codes(failures) == ["GATE4_UNRESOLVED_ANCHOR"]
    assert failures[0].gate == 4


# --- gate 5: structure --------------------------------------------------------


def test_gate5_orphan_epic() -> None:
    payload = proposal_payload(
        tickets=[ticket_payload(epic_ref=None, ticket_type="tech_debt")]
    )
    failures = gates(payload)
    assert codes(failures) == ["GATE5_ORPHAN_EPIC"]


def test_gate5_unresolved_echoed_epic_ref() -> None:
    payload = proposal_payload(
        epics=[epic_payload(key="ATLAS-E1")],
        tickets=[
            ticket_payload(epic_ref="ATLAS-E1"),
            ticket_payload(epic_ref="ATLAS-E9"),
        ],
    )
    failures = gates(payload, backlog={"ATLAS-E1"})
    assert codes(failures) == ["GATE5_UNRESOLVED_EPIC_REF"]
    assert "ATLAS-E9" in failures[0].reason


def test_gate5_echoed_epic_ref_resolves() -> None:
    payload = proposal_payload(
        epics=[epic_payload(key="ATLAS-E1")],
        tickets=[ticket_payload(epic_ref="ATLAS-E1")],
    )
    assert gates(payload, backlog={"ATLAS-E1"}) == []


# --- gate 6: key integrity ----------------------------------------------------


def test_gate6_invented_key_rejected() -> None:
    payload = proposal_payload(tickets=[ticket_payload(key="ATLAS-777")])
    failures = gates(payload, backlog={"ATLAS-1"})
    assert codes(failures) == ["GATE6_UNKNOWN_KEY"]
    assert "ATLAS-777" in failures[0].reason
    assert "ADR-0007" in failures[0].reason


def test_gate6_echoed_backlog_key_passes() -> None:
    payload = proposal_payload(tickets=[ticket_payload(key="ATLAS-1")])
    assert gates(payload, backlog={"ATLAS-1"}) == []


# --- gate 7: size guard (dependency count only, per spec §5 attribution) -----


def chain_payload(dependency_count: int) -> dict[str, Any]:
    tickets = [ticket_payload() for _ in range(dependency_count + 1)]
    dependencies = [
        dependency_payload(source="new:0", target=f"new:{i}")
        for i in range(1, dependency_count + 1)
    ]
    return proposal_payload(tickets=tickets, dependencies=dependencies)


def test_gate7_ten_dependencies_pass() -> None:
    assert gates(chain_payload(10)) == []


def test_gate7_eleven_dependencies_fail() -> None:
    failures = gates(chain_payload(11))
    assert codes(failures) == ["GATE7_OVERSIZED"]
    assert failures[0].gate == 7
    assert "11" in failures[0].reason


# --- gap decisions asserted ----------------------------------------------------


def test_one_violation_one_attributable_failure() -> None:
    # 11 dependencies: exactly one failure, from gate 7 and nowhere else.
    failures = gates(chain_payload(11))
    assert len(failures) == 1
    assert failures[0].gate == 7


def test_aggregation_reports_every_failure() -> None:
    # Three seeded violations across three gates: all reported at once.
    payload = proposal_payload(
        epics=[epic_payload()],  # orphan: the only ticket is epic-less
        tickets=[
            ticket_payload(
                key="ATLAS-777",  # gate 6: not in backlog
                epic_ref=None,
                ticket_type="tech_debt",
                source_anchor="docs/a.md#nope",  # gate 4
            )
        ],
    )
    failures = gates(payload, backlog=set())
    assert codes(failures) == [
        "GATE4_UNRESOLVED_ANCHOR",
        "GATE5_ORPHAN_EPIC",
        "GATE6_UNKNOWN_KEY",
    ]
    assert [failure.gate for failure in failures] == [4, 5, 6]

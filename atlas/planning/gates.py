"""Validation gates 2-7 (ATLAS-23), per spec §5.

Gate 1 (schema validity) is the parser's: a payload that fails it never
becomes a Proposal, so there is nothing for these gates to run over.
Gates 2-7 run unconditionally over a parsed Proposal and AGGREGATE
every failure (gap-1 policy): an operator repairing a proposal sees
the complete list, each failure with its gate number, stable code, and
machine-readable reason — the shape ATLAS-26 persists into the failed
PlanRun.

Full-state semantics, no reconciler: gates take the proposal, the
current backlog's key set, and the AnchorIndex as parameters.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

import networkx as nx

from atlas.core.anchors import AnchorIndex, IngestionError
from atlas.planning.proposal import Proposal


@dataclass(frozen=True)
class GateFailure:
    """One gate failure: machine-readable, attributable, serialisable."""

    gate: int
    code: str
    reason: str


def _ticket_node_ids(proposal: Proposal) -> list[str]:
    """Stable node identity: echoed key, or new:<index> for new tickets."""
    return [
        ticket.key if ticket.key is not None else f"new:{index}"
        for index, ticket in enumerate(proposal.tickets)
    ]


def _resolve_ref(value: str, echoed_keys: set[str]) -> str | None:
    """Reference -> node id, or None when unresolvable (gate 3's case).

    new:<n> is always resolvable here: the parser bounds-checked it.
    """
    if value.startswith("new:"):
        return value
    return value if value in echoed_keys else None


def run_gates(
    proposal: Proposal,
    *,
    current_backlog_keys: Collection[str],
    anchor_index: AnchorIndex,
) -> list[GateFailure]:
    """Run gates 2-7; empty list means every gate passed."""
    failures: list[GateFailure] = []
    node_ids = _ticket_node_ids(proposal)
    echoed_keys = {ticket.key for ticket in proposal.tickets if ticket.key is not None}

    # Gate 3 first (its resolution feeds the gate-2 graph and gate 7).
    edges: list[tuple[str, str]] = []
    for index, dependency in enumerate(proposal.dependencies):
        endpoints = {}
        for field_name, value in (
            ("source", dependency.source),
            ("target", dependency.target),
        ):
            resolved = _resolve_ref(value, echoed_keys)
            if resolved is None:
                failures.append(
                    GateFailure(
                        gate=3,
                        code="GATE3_UNRESOLVED_TARGET",
                        reason=(
                            f"dependencies[{index}].{field_name} {value!r} "
                            "matches no ticket in the proposal (full-state "
                            "semantics: an omitted ticket is archived)"
                        ),
                    )
                )
            else:
                endpoints[field_name] = resolved
        if len(endpoints) == 2:
            edges.append((endpoints["source"], endpoints["target"]))

    # Gate 2: acyclicity over the resolvable dependency graph.
    graph: nx.DiGraph[str] = nx.DiGraph()
    graph.add_nodes_from(node_ids)
    graph.add_edges_from(edges)
    if not nx.is_directed_acyclic_graph(graph):
        cycle = nx.find_cycle(graph)
        path = " -> ".join([edge[0] for edge in cycle] + [cycle[0][0]])
        failures.append(
            GateFailure(
                gate=2,
                code="GATE2_CYCLE",
                reason=f"dependency graph has a cycle: {path}",
            )
        )

    # Gate 4: every source_anchor resolves at the recorded SHA.
    def check_anchor(anchor: str, label: str) -> None:
        try:
            anchor_index.resolve(anchor)
        except IngestionError as error:
            failures.append(
                GateFailure(
                    gate=4,
                    code="GATE4_UNRESOLVED_ANCHOR",
                    reason=f"{label}: {error}",
                )
            )

    for index, epic in enumerate(proposal.epics):
        check_anchor(epic.source_anchor, f"epics[{index}] ({epic.title!r})")
    for index, ticket in enumerate(proposal.tickets):
        check_anchor(ticket.source_anchor, f"tickets[{index}] ({ticket.title!r})")

    # Gate 5: no orphan epics; echoed epic_refs resolve to proposal epics.
    epic_keys = {epic.key for epic in proposal.epics if epic.key is not None}
    referenced_epics: set[str] = set()
    for index, ticket in enumerate(proposal.tickets):
        ref = ticket.epic_ref
        if ref is None:
            continue
        if ref.startswith("new_epic:") or ref in epic_keys:
            referenced_epics.add(ref)
        else:
            failures.append(
                GateFailure(
                    gate=5,
                    code="GATE5_UNRESOLVED_EPIC_REF",
                    reason=(
                        f"tickets[{index}].epic_ref {ref!r} matches no epic "
                        "in the proposal"
                    ),
                )
            )
    for index, epic in enumerate(proposal.epics):
        identities = {f"new_epic:{index}"}
        if epic.key is not None:
            identities.add(epic.key)
        if not identities & referenced_epics:
            failures.append(
                GateFailure(
                    gate=5,
                    code="GATE5_ORPHAN_EPIC",
                    reason=(
                        f"epics[{index}] ({epic.title!r}) has no tickets; "
                        "every epic has at least one ticket (spec §5)"
                    ),
                )
            )

    # Gate 6: key integrity — no model-minted keys (ADR-0007).
    backlog = set(current_backlog_keys)
    for kind, keys in (
        ("tickets", (ticket.key for ticket in proposal.tickets)),
        ("epics", (epic.key for epic in proposal.epics)),
    ):
        for key in keys:
            if key is not None and key not in backlog:
                failures.append(
                    GateFailure(
                        gate=6,
                        code="GATE6_UNKNOWN_KEY",
                        reason=(
                            f"{kind} key {key!r} does not exist in the "
                            "current backlog; the model never invents keys "
                            "(ADR-0007)"
                        ),
                    )
                )

    # Gate 7: size guard — dependency count only; the 1-7 acceptance
    # criteria bound is the Proposal model's and fails as gate 1 (spec
    # §5 attribution).
    dependency_counts: dict[str, int] = dict.fromkeys(node_ids, 0)
    for source, _ in edges:
        if source in dependency_counts:
            dependency_counts[source] += 1
    for node_id, count in dependency_counts.items():
        if count > 10:
            failures.append(
                GateFailure(
                    gate=7,
                    code="GATE7_OVERSIZED",
                    reason=(
                        f"ticket {node_id!r} has {count} dependencies "
                        "(maximum 10); oversized tickets reduce agent "
                        "success (spec §5)"
                    ),
                )
            )

    return sorted(failures, key=lambda failure: (failure.gate, failure.reason))

"""Readiness predicate (ATLAS-34), per docs/atlas/dependency-engine.md
"Readiness predicate".

The predicate that decides which tickets are eligible to be worked on,
computed over ATLAS-31's projected, ATLAS-40-validated DiGraph. The PM
Engine consumes it in Phase 4 (promotion to Ready for Agent); this ticket
stops at the predicate and never touches context packs.

Pure computation over the graph: ZERO model/API calls and ZERO storage
queries. It reads only the node attributes ATLAS-31 already set — ``status``
(the string ``ticket.status.value``), ``node_type``, ``present``, and
``acceptance_criteria_count`` (added at build time so condition 4 needs no
re-query of acceptance criteria) — and the ``dependency_type`` edge
attribute. The graph IS the input.

A ticket is ready exactly when all five conditions hold:

1. ``status`` is ``planned`` or the Atlas-internal compatibility value
   ``backlog``. Governed apply creates new work as ``planned``.
2. Every ``depends_on`` ticket target has ``status: done``.
3. Every ``depends_on`` ADR target has ``status: accepted``.
4. The ticket has >=1 acceptance criterion.
5. No dependency target is missing (a dangling target makes a ticket
   not-ready and is a validation error, not a silent skip).

Relationship to ATLAS-40 (condition 5): readiness assumes it runs on an
already-validated graph, where ATLAS-40's ``validate_graph`` has already
raised ``DanglingTargetError`` on any ``present=False`` target before
readiness is ever computed — that raise is the hard gate. Readiness does
not re-implement dangling detection; it DEFENDS against a ``present=False``
target by reporting a :class:`NotReadyCode.DANGLING_TARGET` reason rather
than crashing or silently treating it as ready. A dangling target is never
silently ready on either side.

Results are typed (:class:`ReadinessResult`), not bare booleans, so callers
(the PM Engine, the later ``atlas deps ready`` CLI) can inspect WHY a ticket
is not ready, not merely that it isn't.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import networkx as nx

from atlas.core.models.adr import ADRStatus
from atlas.core.models.dependency import DependencyType
from atlas.core.models.ticket import TicketStatus

# Statuses from which a ticket is eligible to be promoted, derived from the
# enum (not hard-coded): normal ``planned`` plus retained ``backlog``
# compatibility. Dependency blockedness never adds a stored status here.
READY_STATUSES: frozenset[str] = frozenset(
    {TicketStatus.PLANNED.value, TicketStatus.BACKLOG.value}
)
# The status a depends_on ticket target must have (condition 2) and the
# status a depends_on ADR target must have (condition 3), enum-derived.
_DEPENDENCY_DONE = TicketStatus.DONE.value
_ADR_ACCEPTED = ADRStatus.ACCEPTED.value

_DEPENDS_ON = DependencyType.DEPENDS_ON.value


class NotReadyCode(StrEnum):
    """Why a ticket is not ready — one code per failing readiness condition,
    so a caller can branch on the machine code and show the message."""

    WRONG_STATUS = "wrong_status"
    DEPENDENCY_NOT_DONE = "dependency_not_done"
    ADR_NOT_ACCEPTED = "adr_not_accepted"
    NO_ACCEPTANCE_CRITERIA = "no_acceptance_criteria"
    DANGLING_TARGET = "dangling_target"


@dataclass(frozen=True)
class NotReadyReason:
    """A single failing condition. ``target`` names the offending
    ``depends_on`` node key (or its UUID, for a dangling target) when the
    reason is about a dependency; ``status`` carries the offending status
    when one is relevant."""

    code: NotReadyCode
    message: str
    target: str | None = None
    status: str | None = None


@dataclass(frozen=True)
class ReadinessResult:
    """The typed readiness verdict for one ticket. ``ready`` is derived from
    ``reasons`` so the boolean can never contradict the failing conditions:
    a ticket is ready exactly when no condition failed."""

    key: str
    reasons: tuple[NotReadyReason, ...] = field(default_factory=tuple)

    @property
    def ready(self) -> bool:
        return not self.reasons


def is_ready(graph: nx.DiGraph[str], key: str) -> ReadinessResult:
    """Evaluate the five readiness conditions for ticket ``key`` over the
    projected graph and return a typed :class:`ReadinessResult`.

    Collects EVERY failing condition (not first-fail), mirroring ATLAS-40's
    collect-all, so callers see every reason at once. Reads only ATLAS-31's
    node/edge attributes; never touches storage.

    ``key`` must name a present ticket node — that is a precondition of the
    predicate, distinct from the readiness conditions, so a missing key or a
    non-ticket node raises :class:`ValueError` rather than reporting
    not-ready.
    """
    if key not in graph:
        raise ValueError(f"no such node in graph: {key!r}")
    data = graph.nodes[key]
    if data.get("node_type") != "ticket" or not data.get("present", True):
        raise ValueError(f"not a present ticket node: {key!r}")

    reasons: list[NotReadyReason] = []

    # Condition 1: status is planned or retained backlog compatibility.
    status = data.get("status")
    if status not in READY_STATUSES:
        reasons.append(
            NotReadyReason(
                NotReadyCode.WRONG_STATUS,
                f"status {status!r} is not one of {sorted(READY_STATUSES)}",
                status=str(status) if status is not None else None,
            )
        )

    # Conditions 2, 3, 5: every depends_on target. Edge A -> B means A
    # depends_on B, so the targets are the out-edges of this ticket.
    for _source, target, dep_type in graph.out_edges(key, data="dependency_type"):
        if dep_type != _DEPENDS_ON:
            continue
        target_data = graph.nodes[target]
        # Condition 5 (defensive): an absent target is the dangling case
        # ATLAS-40 raises on; readiness reports it not-ready rather than
        # crashing or treating it as satisfied.
        if not target_data.get("present", True):
            reasons.append(
                NotReadyReason(
                    NotReadyCode.DANGLING_TARGET,
                    f"depends_on target {target!r} is missing (dangling)",
                    target=target,
                )
            )
            continue
        target_type = target_data.get("node_type")
        target_status = target_data.get("status")
        # node_type selects the rule: ticket -> done, adr -> accepted.
        # Target types the spec does not name (epic/component) are not
        # gating.
        if target_type == "ticket" and target_status != _DEPENDENCY_DONE:
            reasons.append(
                NotReadyReason(
                    NotReadyCode.DEPENDENCY_NOT_DONE,
                    f"depends_on ticket {target!r} has status "
                    f"{target_status!r}, not {_DEPENDENCY_DONE!r}",
                    target=target,
                    status=str(target_status) if target_status is not None else None,
                )
            )
        elif target_type == "adr" and target_status != _ADR_ACCEPTED:
            reasons.append(
                NotReadyReason(
                    NotReadyCode.ADR_NOT_ACCEPTED,
                    f"depends_on ADR {target!r} has status "
                    f"{target_status!r}, not {_ADR_ACCEPTED!r}",
                    target=target,
                    status=str(target_status) if target_status is not None else None,
                )
            )

    # Condition 4: >=1 acceptance criterion, read from the count ATLAS-31
    # set so storage is never re-queried.
    if data.get("acceptance_criteria_count", 0) < 1:
        reasons.append(
            NotReadyReason(
                NotReadyCode.NO_ACCEPTANCE_CRITERIA,
                "ticket has no acceptance criteria",
            )
        )

    return ReadinessResult(key, tuple(reasons))


def ready_tickets(graph: nx.DiGraph[str]) -> list[ReadinessResult]:
    """The ready tickets over the whole projected graph, key-ordered.

    Every present ticket node is run through :func:`is_ready`; only the
    ready ones are returned. Callers wanting the WHY for a not-ready ticket
    call :func:`is_ready` directly.
    """
    results = [
        is_ready(graph, key)
        for key, data in graph.nodes(data=True)
        if data.get("node_type") == "ticket" and data.get("present", True)
    ]
    return sorted(
        (result for result in results if result.ready),
        key=lambda result: result.key,
    )

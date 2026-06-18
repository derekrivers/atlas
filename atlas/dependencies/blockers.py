"""Blocker analysis (ATLAS-36), per docs/atlas/dependency-engine.md
"Blocker analysis".

Three ADVISORY functions over ATLAS-31's projected, ATLAS-40-validated
DiGraph. They surface what is blocking what, what completing a ticket would
unlock, and which high-risk tickets other work depends on. Nothing here adds
or modifies a gate: blocker analysis NEVER influences readiness (ATLAS-34) or
dispatch. Pure, deterministic computation: ZERO model/API calls and ZERO
storage queries — the graph IS the input.

Single source of "a dependency is satisfied". This module owns NO copy of
that rule. :func:`blocked` derives its blocking set from
:func:`~atlas.dependencies.readiness.is_ready` — it keeps only the
dependency-related reasons (:class:`~atlas.dependencies.readiness.NotReadyCode`
``DEPENDENCY_NOT_DONE`` / ``ADR_NOT_ACCEPTED`` / ``DANGLING_TARGET``) and
discards the status/criteria reasons, which are about the ticket itself, not
its dependencies. :func:`unlocks` and :func:`high_risk_blockers` are in turn
expressed over :func:`is_ready` and :func:`blocked`, so "what blocks ticket
``d``" has exactly one implementation.

Results are typed (mirroring ATLAS-34/35), with the boolean/count derived from
the data so it can never contradict it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from atlas.core.enums import RiskLevel
from atlas.core.models.dependency import DependencyType
from atlas.core.models.ticket import TicketStatus
from atlas.dependencies.readiness import NotReadyCode, is_ready
from atlas.dependencies.validation import TERMINAL_STATUSES

# The readiness reasons that are about a DEPENDENCY (not the ticket's own
# status or criteria): exactly these make a ticket "blocked" by something else.
_BLOCKING_CODES: frozenset[NotReadyCode] = frozenset(
    {
        NotReadyCode.DEPENDENCY_NOT_DONE,
        NotReadyCode.ADR_NOT_ACCEPTED,
        NotReadyCode.DANGLING_TARGET,
    }
)
# Risk levels the advisory high-risk report surfaces, enum-derived.
_HIGH_RISK: frozenset[str] = frozenset({RiskLevel.HIGH.value, RiskLevel.CRITICAL.value})
# The status that completing a ticket sets (the unlocks "what if t finished?"
# hypothesis) and that high_risk_blockers means by "not done", enum-derived.
_DONE = TicketStatus.DONE.value
_DEPENDS_ON = DependencyType.DEPENDS_ON.value


@dataclass(frozen=True)
class BlockedTarget:
    """One unfinished ``depends_on`` target of a ticket, carrying the
    readiness ``code`` that explains WHY it blocks — a pending ticket
    (``DEPENDENCY_NOT_DONE``), an unaccepted ADR (``ADR_NOT_ACCEPTED``), or a
    dangling data error (``DANGLING_TARGET``). Preserving the code lets a
    consumer tell a normal pending dependency from a data defect."""

    key: str
    code: NotReadyCode


@dataclass(frozen=True)
class BlockedResult:
    """The unfinished ``depends_on`` targets blocking one ticket. ``targets``
    are key-sorted; ``is_blocked`` is derived so it cannot contradict them."""

    key: str
    targets: tuple[BlockedTarget, ...] = field(default_factory=tuple)

    @property
    def is_blocked(self) -> bool:
        return bool(self.targets)


@dataclass(frozen=True)
class UnlocksResult:
    """The direct dependents that flip not-ready -> ready when a ticket is
    treated as done. ``dependents`` are key-sorted; ``count`` is derived."""

    key: str
    dependents: tuple[str, ...] = field(default_factory=tuple)

    @property
    def count(self) -> int:
        return len(self.dependents)


@dataclass(frozen=True)
class HighRiskBlocker:
    """One high/critical-risk ticket that non-terminal work depends on.
    ``blocks`` is the key-sorted set of tickets it blocks; ``blocked_count``
    is derived so it cannot contradict the data. Advisory only."""

    target: str
    risk_level: str
    blocks: tuple[str, ...]

    @property
    def blocked_count(self) -> int:
        return len(self.blocks)


def _require_present_ticket(graph: nx.DiGraph[str], key: str) -> None:
    """Precondition shared by :func:`blocked` and :func:`unlocks`, mirroring
    :func:`is_ready`: ``key`` must name a present ticket node, else
    :class:`ValueError`. This is a precondition, not a blocker verdict."""
    if key not in graph:
        raise ValueError(f"no such node in graph: {key!r}")
    data = graph.nodes[key]
    if data.get("node_type") != "ticket" or not data.get("present", True):
        raise ValueError(f"not a present ticket node: {key!r}")


def blocked(graph: nx.DiGraph[str], key: str) -> BlockedResult:
    """The unfinished ``depends_on`` targets blocking ticket ``key``.

    Derived from :func:`is_ready` (the single source of dependency
    satisfaction): keeps exactly the reasons whose code is in
    :data:`_BLOCKING_CODES` and reports each target with that code, so a done
    target never appears and a dangling/unaccepted one does. Reads only
    ATLAS-31 attributes; never touches storage.

    ``key`` must name a present ticket node (the :func:`is_ready` precondition,
    which raises :class:`ValueError`).
    """
    result = is_ready(graph, key)
    targets = tuple(
        sorted(
            (
                BlockedTarget(reason.target, reason.code)
                for reason in result.reasons
                if reason.code in _BLOCKING_CODES and reason.target is not None
            ),
            key=lambda target: target.key,
        )
    )
    return BlockedResult(key, targets)


def unlocks(graph: nx.DiGraph[str], key: str) -> UnlocksResult:
    """The direct dependents that become ready iff ``key`` is treated as done.

    A dependent ``d`` (a present ticket with a ``depends_on`` edge INTO
    ``key``) is unlocked exactly when ``is_ready(graph, d)`` is False AND
    ``is_ready(copy, d)`` is True, where ``copy`` is ``graph`` with ``key``'s
    status set to done. A ``d`` still blocked by another dependency therefore
    does not count, and an already-done ``key`` unlocks nothing (no dependent
    flips). The input graph is NEVER mutated — the hypothesis runs on a copy.

    ``key`` must name a present ticket node, else :class:`ValueError`.
    """
    _require_present_ticket(graph, key)

    # The "what if key finished?" hypothesis, on a copy so the input is never
    # mutated. copy() gives independent attribute dicts; reassigning status
    # touches only the copy.
    finished = graph.copy()
    finished.nodes[key]["status"] = _DONE

    unlocked: list[str] = []
    for source, _target, dep_type in graph.in_edges(key, data="dependency_type"):
        if dep_type != _DEPENDS_ON:
            continue
        source_data = graph.nodes[source]
        if source_data.get("node_type") != "ticket" or not source_data.get(
            "present", True
        ):
            continue
        if not is_ready(graph, source).ready and is_ready(finished, source).ready:
            unlocked.append(source)

    return UnlocksResult(key, tuple(sorted(unlocked)))


def high_risk_blockers(graph: nx.DiGraph[str]) -> tuple[HighRiskBlocker, ...]:
    """Advisory report: high/critical-risk tickets that non-terminal work
    depends on.

    A blocker ``B`` is reported when it is a present, not-done ticket with
    ``risk_level`` in {high, critical} that is a ``depends_on`` target of at
    least one non-terminal present ticket. Aggregated over :func:`blocked` (the
    single "what blocks ``d``" implementation): each non-terminal present
    ticket ``d`` contributes its blocked targets, kept only where the target is
    a high/critical ticket, bucketed by target. A done target never appears
    (``blocked`` omits satisfied dependencies); a dangling target carries no
    ``risk_level`` and is excluded by construction. Ordered by blocked-count
    descending, then key. NEVER gates anything.
    """
    blocks_by_target: dict[str, set[str]] = {}
    for source, data in graph.nodes(data=True):
        if data.get("node_type") != "ticket" or not data.get("present", True):
            continue
        if data.get("status") in TERMINAL_STATUSES:
            continue
        for target in blocked(graph, source).targets:
            if graph.nodes[target.key].get("risk_level") in _HIGH_RISK:
                blocks_by_target.setdefault(target.key, set()).add(source)

    report = [
        HighRiskBlocker(
            target,
            graph.nodes[target]["risk_level"],
            tuple(sorted(sources)),
        )
        for target, sources in blocks_by_target.items()
    ]
    report.sort(key=lambda blocker: (-blocker.blocked_count, blocker.target))
    return tuple(report)

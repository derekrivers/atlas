"""Typed graph-validation errors (ATLAS-40), per
docs/atlas/dependency-engine.md "Validation rules".

One subclass per validation rule, a shared ``GraphValidationError`` base
so a caller that only cares "valid or not" writes ``except
GraphValidationError``, and a ``GraphValidationFailed`` aggregate that
carries every violation found in one pass (collect-all, not raise-on-first
— an operator sees every problem at once, mirroring how the reconciler
surfaces all conflicts). No generic exceptions are used for validation
failures.
"""

from __future__ import annotations


class GraphValidationError(Exception):
    """Base for every dependency-graph validation failure.

    ``validate_graph`` raises the :class:`GraphValidationFailed` aggregate;
    the individual rule errors below are the typed members it carries in
    ``.violations``. Catching this base catches both.
    """


class CycleError(GraphValidationError):
    """The ``depends_on`` subgraph is cyclic.

    ``cycle_path`` is the full cycle as a closed loop of node keys
    (``[a, b, c, a]``), mirroring the gate-2 acyclicity report.
    """

    def __init__(self, cycle_path: list[str]) -> None:
        self.cycle_path = cycle_path
        super().__init__(f"dependency graph has a cycle: {' -> '.join(cycle_path)}")


class SelfEdgeError(GraphValidationError):
    """A node depends on itself (a self-loop ``A -> A``)."""

    def __init__(self, node: str) -> None:
        self.node = node
        super().__init__(f"self-edge: {node!r} depends on itself")


class DuplicateEdgeError(GraphValidationError):
    """Two edges share the same (source, target, dependency_type).

    Structurally unrepresentable in ATLAS-31's ``DiGraph`` projection;
    this fires only if the projection becomes a ``MultiDiGraph`` (see
    ``validate_graph``).
    """

    def __init__(self, source: str, target: str, dependency_type: str) -> None:
        self.source = source
        self.target = target
        self.dependency_type = dependency_type
        super().__init__(
            f"duplicate edge: {source!r} -> {target!r} ({dependency_type})"
        )


class DanglingTargetError(GraphValidationError):
    """An edge points at an absent (``present=False``) target node — the
    honest representation ATLAS-31 left visible for this rule to raise on."""

    def __init__(self, target: str, sources: list[str]) -> None:
        self.target = target
        self.sources = sources
        joined = ", ".join(repr(source) for source in sources)
        super().__init__(
            f"dangling target {target!r} (absent node) is depended on by: {joined}"
        )


class TerminalDependencyError(GraphValidationError):
    """A terminal-status ticket has a ``depends_on`` edge to a non-terminal
    one — completed work cannot newly depend on pending work (a data
    error)."""

    def __init__(
        self,
        source: str,
        source_status: str,
        target: str,
        target_status: str,
    ) -> None:
        self.source = source
        self.source_status = source_status
        self.target = target
        self.target_status = target_status
        super().__init__(
            f"terminal ticket {source!r} ({source_status}) depends on "
            f"non-terminal {target!r} ({target_status})"
        )


class GraphValidationFailed(GraphValidationError):
    """Aggregate raised by ``validate_graph`` when any rule fails; carries
    every individual typed violation in ``.violations``."""

    def __init__(self, violations: list[GraphValidationError]) -> None:
        self.violations = violations
        summary = "; ".join(str(violation) for violation in violations)
        super().__init__(f"graph validation failed ({len(violations)}): {summary}")

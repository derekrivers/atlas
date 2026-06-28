"""Verification Engine (Phase 7), per docs/atlas/verification-engine.md.

Verification answers one question: is the "no evidence = no completion"
rule satisfied for a ticket at its PR head commit? It is a pure evaluator —
it never creates evidence, never transitions tickets (the PM Engine acts on
its verdict), and never accepts agent-tier evidence for a machine-checkable
requirement.

ATLAS-71 lands the foundation: the required-check matrix (configuration, in
``required_checks.yaml``) and the pure rule-resolver :func:`required_checks`,
which answers "which VerificationCheckTypes does this ticket require?".
ATLAS-75 adds the first per-check evaluator, :func:`evaluate_machine_check`
(machine_checks.py) — TESTS/LINT against system-tier evidence pinned to the PR
head commit. ATLAS-74 adds :func:`evaluate_documentation_check`
(documentation_check.py) — the ``documentation`` check against system-tier
DOCUMENTATION_UPDATE evidence pinned to the same head commit. The remaining
per-check evaluators, completion validators, and CLI/reports are later Phase 7
tickets.

Layer position: ``atlas.verification`` sits directly below ``atlas.pm`` and
above ``atlas.context`` in the import-linter spine. It may import only layers
below it (``atlas.core`` and friends) — never reach up into ``atlas.pm``,
``atlas.planning``, or ``atlas.cli``. The resolver imports ``atlas.core``
only.

Contract recorded for later tickets (OP-3): a future ``atlas verify`` will
write its human-tier acceptance/approval outcomes as Evidence records pinned
to the PR head commit (commit_sha == the verified head). Those later tickets
must honour that pin — this ticket implements none of it; it only records the
contract here so the writers do not drift from it.

Deferred (OP-4): SECURITY verification has no evaluator in v1. The resolver
surfaces a SECURITY check only for ``risk_level == critical``, as
``required=False`` so it never gates; see verification-engine.md.
"""

from atlas.verification.documentation_check import (
    DocumentationEvaluation,
    evaluate_documentation_check,
)
from atlas.verification.machine_checks import (
    MACHINE_CHECK_EVIDENCE,
    MACHINE_CHECK_TYPES,
    MachineCheckEvaluation,
    evaluate_machine_check,
)
from atlas.verification.rules import RequiredCheck, required_checks

__all__ = [
    "MACHINE_CHECK_EVIDENCE",
    "MACHINE_CHECK_TYPES",
    "DocumentationEvaluation",
    "MachineCheckEvaluation",
    "RequiredCheck",
    "evaluate_documentation_check",
    "evaluate_machine_check",
    "required_checks",
]

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
DOCUMENTATION_UPDATE evidence pinned to the same head commit. ATLAS-72 adds
:func:`evaluate_acceptance_criteria` (acceptance_check.py) — the
``acceptance_criteria`` check against human-tier MANUAL_APPROVAL confirmations
pinned to the same head commit, one per criterion (it also defines
:func:`acceptance_criterion_hash`, the confirmation convention ATLAS-73 and
ATLAS-80 inherit). ATLAS-73 adds :func:`evaluate_scope` (scope_check.py) — the
``scope`` check: every changed PR file must be in the ticket's declared scope
(``relevant_docs`` + the ``source_anchor`` path) or carry a human-tier
waive/fail decision pinned to the same head commit (inheriting ATLAS-72's
per-decision convention, with a literal path discriminator). ATLAS-76 is the
composition keystone: :func:`evaluate_human_approval` (human_approval_check.py)
closes the OP-A gap the resolver left — a blanket human-tier MANUAL_APPROVAL
pinned to the head commit, ticket-scoped, carrying NEITHER discriminator (its
absence is what distinguishes it from an acceptance confirmation or a scope
decision), latest deciding with status pass-through (OP-B) — and
:func:`evaluate_ticket` (completion.py) resolves a ticket's required checks,
dispatches each to its evaluator, and composes the per-ticket verdict (PASSED iff
every required check PASSED; FAILED if any required FAILED; else PENDING;
non-required checks like the deferred SECURITY do not gate). PR-level aggregation
and the CLI/reports are later Phase 7 tickets.

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

from atlas.verification.acceptance_check import (
    AcceptanceEvaluation,
    acceptance_criterion_hash,
    evaluate_acceptance_criteria,
)
from atlas.verification.completion import (
    CheckOutcome,
    TicketVerification,
    evaluate_ticket,
)
from atlas.verification.documentation_check import (
    DocumentationEvaluation,
    evaluate_documentation_check,
)
from atlas.verification.human_approval_check import (
    HumanApprovalEvaluation,
    evaluate_human_approval,
)
from atlas.verification.machine_checks import (
    MACHINE_CHECK_EVIDENCE,
    MACHINE_CHECK_TYPES,
    MachineCheckEvaluation,
    evaluate_machine_check,
)
from atlas.verification.rules import RequiredCheck, required_checks
from atlas.verification.scope_check import (
    SCOPE_DECISION_PATH_KEY,
    ScopeEvaluation,
    evaluate_scope,
)

__all__ = [
    "MACHINE_CHECK_EVIDENCE",
    "MACHINE_CHECK_TYPES",
    "SCOPE_DECISION_PATH_KEY",
    "AcceptanceEvaluation",
    "CheckOutcome",
    "DocumentationEvaluation",
    "HumanApprovalEvaluation",
    "MachineCheckEvaluation",
    "RequiredCheck",
    "ScopeEvaluation",
    "TicketVerification",
    "acceptance_criterion_hash",
    "evaluate_acceptance_criteria",
    "evaluate_documentation_check",
    "evaluate_human_approval",
    "evaluate_machine_check",
    "evaluate_scope",
    "evaluate_ticket",
    "required_checks",
]

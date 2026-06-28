"""The Verification Engine rule-resolver (ATLAS-71), per
docs/atlas/verification-engine.md "Required-check matrix".

:func:`required_checks` answers one question for a ticket: which
:class:`VerificationCheckType`\\ s are required, and which (if any) are
surfaced but non-gating? It is PURE — no I/O beyond reading the packaged
matrix, no clock, no storage — and it NEVER raises (the
``atlas/context/validation.py`` idiom: a pre-completion gate must answer
"what is required, and why" without throwing).

The required-check matrix is configuration (``required_checks.yaml``, shipped
as package data), not code: tightening it is a doc change. The resolver reads
each matrix cell's token and emits the check per that token; a check absent
from a ticket-type's row is simply not required. Two rules live in code, not
the matrix:

- a check whose token's condition is unmet is OMITTED entirely (e.g.
  ``documentation`` when the ticket has no ``documentation_requirements``,
  or ``human_approval`` when ``risk_level`` is below high); and
- the OP-4 cross-cutting SECURITY rule: for ``risk_level == critical`` the
  resolver surfaces a SECURITY check with ``required=False`` so it never
  gates, the evaluator being deferred (verification-engine.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml

from atlas.core.enums import RiskLevel
from atlas.core.models import Ticket, VerificationCheckType

# Matrix cell tokens (verification-engine.md). A cell carries exactly one.
TOKEN_REQUIRED = "required"
TOKEN_IF_DOCUMENTATION_REQUIREMENTS = "if_documentation_requirements"
TOKEN_IF_RISK_HIGH = "if_risk_high"

# The matrix columns in design-doc order, so the resolver's output order is
# deterministic and column-faithful regardless of YAML key order. SECURITY is
# absent — it is the code-applied cross-cutting rule, never a matrix column.
_MATRIX_COLUMNS: tuple[VerificationCheckType, ...] = (
    VerificationCheckType.TESTS,
    VerificationCheckType.LINT,
    VerificationCheckType.ACCEPTANCE_CRITERIA,
    VerificationCheckType.DOCUMENTATION,
    VerificationCheckType.SCOPE,
    VerificationCheckType.HUMAN_APPROVAL,
)

# "risk ≥ high" in the matrix: high or critical.
_RISK_AT_LEAST_HIGH = frozenset({RiskLevel.HIGH, RiskLevel.CRITICAL})

# OP-4: recorded on the non-gating SECURITY check so a reader (and any later
# writer) knows it is deferred, not a verdict. A future evaluator records the
# check's status as NOT_APPLICABLE while it is required=False.
SECURITY_DEFERRAL_NOTE = (
    "SECURITY verification is deferred (ATLAS-71): v1 surfaces this check as "
    "non-gating (required=False, status not_applicable) for critical-risk "
    "tickets; the evaluator is a later Phase 7 ticket."
)

_MATRIX_RESOURCE = "required_checks.yaml"


@dataclass(frozen=True)
class RequiredCheck:
    """One resolved entry of a ticket's required-check set.

    Frozen: a resolved requirement is an immutable record. ``required`` is
    ``True`` for every gating check; it is ``False`` only for the OP-4
    non-gating SECURITY surface, which then carries a ``note`` explaining the
    deferral. ``note`` is ``None`` for ordinary required checks.
    """

    check_type: VerificationCheckType
    required: bool
    note: str | None = None


@lru_cache(maxsize=1)
def _load_matrix() -> dict[str, dict[str, str]]:
    """Read and index ``required_checks.yaml`` (cached; the file is static).

    Each YAML key is one matrix row label; a label may name several ticket
    types joined by ``/`` (the design-doc ``spike/research`` row), so the row
    is indexed under each. The returned mapping is ticket-type value ->
    {check_type value -> token}.
    """
    raw = (
        resources.files("atlas.verification")
        .joinpath(_MATRIX_RESOURCE)
        .read_text(encoding="utf-8")
    )
    parsed: Any = yaml.safe_load(raw)
    matrix: dict[str, dict[str, str]] = {}
    for label, cells in parsed.items():
        row = {str(check): str(token) for check, token in cells.items()}
        for ticket_type in str(label).split("/"):
            matrix[ticket_type] = row
    return matrix


def required_checks(ticket: Ticket) -> tuple[RequiredCheck, ...]:
    """Resolve the required-check set for ``ticket`` (never raises).

    Reads the matrix row for ``ticket.ticket_type`` and emits each cell per
    its token; conditional cells whose condition is unmet are omitted. Then
    applies the OP-4 SECURITY rule in code. An unknown ticket type (no row) or
    an unrecognised token yields no entry for it rather than an error — a pure
    gate answers without throwing; matrix integrity is a test's job, not a
    resolve-time exception.
    """
    row = _load_matrix().get(ticket.ticket_type.value, {})
    checks: list[RequiredCheck] = []
    for column in _MATRIX_COLUMNS:
        token = row.get(column.value)
        # A column is required when its cell is unconditional, or its
        # conditional token's condition holds. A None/unrecognised token, or an
        # unmet condition, emits nothing for this column.
        if (
            token == TOKEN_REQUIRED
            or (
                token == TOKEN_IF_DOCUMENTATION_REQUIREMENTS
                and bool(ticket.documentation_requirements)
            )
            or (
                token == TOKEN_IF_RISK_HIGH and ticket.risk_level in _RISK_AT_LEAST_HIGH
            )
        ):
            checks.append(RequiredCheck(column, required=True))
    if ticket.risk_level == RiskLevel.CRITICAL:
        checks.append(
            RequiredCheck(
                VerificationCheckType.SECURITY,
                required=False,
                note=SECURITY_DEFERRAL_NOTE,
            )
        )
    return tuple(checks)

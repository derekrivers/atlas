"""ATLAS-71: the rule-resolver returns the correct required-check set for
every ticket type and every conditional branch, and never raises.

Each behavioural assertion names the wrong answer it would catch:
documentation must be ABSENT when the ticket has no documentation_requirements;
human_approval must be ABSENT below high risk; SECURITY must appear ONLY for
critical risk and ONLY as required=False. The defensive "unknown type → no
matrix row" branch is exercised with a fabricated type so it is a real guard,
not dead code. A matrix-integrity test pins that every real TicketType has a
row and every cell token is recognised.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from atlas.core.enums import RiskLevel
from atlas.core.models import Ticket, TicketType, VerificationCheckType
from atlas.verification import RequiredCheck, required_checks
from atlas.verification.rules import (
    TOKEN_IF_DOCUMENTATION_REQUIREMENTS,
    TOKEN_IF_RISK_HIGH,
    TOKEN_REQUIRED,
    _load_matrix,
)

NOW = datetime(2026, 6, 28, tzinfo=UTC)
VCT = VerificationCheckType


def make_ticket(
    *,
    ticket_type: TicketType,
    risk_level: RiskLevel = RiskLevel.LOW,
    documentation_requirements: list[str] | None = None,
    **overrides: Any,
) -> Ticket:
    base: dict[str, Any] = {
        "id": uuid4(),
        "product_id": uuid4(),
        "key": "ATLAS-71",
        "title": "Verification rules",
        "objective": "Resolve required checks.",
        "context": "Phase 7 Verification Engine.",
        "status": "in_progress",
        "ticket_type": ticket_type,
        "risk_level": risk_level,
        "priority": 10,
        "documentation_requirements": documentation_requirements or [],
        "source_anchor": "docs/atlas/verification-engine.md#required-check-matrix",
        "created_by_type": "agent",
        "created_by_id": "claude",
        "created_at": NOW,
        "updated_at": NOW,
    }
    return Ticket(**base | overrides)


def types(checks: tuple[RequiredCheck, ...]) -> set[VerificationCheckType]:
    return {c.check_type for c in checks}


def required_types(checks: tuple[RequiredCheck, ...]) -> set[VerificationCheckType]:
    return {c.check_type for c in checks if c.required}


# --- per-ticket-type required sets (matrix rows) ---------------------------


def test_feature_low_risk_no_docs() -> None:
    checks = required_checks(make_ticket(ticket_type=TicketType.FEATURE))
    # tests, lint, acceptance_criteria, scope — and NOTHING else: documentation
    # is omitted (no doc requirements), human_approval omitted (low risk),
    # security omitted (not critical). Each absence is a named wrong answer.
    assert types(checks) == {VCT.TESTS, VCT.LINT, VCT.ACCEPTANCE_CRITERIA, VCT.SCOPE}
    assert VCT.DOCUMENTATION not in types(checks)
    assert VCT.HUMAN_APPROVAL not in types(checks)
    assert VCT.SECURITY not in types(checks)
    assert all(c.required for c in checks)


def test_bug_and_tech_debt_share_the_same_set() -> None:
    expected = {VCT.TESTS, VCT.LINT, VCT.ACCEPTANCE_CRITERIA, VCT.SCOPE}
    for ticket_type in (TicketType.BUG, TicketType.TECH_DEBT):
        checks = required_checks(make_ticket(ticket_type=ticket_type))
        assert types(checks) == expected, ticket_type
        # bug/tech_debt never require documentation or human_approval.
        assert VCT.DOCUMENTATION not in types(checks)
        assert VCT.HUMAN_APPROVAL not in types(checks)


def test_documentation_ticket_row() -> None:
    checks = required_checks(make_ticket(ticket_type=TicketType.DOCUMENTATION))
    # No tests, no scope; lint + acceptance_criteria + documentation (always).
    assert types(checks) == {VCT.LINT, VCT.ACCEPTANCE_CRITERIA, VCT.DOCUMENTATION}
    assert VCT.TESTS not in types(checks)
    assert VCT.SCOPE not in types(checks)


def test_spike_and_research_share_one_row() -> None:
    expected = {VCT.ACCEPTANCE_CRITERIA, VCT.DOCUMENTATION, VCT.HUMAN_APPROVAL}
    for ticket_type in (TicketType.SPIKE, TicketType.RESEARCH):
        checks = required_checks(make_ticket(ticket_type=ticket_type))
        # acceptance_criteria + documentation (findings, always required in v1)
        # + human_approval (always for this row). No tests, no lint, no scope.
        assert types(checks) == expected, ticket_type
        assert VCT.TESTS not in types(checks)
        assert VCT.LINT not in types(checks)
        assert VCT.SCOPE not in types(checks)


# --- conditional branch: documentation on documentation_requirements -------


def test_feature_documentation_required_only_when_requirements_present() -> None:
    without = required_checks(make_ticket(ticket_type=TicketType.FEATURE))
    with_docs = required_checks(
        make_ticket(
            ticket_type=TicketType.FEATURE,
            documentation_requirements=["docs/atlas/verification-engine.md"],
        )
    )
    # Wrong answer: documentation present regardless of requirements.
    assert VCT.DOCUMENTATION not in types(without)
    assert VCT.DOCUMENTATION in required_types(with_docs)


def test_infrastructure_documentation_conditional_too() -> None:
    with_docs = required_checks(
        make_ticket(
            ticket_type=TicketType.INFRASTRUCTURE,
            documentation_requirements=["docs/runbooks/local-development.md"],
        )
    )
    assert VCT.DOCUMENTATION in required_types(with_docs)
    without = required_checks(make_ticket(ticket_type=TicketType.INFRASTRUCTURE))
    assert VCT.DOCUMENTATION not in types(without)


# --- conditional branch: human_approval on risk ≥ high ---------------------


def test_human_approval_required_at_high_and_critical_only() -> None:
    # The named wrong answer: human_approval present at medium risk.
    medium = required_checks(
        make_ticket(ticket_type=TicketType.FEATURE, risk_level=RiskLevel.MEDIUM)
    )
    assert VCT.HUMAN_APPROVAL not in types(medium)

    for risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        checks = required_checks(
            make_ticket(ticket_type=TicketType.FEATURE, risk_level=risk)
        )
        assert VCT.HUMAN_APPROVAL in required_types(checks), risk


def test_low_risk_feature_has_no_human_approval() -> None:
    checks = required_checks(
        make_ticket(ticket_type=TicketType.FEATURE, risk_level=RiskLevel.LOW)
    )
    assert VCT.HUMAN_APPROVAL not in types(checks)


# --- OP-4 cross-cutting SECURITY rule --------------------------------------


def test_security_surfaced_only_at_critical_and_never_gates() -> None:
    for risk in (RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH):
        checks = required_checks(
            make_ticket(ticket_type=TicketType.FEATURE, risk_level=risk)
        )
        # Wrong answer: SECURITY surfaced below critical (e.g. at high).
        assert VCT.SECURITY not in types(checks), risk

    critical = required_checks(
        make_ticket(ticket_type=TicketType.FEATURE, risk_level=RiskLevel.CRITICAL)
    )
    security = [c for c in critical if c.check_type is VCT.SECURITY]
    assert len(security) == 1
    # The decisive guard: it is surfaced but NON-gating (required=False), with
    # a deferral note. A required=True security check would gate completion.
    assert security[0].required is False
    assert security[0].note is not None
    # And it is the only non-required entry — every other check gates.
    assert required_types(critical) == types(critical) - {VCT.SECURITY}


def test_security_applies_across_ticket_types_at_critical() -> None:
    # The rule is cross-cutting, not feature-only: a critical bug surfaces it
    # too, still non-gating.
    checks = required_checks(
        make_ticket(ticket_type=TicketType.BUG, risk_level=RiskLevel.CRITICAL)
    )
    assert VCT.SECURITY in types(checks)
    assert all(not c.required for c in checks if c.check_type is VCT.SECURITY)


# --- never raises; deterministic ordering ----------------------------------


def test_resolver_never_raises_for_any_type_and_risk() -> None:
    for ticket_type in TicketType:
        for risk in RiskLevel:
            for docs in ([], ["docs/x.md"]):
                # Must not raise for any valid Ticket (validation.py idiom).
                result = required_checks(
                    make_ticket(
                        ticket_type=ticket_type,
                        risk_level=risk,
                        documentation_requirements=docs,
                    )
                )
                assert isinstance(result, tuple)


def test_output_is_in_matrix_column_order() -> None:
    # Deterministic, column-faithful order regardless of YAML key order, with
    # SECURITY (the code-applied rule) last.
    checks = required_checks(
        make_ticket(
            ticket_type=TicketType.FEATURE,
            risk_level=RiskLevel.CRITICAL,
            documentation_requirements=["docs/x.md"],
        )
    )
    order = [c.check_type for c in checks]
    assert order == [
        VCT.TESTS,
        VCT.LINT,
        VCT.ACCEPTANCE_CRITERIA,
        VCT.DOCUMENTATION,
        VCT.SCOPE,
        VCT.HUMAN_APPROVAL,
        VCT.SECURITY,
    ]


# --- defensive branch: unknown / removed ticket type -----------------------


class _FakeType:
    """A stand-in for a ticket type with no matrix row (e.g. a hypothetical or
    removed TicketType). Carries the ``.value`` the resolver reads."""

    value = "no_such_ticket_type"


def test_unknown_ticket_type_yields_no_matrix_checks_and_never_raises() -> None:
    # Exercises the "no row → empty" guard as a REAL guard. A non-critical
    # unknown type resolves to no checks at all (no row, no security rule).
    ticket = make_ticket(ticket_type=TicketType.FEATURE)
    object.__setattr__(ticket, "ticket_type", _FakeType())
    result = required_checks(ticket)
    assert result == ()


def test_unknown_ticket_type_still_applies_security_at_critical() -> None:
    # The cross-cutting SECURITY rule is independent of the matrix row, so an
    # unknown critical type still surfaces it non-gating — and still no raise.
    ticket = make_ticket(ticket_type=TicketType.FEATURE, risk_level=RiskLevel.CRITICAL)
    object.__setattr__(ticket, "ticket_type", _FakeType())
    result = required_checks(ticket)
    assert types(result) == {VCT.SECURITY}
    assert result[0].required is False


# --- matrix integrity (catches a silent token typo / missing row) ----------


def test_every_ticket_type_has_a_matrix_row() -> None:
    matrix = _load_matrix()
    for ticket_type in TicketType:
        assert ticket_type.value in matrix, ticket_type


def test_every_matrix_token_is_recognised() -> None:
    known = {TOKEN_REQUIRED, TOKEN_IF_DOCUMENTATION_REQUIREMENTS, TOKEN_IF_RISK_HIGH}
    matrix = _load_matrix()
    for ticket_type, row in matrix.items():
        for check, token in row.items():
            assert token in known, (ticket_type, check, token)
            # Every cell's check key must be a real VerificationCheckType.
            assert check in {t.value for t in VerificationCheckType}, (
                ticket_type,
                check,
            )

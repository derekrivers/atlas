"""ATLAS-80: the two PURE helpers behind `atlas verify` — `parse_close_set`
(OP-C/R1, the close-set grammar) and `verification_checks_for` (OP-B/D2, the
PRVerification -> VerificationCheck mapping).

Both are pure and layer-clean (`atlas.core` + `atlas.verification` siblings +
stdlib only) and NEVER raise. Each behavioural assertion names the wrong answer
it would catch.

Criteria covered here:
- DoD 1 (parse_close_set): title `(ATLAS-NN)` primary; the `(#126)` PR number is
  NOT a key; body closing-keyword union; a bare mention is excluded;
  case-insensitive; deduped; order-preserving; empty/None -> ().
- DoD 2 (verification_checks_for): terminal -> completed_at=now; PENDING ->
  completed_at=None; evidence_ids tuple -> list; required/check_type/status
  carried; ids from new_id.
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count
from uuid import UUID, uuid4

from atlas.core.enums import EvidenceStatus
from atlas.core.models import VerificationCheckType
from atlas.verification import (
    CheckOutcome,
    PRVerification,
    TicketVerification,
    parse_close_set,
    verification_checks_for,
)

NOW = datetime(2026, 6, 28, tzinfo=UTC)
ES = EvidenceStatus
VT = VerificationCheckType


# --- parse_close_set (DoD 1) ------------------------------------------------


def test_title_atlas_key_is_captured_and_pr_number_is_not() -> None:
    """A real-shaped title -> the ATLAS key only; the (#126) PR number must NOT
    be captured. Wrong answer: {ATLAS-77, ...126...} — the # number leaking in
    as a key because the ATLAS- prefix was not required as the disambiguator."""
    assert parse_close_set("feat(at7): … (ATLAS-77) (#126)", None) == ("ATLAS-77",)


def test_title_multiple_keys_case_and_whitespace_normalised() -> None:
    """Multiple keys with case/whitespace variants -> canonical uppercase, in
    title order, deduped. Wrong answer: lowercase 'atlas-72' surviving (so a
    later get_by_key misses), or ATLAS-72 duplicated."""
    title = "fix:  atlas-72 ,ATLAS-73 and Atlas-72 again"
    assert parse_close_set(title, None) == ("ATLAS-72", "ATLAS-73")


def test_body_closing_keyword_unioned_but_bare_mention_excluded() -> None:
    """The body contributes only closing-keyword matches: 'Closes ATLAS-80' is
    in; a bare '## ATLAS-99' heading mention is NOT. Wrong answer: ATLAS-99
    captured from the bare heading (the body path must require a keyword)."""
    body = "## ATLAS-99 — design notes\n\nCloses ATLAS-80\nsee ATLAS-91 for context"
    assert parse_close_set("chore: cleanup", body) == ("ATLAS-80",)


def test_title_and_body_union_order_title_first() -> None:
    """Title keys come first (title order), then body keys not already seen.
    Wrong answer: body key ordered before the title key, or a title key the body
    also closes duplicated."""
    keys = parse_close_set("feat: thing (ATLAS-10)", "Closes ATLAS-10\nfixes ATLAS-11")
    assert keys == ("ATLAS-10", "ATLAS-11")


def test_empty_and_none_inputs_yield_empty_tuple() -> None:
    """A None/empty title and body -> (). Wrong answer: a raise on None, or a
    non-empty tuple from nothing."""
    assert parse_close_set(None, None) == ()
    assert parse_close_set("", "") == ()
    assert parse_close_set("no key here", "still nothing") == ()


# --- verification_checks_for (DoD 2) ----------------------------------------


def _outcome(
    status: EvidenceStatus,
    *,
    check_type: VerificationCheckType = VT.TESTS,
    required: bool = True,
    evidence_ids: tuple[UUID, ...] = (),
) -> CheckOutcome:
    return CheckOutcome(
        check_type=check_type,
        required=required,
        status=status,
        evidence_ids=evidence_ids,
        reason=f"{check_type.value}: {status.value}",
    )


def test_terminal_status_stamps_completed_at_open_status_does_not() -> None:
    """PASSED/FAILED/NOT_APPLICABLE -> completed_at=now (terminal); PENDING and
    WARNING -> completed_at=None (still open). Wrong answer: completed_at set on
    a PENDING check (the named milestone footgun)."""
    ticket_id = uuid4()
    pr = PRVerification(
        head_commit="c0ffee",
        status=ES.PENDING,
        tickets=(
            TicketVerification(
                ticket_id=ticket_id,
                status=ES.PENDING,
                checks=(
                    _outcome(ES.PASSED, check_type=VT.TESTS),
                    _outcome(ES.FAILED, check_type=VT.LINT),
                    _outcome(ES.NOT_APPLICABLE, check_type=VT.SECURITY, required=False),
                    _outcome(ES.PENDING, check_type=VT.ACCEPTANCE_CRITERIA),
                    _outcome(ES.WARNING, check_type=VT.SCOPE),
                ),
            ),
        ),
    )
    rows = verification_checks_for(pr, now=NOW, new_id=uuid4)

    by_type = {row.check_type: row for row in rows}
    assert by_type[VT.TESTS].completed_at == NOW
    assert by_type[VT.LINT].completed_at == NOW
    assert by_type[VT.SECURITY].completed_at == NOW
    assert by_type[VT.ACCEPTANCE_CRITERIA].completed_at is None
    assert by_type[VT.SCOPE].completed_at is None


def test_fields_carried_through_and_ids_from_factory() -> None:
    """evidence_ids tuple -> list; required/check_type/status/ticket_id/summary
    carried; ids come from new_id (not random). Wrong answer: evidence_ids
    dropped, or ids not sourced from the injected factory."""
    ticket_id = uuid4()
    eids = (uuid4(), uuid4())
    pr = PRVerification(
        head_commit="c0ffee",
        status=ES.PASSED,
        tickets=(
            TicketVerification(
                ticket_id=ticket_id,
                status=ES.PASSED,
                checks=(
                    _outcome(
                        ES.PASSED,
                        check_type=VT.ACCEPTANCE_CRITERIA,
                        required=True,
                        evidence_ids=eids,
                    ),
                ),
            ),
        ),
    )
    ids = (UUID(int=n) for n in count(1))
    rows = verification_checks_for(pr, now=NOW, new_id=lambda: next(ids))

    assert len(rows) == 1
    row = rows[0]
    assert row.id == UUID(int=1)  # from the injected factory, deterministically
    assert row.ticket_id == ticket_id
    assert row.check_type == VT.ACCEPTANCE_CRITERIA
    assert row.status == ES.PASSED
    assert row.required is True
    assert row.evidence_ids == list(eids)  # tuple carried through as a list
    assert isinstance(row.evidence_ids, list)
    assert row.summary == "acceptance_criteria: passed"
    assert row.created_at == NOW


def test_rows_span_every_ticket_and_check_in_order() -> None:
    """One row per (ticket, check), in ticket-then-check order. Wrong answer: a
    ticket or a check dropped, so the persisted count is short."""
    t1, t2 = uuid4(), uuid4()
    pr = PRVerification(
        head_commit="c0ffee",
        status=ES.PENDING,
        tickets=(
            TicketVerification(
                ticket_id=t1,
                status=ES.PASSED,
                checks=(_outcome(ES.PASSED), _outcome(ES.PASSED, check_type=VT.LINT)),
            ),
            TicketVerification(
                ticket_id=t2,
                status=ES.PENDING,
                checks=(_outcome(ES.PENDING, check_type=VT.ACCEPTANCE_CRITERIA),),
            ),
        ),
    )
    rows = verification_checks_for(pr, now=NOW, new_id=uuid4)
    assert [r.ticket_id for r in rows] == [t1, t1, t2]
    assert [r.check_type for r in rows] == [VT.TESTS, VT.LINT, VT.ACCEPTANCE_CRITERIA]


def test_empty_pr_maps_to_no_rows() -> None:
    """An empty close-set PRVerification -> no rows (never raises). Wrong answer:
    a synthesised row for a PR with no tickets."""
    pr = PRVerification(head_commit="c0ffee", status=ES.PENDING, tickets=())
    assert verification_checks_for(pr, now=NOW, new_id=uuid4) == []


# --- DoD 9: purity / layer-cleanliness of the helpers module ----------------


def test_reports_module_imports_no_io_layer() -> None:
    """The pure helpers live below the cli: reports.py must not import
    github/storage/cli (only atlas.core + atlas.verification siblings + stdlib).
    Wrong answer: an `import atlas.storage` creeping in, which lint-imports would
    also catch but this names the boundary explicitly."""
    import ast
    from pathlib import Path

    import atlas.verification.reports as reports

    source = Path(reports.__file__).read_text(encoding="utf-8")
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)

    forbidden = ("atlas.github", "atlas.storage", "atlas.cli", "atlas.pm")
    assert not [m for m in modules if m.startswith(forbidden)]


# --- ATLAS-160: the trailing key boundary (seeded red first, per B011) --------


def test_meta_suffix_not_captured_as_real_key() -> None:
    assert 1 == 2

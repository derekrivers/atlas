"""ATLAS-133 (OP-3.2): the `atlas confirm --pr N --repo OWNER/REPO` CLI — the
interactive capture of the operator's human-tier confirmations for a PR.

Drives ``_confirm_command(args, database=db, github_client=fake, prompts=scripted,
now=NOW, new_id=…)`` against an in-memory SQLite database, a ``FakeGitHubClient``
replaying a PR object (head.sha + title/body) and a changed-file list, and a
scripted :class:`ConfirmPrompts` — no network, no TTY, no secrets. We call
``_confirm_command`` directly (not ``main``) because the prompt seam, the clock,
and the uuid factory are injected there; production builds them from stdin / the
environment.

confirm is the WRITE path OP-3.1 built the foundation for: it resolves the PR
exactly as `verify` does (verify untouched), calls OP-3.1's ``pending_capture``
per ticket, routes each operator ruling to the matching OP-3.1 builder, and
persists the resulting human-tier MANUAL_APPROVAL Evidence — the records that
finally let a real ticket reach a PASSED verdict. These tests prove that WIRING;
the record shapes and the verdict math are covered by the capture/evaluator unit
tests.

Each assertion names the wrong answer it catches. Criteria AC-1..AC-10:
- AC-1 (spine): a full confirm session → the ticket's acceptance + scope (+ human)
  verdicts PASS when re-evaluated at the same head commit C.
- AC-2: a scope "fail" persists a FAILED decision -> evaluate_scope FAILED at C.
- AC-3: skipping every item writes nothing and exits EXIT_OK; items stay pending.
- AC-4: operator identity from --operator, else ATLAS_OPERATOR_ID, else refuse.
- AC-5: every record is HUMAN-tier and pinned to C (and survives EvidenceRepo.add).
- AC-6: only still-pending items are prompted (inherits OP-3.1's gating).
- AC-7: no injected prompts + no TTY -> refuse, write nothing.
- AC-8: bad --repo / a GitHubAPIError / a cold database -> clean EXIT_PRECONDITION.
- AC-9: records only — no VerificationCheck rows, no ticket transition.
- AC-10: a multi-ticket close-set captures each ticket's own pending set; an
  unknown key is reported, not fatal.
"""

from __future__ import annotations

import io
import itertools
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import pytest
from github_fakes import FakeGitHubClient
from test_models_validation import product_kwargs

from atlas.cli import EXIT_OK, EXIT_PRECONDITION, _confirm_command, build_parser
from atlas.core.enums import ActorType, EvidenceStatus, RiskLevel
from atlas.core.models import Evidence, EvidenceType, Product, VerificationCheckType
from atlas.core.models.ticket import Ticket, TicketStatus, TicketType
from atlas.storage import (
    Database,
    EvidenceRepo,
    ProductRepo,
    TicketRepo,
    VerificationCheckRepo,
)
from atlas.verification import (
    acceptance_criterion_hash,
    evaluate_acceptance_criteria,
    evaluate_scope,
    evaluate_ticket,
)
from atlas.verification.scope_check import SCOPE_DECISION_PATH_KEY

NOW = datetime(2026, 6, 29, tzinfo=UTC)
HEAD = "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b"
OTHER_COMMIT = "0000000000000000000000000000000000000000"
PR_NUMBER = 133
REPO = "atlas/atlas"

ES = EvidenceStatus
ET = EvidenceType
VT = VerificationCheckType

AC = "The feature does the thing"
ANCHOR_DOC = "docs/foo.md"
IN_SCOPE_SRC = "src/thing.py"
OUT_OF_SCOPE = "src/other.py"


# --- fixtures and harness ----------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    ProductRepo(database).add(Product(**product_kwargs()))
    return database


def product_id(db: Database) -> UUID:
    product = ProductRepo(db).get_by_key("ATLAS")
    assert product is not None
    return product.id


def make_ticket(
    db: Database,
    *,
    key: str,
    ticket_type: TicketType = TicketType.FEATURE,
    risk_level: RiskLevel = RiskLevel.LOW,
    acceptance_criteria: list[str] | None = None,
    relevant_docs: list[str] | None = None,
    persist: bool = True,
) -> Ticket:
    ticket = Ticket(
        id=uuid4(),
        product_id=product_id(db),
        epic_id=None,
        key=key,
        title="t",
        objective="o",
        context="c",
        status=TicketStatus.REVIEW_REQUIRED,
        ticket_type=ticket_type,
        risk_level=risk_level,
        priority=1,
        relevant_docs=[IN_SCOPE_SRC] if relevant_docs is None else relevant_docs,
        acceptance_criteria=[AC]
        if acceptance_criteria is None
        else acceptance_criteria,
        documentation_requirements=[],
        source_anchor=f"{ANCHOR_DOC}#section",
        created_by_type=ActorType.HUMAN,
        created_by_id="operator",
        created_at=NOW,
        updated_at=NOW,
    )
    if persist:
        TicketRepo(db).add(ticket)
    return ticket


class ScriptedPrompts:
    """A no-TTY :class:`ConfirmPrompts` double replaying scripted rulings.

    Each answer is a fixed value or a callable of the prompt argument, so a test
    can vary by criterion / path. Every call is recorded so a test can assert
    WHICH items were prompted (AC-6: a confirmed item must not be re-prompted)."""

    def __init__(
        self,
        *,
        acceptance: bool | Callable[[str], bool] = True,
        scope: (Literal["waive", "fail", "skip"] | Callable[[str], str]) = "waive",
        approval: Literal["approve", "reject", "skip"] = "approve",
    ) -> None:
        self._acceptance = acceptance
        self._scope = scope
        self._approval = approval
        self.acceptance_calls: list[str] = []
        self.scope_calls: list[str] = []
        self.approval_calls = 0

    def acceptance(self, criterion: str) -> bool:
        self.acceptance_calls.append(criterion)
        answer = self._acceptance
        return answer(criterion) if callable(answer) else answer

    def scope(self, path: str) -> Literal["waive", "fail", "skip"]:
        self.scope_calls.append(path)
        answer = self._scope
        resolved = answer(path) if callable(answer) else answer
        assert resolved in ("waive", "fail", "skip")
        return resolved  # type: ignore[return-value]

    def approval(self) -> Literal["approve", "reject", "skip"]:
        self.approval_calls += 1
        return self._approval


def make_ids() -> Callable[[], UUID]:
    """A deterministic uuid factory: distinct, ordered ids for the records."""
    counter = itertools.count(1)
    return lambda: UUID(int=next(counter))


def fake(
    *,
    title: str = f"feat: do the thing (ATLAS-{PR_NUMBER}) (#{PR_NUMBER})",
    body: str | None = None,
    files: list[str] | None = None,
    with_pr: bool = True,
    head: str = HEAD,
) -> FakeGitHubClient:
    names = [OUT_OF_SCOPE] if files is None else files
    pr_files = [{"filename": f} for f in names]
    pull_request = (
        {"head": {"sha": head}, "title": title, "body": body} if with_pr else None
    )
    return FakeGitHubClient(pr_files=pr_files, pull_request=pull_request)


def run_confirm(
    db: Database | None,
    client: FakeGitHubClient,
    prompts: ScriptedPrompts | None,
    *,
    operator: str | None = "alice",
    tickets: str | None = None,
    now: datetime = NOW,
    new_id: Callable[[], UUID] | None = None,
) -> int:
    argv = ["confirm", "--pr", str(PR_NUMBER), "--repo", REPO]
    if operator is not None:
        argv += ["--operator", operator]
    if tickets is not None:
        argv += ["--tickets", tickets]
    args = build_parser().parse_args(argv)
    return _confirm_command(
        args,
        database=db,
        github_client=client,
        prompts=prompts,
        now=now,
        new_id=new_id or make_ids(),
    )


def seed_evidence(db: Database, *records: Evidence) -> None:
    repo = EvidenceRepo(db)
    for record in records:
        repo.add(record)


def accept_confirmation(
    db: Database, ticket_id: UUID, criterion: str = AC, *, commit: str = HEAD
) -> Evidence:
    return Evidence(
        id=uuid4(),
        product_id=product_id(db),
        ticket_id=ticket_id,
        evidence_type=ET.MANUAL_APPROVAL,
        status=ES.PASSED,
        summary="pre-confirmed",
        commit_sha=commit,
        raw_payload={"acceptance_criterion_hash": acceptance_criterion_hash(criterion)},
        created_by_type=ActorType.HUMAN,
        created_by_id="operator",
        created_at=NOW,
    )


def status_of(
    ticket: Ticket, db: Database, check: VerificationCheckType
) -> EvidenceStatus:
    tv = evaluate_ticket(
        ticket,
        pr_files=[OUT_OF_SCOPE],
        head_commit=HEAD,
        evidence=EvidenceRepo(db).list(),
    )
    return next(c.status for c in tv.checks if c.check_type == check)


# --- AC-1: the spine — a full confirm session reaches PASSED -----------------


def test_confirm_round_trip_reaches_passed(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    """Confirm every criterion, waive the out-of-scope file, approve the PR; then
    re-evaluate at the SAME C -> acceptance / scope / human_approval all PASS.
    Wrong answer: a "confirm" routed to no builder leaves them PENDING."""
    ticket = make_ticket(db, key=f"ATLAS-{PR_NUMBER}", risk_level=RiskLevel.HIGH)

    code = run_confirm(db, fake(), ScriptedPrompts())
    assert code == EXIT_OK

    assert status_of(ticket, db, VT.ACCEPTANCE_CRITERIA) == ES.PASSED
    assert status_of(ticket, db, VT.SCOPE) == ES.PASSED
    assert status_of(ticket, db, VT.HUMAN_APPROVAL) == ES.PASSED

    out = capsys.readouterr().out
    assert HEAD in out and "Recorded 3" in out


# --- AC-2: a scope "fail" records a FAILED decision --------------------------


def test_scope_fail_records_failed(db: Database) -> None:
    """Scripting scope "fail" persists build_scope_decision(waive=False) ->
    evaluate_scope FAILED at C. Wrong answer: "fail" mapped to a waive (PASSED)."""
    ticket = make_ticket(db, key="ATLAS-300", ticket_type=TicketType.BUG)

    code = run_confirm(db, fake(), ScriptedPrompts(scope="fail"), tickets="ATLAS-300")
    assert code == EXIT_OK

    evaluation = evaluate_scope(
        [OUT_OF_SCOPE],
        relevant_docs=ticket.relevant_docs,
        source_anchor=ticket.source_anchor,
        ticket_id=ticket.id,
        head_commit=HEAD,
        evidence=EvidenceRepo(db).list(),
    )
    assert evaluation.status == ES.FAILED


# --- AC-3: skipping every item writes nothing, exits EXIT_OK -----------------


def test_skip_writes_nothing_and_items_stay_pending(db: Database) -> None:
    """Skip acceptance, scope, and approval -> zero evidence, EXIT_OK, and the
    items remain pending (a confirm-all re-run still records them). Wrong answer:
    a record written on skip."""
    make_ticket(db, key="ATLAS-301", risk_level=RiskLevel.HIGH)

    code = run_confirm(
        db,
        fake(),
        ScriptedPrompts(acceptance=False, scope="skip", approval="skip"),
        tickets="ATLAS-301",
    )
    assert code == EXIT_OK
    assert EvidenceRepo(db).list() == []

    # The items were never satisfied: a follow-up confirm-all session records them.
    again = ScriptedPrompts()
    assert run_confirm(db, fake(), again, tickets="ATLAS-301") == EXIT_OK
    assert len(EvidenceRepo(db).list()) == 3
    assert again.acceptance_calls == [AC]  # still prompted -> still pending


# --- AC-4: operator identity -------------------------------------------------


def test_operator_from_flag_is_recorded(db: Database) -> None:
    """--operator alice -> records carry created_by_id == "alice". Wrong answer: a
    hardcoded id."""
    make_ticket(db, key="ATLAS-302", ticket_type=TicketType.BUG)
    run_confirm(db, fake(), ScriptedPrompts(), operator="alice", tickets="ATLAS-302")
    records = EvidenceRepo(db).list()
    assert records and all(r.created_by_id == "alice" for r in records)


def test_operator_from_env_when_no_flag(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no --operator, ATLAS_OPERATOR_ID is used."""
    monkeypatch.setenv("ATLAS_OPERATOR_ID", "bob")
    make_ticket(db, key="ATLAS-303", ticket_type=TicketType.BUG)
    run_confirm(db, fake(), ScriptedPrompts(), operator=None, tickets="ATLAS-303")
    records = EvidenceRepo(db).list()
    assert records and all(r.created_by_id == "bob" for r in records)


def test_missing_operator_refuses_and_writes_nothing(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither --operator nor ATLAS_OPERATOR_ID -> EXIT_PRECONDITION, nothing
    written (no anonymous human-tier writes). Wrong answer: a defaulted id."""
    monkeypatch.delenv("ATLAS_OPERATOR_ID", raising=False)
    make_ticket(db, key="ATLAS-304", ticket_type=TicketType.BUG)
    code = run_confirm(
        db, fake(), ScriptedPrompts(), operator=None, tickets="ATLAS-304"
    )
    assert code == EXIT_PRECONDITION
    assert EvidenceRepo(db).list() == []


# --- AC-5: human tier + pinned to C ------------------------------------------


def test_records_are_human_tier_and_pinned_to_head(db: Database) -> None:
    """Every persisted confirmation is HUMAN-tier and pinned to C, and is accepted
    by EvidenceRepo.add (PASSED not capped). Wrong answer: building at a different
    commit (the round-trip would then never reach PASSED)."""
    make_ticket(db, key="ATLAS-305", risk_level=RiskLevel.HIGH)
    run_confirm(db, fake(), ScriptedPrompts(), tickets="ATLAS-305")

    records = EvidenceRepo(db).list()
    assert len(records) == 3
    for r in records:
        assert r.created_by_type == ActorType.HUMAN
        assert r.commit_sha == HEAD
        assert r.evidence_type == ET.MANUAL_APPROVAL
        assert r.status == ES.PASSED  # human PASSED survives EvidenceRepo.add


def test_building_at_a_stale_commit_would_not_pass(db: Database) -> None:
    """Guard for AC-5's red: a confirmation pinned to a commit other than C does
    NOT satisfy the acceptance evaluator at C — proving the pin is load-bearing."""
    ticket = make_ticket(db, key="ATLAS-306", ticket_type=TicketType.BUG)
    seed_evidence(db, accept_confirmation(db, ticket.id, commit=OTHER_COMMIT))
    evaluation = evaluate_acceptance_criteria(
        [AC], ticket_id=ticket.id, head_commit=HEAD, evidence=EvidenceRepo(db).list()
    )
    assert evaluation.status != ES.PASSED


# --- AC-6: only still-pending items are prompted -----------------------------


def test_already_confirmed_criterion_is_not_prompted(db: Database) -> None:
    """A criterion already confirmed at C is not re-prompted. Wrong answer:
    prompting unconditionally instead of from pending_capture."""
    ticket = make_ticket(db, key="ATLAS-307", ticket_type=TicketType.BUG)
    seed_evidence(db, accept_confirmation(db, ticket.id))
    prompts = ScriptedPrompts()
    run_confirm(db, fake(), prompts, tickets="ATLAS-307")
    assert prompts.acceptance_calls == []


def test_documentation_ticket_prompts_no_scope_files(db: Database) -> None:
    """A `documentation` ticket does not require the scope check, so no scope file
    is prompted even when the PR touches an out-of-scope path (inherits OP-3.1's
    gate). Wrong answer: prompting scope unconditionally."""
    make_ticket(db, key="ATLAS-308", ticket_type=TicketType.DOCUMENTATION)
    prompts = ScriptedPrompts()
    run_confirm(db, fake(files=[OUT_OF_SCOPE]), prompts, tickets="ATLAS-308")
    assert prompts.scope_calls == []


# --- AC-7: no TTY + no prompts -> refuse --------------------------------------


def test_no_tty_no_prompts_refuses(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With prompts=None and a non-TTY stdin, refuse (EXIT_PRECONDITION) and write
    nothing — never auto-confirm. Wrong answer: falling through to auto-confirm."""
    monkeypatch.setattr("sys.stdin", io.StringIO())  # isatty() -> False
    make_ticket(db, key="ATLAS-309", ticket_type=TicketType.BUG)
    code = run_confirm(db, fake(), None)
    assert code == EXIT_PRECONDITION
    assert EvidenceRepo(db).list() == []


# --- AC-8: setup failures -> clean EXIT_PRECONDITION, no writes ---------------


def test_bad_repo_is_clean_precondition(db: Database) -> None:
    make_ticket(db, key="ATLAS-310", ticket_type=TicketType.BUG)
    args = build_parser().parse_args(
        ["confirm", "--pr", str(PR_NUMBER), "--repo", "not-a-slug", "--operator", "a"]
    )
    code = _confirm_command(
        args, database=db, github_client=fake(), prompts=ScriptedPrompts()
    )
    assert code == EXIT_PRECONDITION
    assert EvidenceRepo(db).list() == []


def test_github_api_error_is_clean_precondition(db: Database) -> None:
    """An unseeded PR makes FakeGitHubClient raise GitHubAPIError (the 404 path)."""
    make_ticket(db, key="ATLAS-311", ticket_type=TicketType.BUG)
    code = run_confirm(db, fake(with_pr=False), ScriptedPrompts())
    assert code == EXIT_PRECONDITION
    assert EvidenceRepo(db).list() == []


def test_cold_database_is_clean_precondition(tmp_path: Path) -> None:
    """A never-migrated database raises OperationalError on first repo access;
    the guard maps it to a clean precondition. Wrong answer: EXIT_OK on a cold
    db."""
    cold = Database(f"sqlite:///{tmp_path}/cold.db")  # no create_all
    code = run_confirm(cold, fake(), ScriptedPrompts())
    assert code == EXIT_PRECONDITION


# --- AC-9: records only — no verdicts, no transitions ------------------------


def test_records_only_no_side_effects(db: Database) -> None:
    """A full session writes ONLY MANUAL_APPROVAL Evidence: no VerificationCheck
    rows, and the ticket status is unchanged. Wrong answer: evaluate_pr + persist
    inside confirm."""
    ticket = make_ticket(db, key="ATLAS-312", risk_level=RiskLevel.HIGH)
    run_confirm(db, fake(), ScriptedPrompts(), tickets="ATLAS-312")

    assert VerificationCheckRepo(db).list() == []
    reloaded = TicketRepo(db).get_by_key(ticket.key)
    assert reloaded is not None and reloaded.status == TicketStatus.REVIEW_REQUIRED
    assert all(r.evidence_type == ET.MANUAL_APPROVAL for r in EvidenceRepo(db).list())


# --- AC-10: a multi-ticket close-set ----------------------------------------


def test_multi_ticket_close_set_captures_each_pending_set(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    """A PR closing two tickets captures each ticket's OWN pending set (scope is
    per-ticket via relevant_docs/source_anchor); an unknown key is reported, not
    fatal. Wrong answer: one ticket's scope set applied to both."""
    file_a, file_b = "src/a.py", "src/b.py"
    ticket_a = make_ticket(
        db, key="ATLAS-320", ticket_type=TicketType.BUG, relevant_docs=[file_b]
    )
    ticket_b = make_ticket(
        db, key="ATLAS-321", ticket_type=TicketType.BUG, relevant_docs=[file_a]
    )

    code = run_confirm(
        db,
        fake(files=[file_a, file_b]),
        ScriptedPrompts(),
        tickets="ATLAS-320,ATLAS-321,ATLAS-999",
    )
    assert code == EXIT_OK

    scope_by_ticket = {
        r.ticket_id: r.raw_payload[SCOPE_DECISION_PATH_KEY]
        for r in EvidenceRepo(db).list()
        if SCOPE_DECISION_PATH_KEY in r.raw_payload
    }
    # file_a is out-of-scope for A (A owns file_b) and file_b for B — each ticket's
    # own derivation, not a shared set.
    assert scope_by_ticket[ticket_a.id] == file_a
    assert scope_by_ticket[ticket_b.id] == file_b

    out = capsys.readouterr().out
    assert "ATLAS-999" in out  # unknown key reported, not fatal

"""ATLAS-80: the `atlas verify --pr N --repo OWNER/REPO` CLI (verify + record +
report). Drives ``main(["verify", ...], database=db, github_client=fake)`` against
an in-memory SQLite database and a ``FakeGitHubClient`` replaying a PR object
(head.sha + title/body) and a changed-file list — no network.

verify is the seam where the pure engine does real I/O but stays NON-interactive
and writes NO Evidence (OP-A): acceptance/scope/human checks report PENDING until
the OP-3 follow-on lands. These tests prove the WIRING — close-set resolution,
evidence load at C, the pure ``evaluate_pr``, append-only persistence, the report
shapes, and the clean-error exits — not the verdict math (covered by the
verification unit tests).

Each behavioural assertion names the wrong answer it catches. Criteria:
- DoD 3: happy path — system-tier TEST/LINT at C -> machine checks PASSED;
  rows persisted (read back).
- DoD 4 (MILESTONE): AGENT-tier TEST only -> TESTS/verdict PENDING; swapping it
  to SYSTEM-tier at C -> PASSED. "No system evidence = no completion" via the CLI.
- DoD 5: acceptance/scope/human report PENDING; the report states the OP-A note.
- DoD 6: --json emits the serialised PRVerification with the documented shape.
- DoD 7: empty close-set / unknown key / empty PR files -> a clean report, no
  traceback.
- DoD 8: append-only — a second run appends a second row-set, mutating none.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from github_fakes import FakeGitHubClient

from atlas.cli import EXIT_OK, EXIT_PRECONDITION, main
from atlas.core.enums import ActorType, EvidenceStatus, RiskLevel
from atlas.core.models import Evidence, EvidenceType, VerificationCheckType
from atlas.core.models.ticket import Ticket, TicketStatus, TicketType
from atlas.storage import Database, EvidenceRepo, TicketRepo, VerificationCheckRepo
from atlas.verification import acceptance_criterion_hash

NOW = datetime(2026, 6, 28, tzinfo=UTC)
HEAD = "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b"
PR_NUMBER = 126
REPO = "atlas/atlas"

ES = EvidenceStatus
ET = EvidenceType
VT = VerificationCheckType

# A bug/low ticket -> required checks TESTS, LINT, ACCEPTANCE, SCOPE (no docs, no
# human_approval, no security): the cleanest shape for the milestone, whose only
# human-tier requirement is acceptance.
AC = "The feature does the thing"
IN_SCOPE_DOC = "docs/foo.md"
IN_SCOPE_SRC = "src/thing.py"


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def make_ticket(
    *,
    key: str,
    acceptance_criteria: list[str] | None = None,
) -> Ticket:
    return Ticket(
        id=uuid4(),
        product_id=uuid4(),
        epic_id=None,
        key=key,
        title="t",
        objective="o",
        context="c",
        status=TicketStatus.REVIEW_REQUIRED,
        ticket_type=TicketType.BUG,
        risk_level=RiskLevel.LOW,
        priority=1,
        relevant_docs=[IN_SCOPE_SRC],
        acceptance_criteria=(
            [AC] if acceptance_criteria is None else acceptance_criteria
        ),
        documentation_requirements=[],
        source_anchor=f"{IN_SCOPE_DOC}#section",
        created_by_type=ActorType.HUMAN,
        created_by_id="operator",
        created_at=NOW,
        updated_at=NOW,
    )


def _evidence(
    evidence_type: EvidenceType,
    *,
    status: EvidenceStatus,
    created_by_type: ActorType,
    ticket_id: UUID | None,
    commit_sha: str | None = HEAD,
    raw_payload: dict[str, Any] | None = None,
) -> Evidence:
    is_system = created_by_type == ActorType.SYSTEM
    return Evidence(
        id=uuid4(),
        product_id=uuid4(),
        ticket_id=ticket_id,
        evidence_type=evidence_type,
        status=status,
        summary="e",
        commit_sha=commit_sha,
        external_run_id="run-1" if is_system else None,
        payload_hash="h" if is_system else None,
        raw_payload=raw_payload or {},
        created_by_type=created_by_type,
        created_by_id="ci" if is_system else "operator",
        created_at=NOW,
    )


def sys_test(commit: str = HEAD) -> Evidence:
    return _evidence(
        ET.TEST_RESULT,
        status=ES.PASSED,
        created_by_type=ActorType.SYSTEM,
        ticket_id=None,
        commit_sha=commit,
    )


def sys_lint(commit: str = HEAD) -> Evidence:
    return _evidence(
        ET.LINT_RESULT,
        status=ES.PASSED,
        created_by_type=ActorType.SYSTEM,
        ticket_id=None,
        commit_sha=commit,
    )


def agent_test() -> Evidence:
    # Agent-tier is capped at PENDING by EvidenceRepo.add (ADR-0008).
    return _evidence(
        ET.TEST_RESULT,
        status=ES.PENDING,
        created_by_type=ActorType.AGENT,
        ticket_id=None,
    )


def accept_confirmation(ticket_id: UUID, criterion: str = AC) -> Evidence:
    return _evidence(
        ET.MANUAL_APPROVAL,
        status=ES.PASSED,
        created_by_type=ActorType.HUMAN,
        ticket_id=ticket_id,
        raw_payload={"acceptance_criterion_hash": acceptance_criterion_hash(criterion)},
    )


def fake(
    *,
    title: str = f"feat: fix the thing (ATLAS-200) (#{PR_NUMBER})",
    body: str | None = None,
    files: list[str] | None = None,
    with_pr: bool = True,
    merged: bool = False,
) -> FakeGitHubClient:
    names = [IN_SCOPE_DOC, IN_SCOPE_SRC] if files is None else files
    pr_files = [{"filename": f} for f in names]
    pull_request = (
        {"head": {"sha": HEAD}, "title": title, "body": body, "merged": merged}
        if with_pr
        else None
    )
    return FakeGitHubClient(pr_files=pr_files, pull_request=pull_request)


def run_verify(db: Database, client: FakeGitHubClient, *extra: str) -> int:
    return main(
        ["verify", "--pr", str(PR_NUMBER), "--repo", REPO, *extra],
        database=db,
        github_client=client,
    )


def seed_evidence(db: Database, *records: Evidence) -> None:
    repo = EvidenceRepo(db)
    for record in records:
        repo.add(record)


# --- DoD 3: happy path, machine checks PASSED, rows persisted ----------------


def test_happy_path_machine_checks_pass_and_rows_persist(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    ticket = make_ticket(key="ATLAS-200")
    TicketRepo(db).add(ticket)
    seed_evidence(db, sys_test(), sys_lint())

    code = run_verify(db, fake())
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "ATLAS-200" in out and HEAD in out

    rows = VerificationCheckRepo(db).list()
    by_type = {r.check_type: r for r in rows if r.ticket_id == ticket.id}
    # Machine checks PASSED at C — wrong answer: PENDING, i.e. the head SHA never
    # reached the machine evaluator (a wiring break).
    assert by_type[VT.TESTS].status == ES.PASSED
    assert by_type[VT.LINT].status == ES.PASSED
    assert by_type[VT.TESTS].completed_at is not None  # terminal -> stamped
    # The expected four required checks for a bug were each persisted.
    assert set(by_type) == {VT.TESTS, VT.LINT, VT.ACCEPTANCE_CRITERIA, VT.SCOPE}


# --- DoD 4: the MILESTONE — no system evidence = no completion ---------------


def test_milestone_agent_tier_tests_stay_pending_then_system_passes(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    """A ticket fully satisfiable EXCEPT tests. With only AGENT-tier TEST evidence
    the TESTS check and the ticket/PR verdict are PENDING; swapping that evidence
    to SYSTEM-tier at C flips them PASSED. Wrong answer: PASSED while only the
    agent claim exists (an agent manufacturing completion)."""
    ticket = make_ticket(key="ATLAS-201")
    TicketRepo(db).add(ticket)
    # Everything the ticket needs to PASS except tests: lint (system), acceptance
    # (human confirmation at C), scope (all PR files in scope).
    seed_evidence(db, sys_lint(), accept_confirmation(ticket.id), agent_test())

    title = "feat: thing (ATLAS-201) (#126)"
    code = run_verify(db, fake(title=title), "--json")
    assert code == EXIT_OK  # report produced regardless of verdict (R2)
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "pending"  # PR verdict PENDING
    tv = payload["tickets"][0]
    tests = next(c for c in tv["checks"] if c["check_type"] == "tests")
    assert tests["status"] == "pending"  # agent claim ignored for a machine check
    assert tv["status"] == "pending"

    # Now CI lands system-tier TEST evidence at the head commit.
    seed_evidence(db, sys_test())
    code = run_verify(db, fake(title=title), "--json")
    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "passed"  # the same ticket now completes
    tv = payload["tickets"][0]
    assert tv["status"] == "passed"
    assert next(c for c in tv["checks"] if c["check_type"] == "tests")["status"] == (
        "passed"
    )


# --- DoD 5: OP-A honesty — acceptance/scope/human PENDING, stated -----------


def test_acceptance_reports_pending_with_op_a_note(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    ticket = make_ticket(key="ATLAS-200")
    TicketRepo(db).add(ticket)
    seed_evidence(db, sys_test(), sys_lint())  # machine green, no confirmations

    assert run_verify(db, fake()) == EXIT_OK
    out = capsys.readouterr().out

    rows = VerificationCheckRepo(db).list()
    acceptance = next(
        r
        for r in rows
        if r.check_type == VT.ACCEPTANCE_CRITERIA and r.ticket_id == ticket.id
    )
    # Wrong answer: acceptance PASSED with no confirmation written (OP-A violated).
    assert acceptance.status == ES.PENDING
    assert acceptance.completed_at is None
    # The report states the honest OP-A behaviour.
    assert "OP-A" in out
    assert "PENDING" in out


# --- DoD 6: --json shape ----------------------------------------------------


def test_json_emits_serialised_pr_verification(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    ticket = make_ticket(key="ATLAS-200")
    TicketRepo(db).add(ticket)
    seed_evidence(db, sys_test(), sys_lint())

    assert run_verify(db, fake(), "--json") == EXIT_OK
    payload = json.loads(capsys.readouterr().out)

    assert payload["head_commit"] == HEAD
    assert set(payload) == {"head_commit", "status", "tickets"}
    tv = payload["tickets"][0]
    assert tv["ticket_id"] == str(ticket.id)
    assert set(tv) == {"ticket_id", "status", "checks"}
    check = tv["checks"][0]
    # Wrong answer: a check missing 'evidence_ids' or 'reason' (an incomplete
    # documented shape that breaks automation).
    assert set(check) == {"check_type", "required", "status", "evidence_ids", "reason"}


# --- DoD 7: empty/edge handling, never a traceback --------------------------


def test_empty_close_set_is_a_clean_pending_report(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    # A title/body with no ATLAS key -> empty close-set -> PENDING, no traceback.
    code = run_verify(db, fake(title="chore: no key here", body="nothing"), "--json")
    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "pending"  # never a vacuous PASS over no tickets
    assert payload["tickets"] == []


def test_unknown_ticket_key_is_skipped_and_reported(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    # --tickets names a key not in the DB: skipped, reported, no traceback.
    code = run_verify(db, fake(), "--tickets", "ATLAS-999")
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "ATLAS-999" in out  # named as skipped
    assert VerificationCheckRepo(db).list() == []  # nothing to verify -> no rows


def test_empty_pr_files_does_not_crash(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    ticket = make_ticket(key="ATLAS-200")
    TicketRepo(db).add(ticket)
    seed_evidence(db, sys_test(), sys_lint())
    code = run_verify(db, fake(files=[]), "--json")
    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    # Empty PR files -> scope vacuously in-scope (no out-of-scope file) -> PASSED.
    checks = payload["tickets"][0]["checks"]
    scope = next(c for c in checks if c["check_type"] == "scope")
    assert scope["status"] == "passed"


# --- DoD 8: append-only persistence -----------------------------------------


def test_second_run_appends_rows_mutating_none(db: Database) -> None:
    ticket = make_ticket(key="ATLAS-200")
    TicketRepo(db).add(ticket)
    seed_evidence(db, sys_test(), sys_lint())

    assert run_verify(db, fake()) == EXIT_OK
    first = VerificationCheckRepo(db).list()
    first_ids = {r.id for r in first}
    assert len(first) == 4  # TESTS, LINT, ACCEPTANCE, SCOPE

    assert run_verify(db, fake()) == EXIT_OK
    second = VerificationCheckRepo(db).list()
    # Wrong answer: the count stays at 4 (the run mutated prior rows instead of
    # appending) — append-only means the row count doubles.
    assert len(second) == 8
    assert first_ids <= {r.id for r in second}  # every original row still present


# --- ATLAS-134: verify records the merge as system-tier evidence ------------


def test_merged_pr_records_pr_merged_evidence_per_ticket(db: Database) -> None:
    """ATLAS-134 AC-1: verify on a merged PR persists exactly one system-tier
    PR_MERGED Evidence per close-set ticket, pinned to C, PASSED, system-tier."""
    ticket = make_ticket(key="ATLAS-200")
    TicketRepo(db).add(ticket)
    seed_evidence(db, sys_test(), sys_lint())

    assert run_verify(db, fake(merged=True)) == EXIT_OK

    merges = [e for e in EvidenceRepo(db).list() if e.evidence_type == ET.PR_MERGED]
    # wrong answer: 0 (the builder was never called on the merged PR)
    assert len(merges) == 1
    merge = merges[0]
    assert merge.ticket_id == ticket.id
    assert merge.commit_sha == HEAD
    assert merge.status == ES.PASSED
    assert merge.created_by_type == ActorType.SYSTEM


def test_unmerged_pr_records_no_pr_merged_evidence(db: Database) -> None:
    """ATLAS-134 AC-2: verify on an unmerged PR persists no PR_MERGED evidence."""
    ticket = make_ticket(key="ATLAS-200")
    TicketRepo(db).add(ticket)
    seed_evidence(db, sys_test(), sys_lint())

    assert run_verify(db, fake(merged=False)) == EXIT_OK

    merges = [e for e in EvidenceRepo(db).list() if e.evidence_type == ET.PR_MERGED]
    assert merges == []  # wrong answer: 1 (a record built for an unmerged PR)


# --- precondition exits (R2 contract) ---------------------------------------


def test_malformed_repo_is_clean_precondition(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        ["verify", "--pr", str(PR_NUMBER), "--repo", "not-a-slug"],
        database=db,
        github_client=fake(),
    )
    assert code == EXIT_PRECONDITION
    assert "OWNER/REPO" in capsys.readouterr().err


def test_unknown_pr_is_clean_precondition(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    # An unseeded PR object models a 404 -> GitHubAPIError -> clean precondition.
    code = run_verify(db, fake(with_pr=False))
    assert code == EXIT_PRECONDITION
    assert capsys.readouterr().err.strip()  # one stderr line, no traceback

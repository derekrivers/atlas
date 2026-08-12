"""Test-only live FastAPI factory for governed UI fault and race injection."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import alembic.command
from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError

import atlas.orchestration.operator_actions as operator_actions_module
from atlas.api.app import create_app
from atlas.api.dependencies import (
    get_acceptance_workflow_services,
    get_lesson_disposition_service,
)
from atlas.core.enums import EvidenceStatus
from atlas.core.models import Ticket
from atlas.github import (
    GitHubAPIError,
    GitHubCompare,
    GitHubCompareStatus,
    GitHubMalformedResponseError,
    GitHubTimeoutError,
)
from atlas.linear.client import LinearGraphQLClient
from atlas.orchestration import (
    AcceptanceSessionWorkflowServices,
    LessonDispositionService,
    build_acceptance_session_workflow,
)
from atlas.orchestration.pr_context import PRContext
from atlas.orchestration.verify import run_verify
from atlas.pm import sync as pm_sync
from atlas.storage import Database, TicketRepo
from atlas.verification import PRVerification, TicketVerification

_DATABASE_URL = os.environ["ATLAS_DATABASE_URL"]
_OPERATOR_TOKEN = os.environ["ATLAS_OPERATOR_TOKEN"]
_CLOCK_FILE = Path(os.environ["ATLAS_E2E_CLOCK_FILE"])
_RECEIPT_FAILURE = os.environ.get("ATLAS_E2E_RECEIPT_FAILURE") == "1"
_RECEIPT_FAILURE_CANARY = os.environ.get(
    "ATLAS_E2E_RECEIPT_FAILURE_CANARY",
    "seeded-receipt-failure",
)
_ACCEPTANCE = os.environ.get("ATLAS_E2E_ACCEPTANCE") == "1"
_ACCEPTANCE_STATE_FILE = Path(os.environ["ATLAS_E2E_ACCEPTANCE_STATE_FILE"])
_EXTERNAL_EVENTS_FILE = Path(os.environ["ATLAS_E2E_EXTERNAL_EVENTS_FILE"])
_HEAD_SHA = "a" * 40
_MOVED_HEAD_SHA = "c" * 40
_BASE_SHA = "b" * 40
_MOVED_BASE_SHA = "d" * 40
_LONG_HEAD_REF = "agent/" + "phase-14-exact-head-" * 11

_STATE_LOCK = threading.Lock()
_STATE_REVISION: object = None
_PULL_REQUEST_READS = 0
_EVENT_LOCK = threading.Lock()
_PATCHERS: list[Any] = []


def _clock() -> datetime:
    return datetime.fromisoformat(_CLOCK_FILE.read_text(encoding="utf-8").strip())


def _state() -> dict[str, Any]:
    payload = json.loads(_ACCEPTANCE_STATE_FILE.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise GitHubMalformedResponseError("seeded GitHub state was malformed")
    return payload


def _github_mode(state: dict[str, Any]) -> str:
    value = state.get("github", state.get("mode", "current"))
    if not isinstance(value, str):
        raise GitHubMalformedResponseError("seeded GitHub mode was malformed")
    return value


def _read_number() -> tuple[dict[str, Any], int]:
    global _PULL_REQUEST_READS, _STATE_REVISION
    with _STATE_LOCK:
        state = _state()
        revision = state.get("revision")
        if revision != _STATE_REVISION:
            _STATE_REVISION = revision
            _PULL_REQUEST_READS = 0
        _PULL_REQUEST_READS += 1
        return state, _PULL_REQUEST_READS


def _delay(state: dict[str, Any]) -> None:
    value = state.get("delay_ms", 0)
    if isinstance(value, int) and 0 < value <= 2_000:
        time.sleep(value / 1000)


def _observe(state: dict[str, Any]) -> str:
    _delay(state)
    mode = _github_mode(state)
    canary = str(state.get("error_canary", "seeded-external-error"))
    if mode == "timeout":
        raise GitHubTimeoutError(f"GitHub read deadline expired: {canary}")
    if mode == "failure":
        raise GitHubAPIError(f"GitHub external read failed: {canary}")
    if mode == "malformed":
        raise GitHubMalformedResponseError(f"GitHub response malformed: {canary}")
    return mode


def _record_external_event(category: str, operation: str) -> None:
    with (
        _EVENT_LOCK,
        _EXTERNAL_EVENTS_FILE.open("a", encoding="utf-8") as stream,
    ):
        stream.write(
            json.dumps(
                {"category": category, "operation": operation},
                sort_keys=True,
            )
            + "\n"
        )


def _forbidden(category: str, operation: str) -> Callable[..., Any]:
    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        _record_external_event(category, operation)
        raise AssertionError(f"acceptance milestone forbids {category}:{operation}")

    return refuse


def _audit_hook(event: str, args: tuple[Any, ...]) -> None:
    if event == "subprocess.Popen":
        executable = str(args[0]) if args else "unknown"
        _record_external_event("process", executable)
        raise RuntimeError("acceptance milestone forbids child processes")
    if event != "socket.connect" or len(args) < 2:
        return
    address = args[1]
    host = address[0] if isinstance(address, tuple) and address else None
    if host not in {"127.0.0.1", "::1", None}:
        _record_external_event("network", str(host))
        raise RuntimeError("acceptance milestone forbids remote network connections")


def _install_external_mutation_traps() -> None:
    sys.addaudithook(_audit_hook)
    targets: tuple[tuple[Any, str, str, str], ...] = (
        (LinearGraphQLClient, "create_issue", "linear", "create_issue"),
        (LinearGraphQLClient, "update_issue", "linear", "update_issue"),
        (LinearGraphQLClient, "set_state", "linear", "set_state"),
    )
    for owner, attribute, category, operation in targets:
        patcher = patch.object(owner, attribute, new=_forbidden(category, operation))
        patcher.start()
        _PATCHERS.append(patcher)
    for target, category, operation in (
        ("atlas.orchestration.pr_rebase.run_git", "git", "run_git"),
        ("atlas.orchestration.pr_rebase_cli.run_git", "git", "run_git_cli"),
    ):
        patcher = patch(target, new=_forbidden(category, operation))
        patcher.start()
        _PATCHERS.append(patcher)
    module_targets: tuple[tuple[Any, str, str, str], ...] = (
        (pm_sync, "sync_tick", "pm", "sync_tick"),
        (alembic.command, "upgrade", "schema", "upgrade"),
    )
    for owner, attribute, category, operation in module_targets:
        patcher = patch.object(owner, attribute, new=_forbidden(category, operation))
        patcher.start()
        _PATCHERS.append(patcher)


class _ReceiptFailingDispositionService:
    def __init__(self, request: Request) -> None:
        self._service = LessonDispositionService(request.app.state.database)

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        failure = SQLAlchemyError(_RECEIPT_FAILURE_CANARY)
        with patch(
            "atlas.orchestration.operator_actions._add_operator_action_receipt",
            side_effect=failure,
        ):
            return self._service.execute(*args, **kwargs)


class _AcceptanceGitHubClient:
    """Read-only GitHub boundary controlled by one atomic test state file."""

    def fetch_pull_request(
        self, owner: str, repo: str, pr_number: int
    ) -> dict[str, Any]:
        state, read_number = _read_number()
        mode = _observe(state)
        move_after_evidence = mode == "head-moved-after-evidence" and read_number >= 3
        head_sha = (
            _MOVED_HEAD_SHA
            if mode == "head-moved" or move_after_evidence
            else _HEAD_SHA
        )
        ticket_key = "ATLAS-243" if pr_number == 415 else "ATLAS-244"
        head_ref = (
            "agent/atl-415-review-acceptance-console"
            if pr_number == 415
            else _LONG_HEAD_REF
        )
        return {
            "number": pr_number,
            "title": f"{ticket_key}: Acceptance console exact-head milestone",
            "body": None,
            "state": "open",
            "draft": False,
            "merged": False,
            "mergeable": True,
            "head": {
                "ref": head_ref,
                "sha": head_sha,
                "repo": {"full_name": f"{owner}/{repo}"},
            },
            "base": {
                "ref": "main",
                "sha": _BASE_SHA,
                "repo": {"full_name": f"{owner}/{repo}"},
            },
        }

    def fetch_branch_head(self, *_args: Any) -> str:
        state = _state()
        mode = _observe(state)
        move_after_evidence = (
            mode == "main-moved-after-evidence" and _PULL_REQUEST_READS >= 3
        )
        return (
            _MOVED_BASE_SHA
            if mode == "main-moved" or move_after_evidence
            else _BASE_SHA
        )

    def compare_commits(
        self,
        _owner: str,
        _repo: str,
        base_sha: str,
        _head_sha: str,
    ) -> GitHubCompare:
        _observe(_state())
        return GitHubCompare(
            status=GitHubCompareStatus.AHEAD,
            ahead_by=1,
            behind_by=0,
            merge_base_sha=base_sha,
        )

    def fetch_workflow_runs(self, *_args: Any) -> list[dict[str, Any]]:
        state = _state()
        mode = _observe(state)
        if mode == "evidence-malformed":
            return [{"unbounded_error": state.get("error_canary", "malformed")}]
        return [
            {
                "id": 24401,
                "name": "test-phase-14-live",
                "status": "completed",
                "conclusion": "success",
                "updated_at": "2030-08-12T12:00:00Z",
                "html_url": "https://example.invalid/atlas/actions/24401",
            },
            {
                "id": 24402,
                "name": "lint-phase-14-live",
                "status": "completed",
                "conclusion": "success",
                "updated_at": "2030-08-12T12:00:01Z",
                "html_url": "https://example.invalid/atlas/actions/24402",
            },
        ]

    def fetch_check_runs(self, *_args: Any) -> list[dict[str, Any]]:
        _observe(_state())
        return []

    def fetch_pr_reviews(self, *_args: Any) -> list[dict[str, Any]]:
        _observe(_state())
        return [
            {
                "id": 24403,
                "state": "APPROVED",
                "commit_id": _HEAD_SHA,
                "user": {"login": "phase-14-reviewer"},
                "html_url": "https://example.invalid/atlas/reviews/24403",
            }
        ]

    def fetch_pr_files(
        self, _owner: str, _repo: str, pr_number: int
    ) -> list[dict[str, Any]]:
        _observe(_state())
        return [
            {
                "filename": (
                    "docs/atlas/operator-ui.md"
                    if pr_number == 415
                    else "docs/atlas/review-acceptance-console.md"
                )
            }
        ]

    def merge_pull_request(self, *_args: Any, **_kwargs: Any) -> None:
        _forbidden("github", "merge_pull_request")()

    def update_pull_request(self, *_args: Any, **_kwargs: Any) -> None:
        _forbidden("github", "update_pull_request")()

    def update_ref(self, *_args: Any, **_kwargs: Any) -> None:
        _forbidden("github", "update_ref")()


class _AcceptanceTicketLookup:
    """Canonical repository read with deterministic read-only drift projection."""

    def __init__(self, repository: TicketRepo) -> None:
        self._repository = repository

    def get_by_key(self, key: str) -> Ticket | None:
        ticket = self._repository.get_by_key(key)
        if ticket is None:
            return None
        mode = _state().get("ticket", "current")
        if mode == "criteria-drift":
            return ticket.model_copy(
                update={
                    "acceptance_criteria": [
                        *ticket.acceptance_criteria,
                        "A criterion added after the exact-head session was pinned.",
                    ]
                }
            )
        if mode == "missing":
            return None
        return ticket


class _AcceptanceVerificationFixture:
    """Canonical verifier by default; explicit typed verdicts for fault cases."""

    def __init__(self, tickets: TicketRepo) -> None:
        self._tickets = tickets

    def __call__(
        self,
        context: PRContext,
        close_set: tuple[str, ...],
        db: Database,
    ) -> PRVerification:
        mode = _state().get("verification", "canonical")
        if mode == "canonical":
            return run_verify(context, close_set, db).verification
        if mode == "malformed":
            return cast(PRVerification, {"malformed": True})

        status_by_mode = {
            "pending": EvidenceStatus.PENDING,
            "failed": EvidenceStatus.FAILED,
            "warning": EvidenceStatus.WARNING,
            "not_applicable": EvidenceStatus.NOT_APPLICABLE,
        }
        status = status_by_mode.get(str(mode), EvidenceStatus.PASSED)
        tickets = tuple(
            TicketVerification(
                ticket_id=ticket.id,
                status=status,
                checks=(),
            )
            for key in close_set
            if (ticket := self._tickets.get_by_key(key)) is not None
        )
        if mode == "close-set-mismatch":
            tickets = ()
        return PRVerification(
            head_commit=_MOVED_HEAD_SHA if mode == "old-head" else _HEAD_SHA,
            status=status,
            tickets=tickets,
        )


_REAL_ADD_RECEIPT = cast(
    Callable[[Any, Any], None],
    vars(operator_actions_module)["_add_operator_action_receipt"],
)
_REAL_APPLY_MUTATIONS = cast(
    Callable[[Any, Any], None],
    vars(operator_actions_module)["_apply_operator_action_mutations"],
)


def _guarded_add_receipt(session: Any, receipt: Any) -> None:
    action = _state().get("receipt_failure_action")
    if action == receipt.action:
        raise SQLAlchemyError(str(_state().get("error_canary", "receipt-failure")))
    _REAL_ADD_RECEIPT(session, receipt)


def _guarded_apply_mutations(session: Any, mutations: Any) -> None:
    if _state().get("store_failure") is True:
        raise SQLAlchemyError(str(_state().get("error_canary", "store-failure")))
    _REAL_APPLY_MUTATIONS(session, mutations)


_database = Database(_DATABASE_URL)
_github = _AcceptanceGitHubClient()
_workflow: AcceptanceSessionWorkflowServices | None = None
if _ACCEPTANCE:
    _workflow = build_acceptance_session_workflow(
        _database,
        _github,
        _clock,
        ticket_lookup=_AcceptanceTicketLookup(TicketRepo(_database)),
        verification_service=_AcceptanceVerificationFixture(TicketRepo(_database)),
    )

app = create_app(
    database=_database,
    enable_writes=True,
    operator_token=_OPERATOR_TOKEN,
    bind_host="127.0.0.1",
    clock=_clock,
    acceptance_repositories=("acme/atlas",) if _ACCEPTANCE else None,
    acceptance_github_client=_github if _ACCEPTANCE else None,
    acceptance_external_timeout_seconds=0.25,
)

if _workflow is not None:
    app.dependency_overrides[get_acceptance_workflow_services] = lambda: _workflow

_PATCHERS.append(
    patch.object(
        operator_actions_module,
        "_add_operator_action_receipt",
        new=_guarded_add_receipt,
    )
)
_PATCHERS[-1].start()
_PATCHERS.append(
    patch.object(
        operator_actions_module,
        "_apply_operator_action_mutations",
        new=_guarded_apply_mutations,
    )
)
_PATCHERS[-1].start()
_install_external_mutation_traps()

if _RECEIPT_FAILURE:

    def _receipt_failing_service(request: Request) -> _ReceiptFailingDispositionService:
        return _ReceiptFailingDispositionService(request)

    app.dependency_overrides[get_lesson_disposition_service] = _receipt_failing_service

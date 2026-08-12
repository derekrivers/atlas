"""Test-only live FastAPI factory for governed UI fault and race injection."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError

from atlas.api.app import create_app
from atlas.api.dependencies import get_lesson_disposition_service
from atlas.github import (
    GitHubAPIError,
    GitHubCompare,
    GitHubCompareStatus,
    GitHubTimeoutError,
)
from atlas.orchestration import LessonDispositionService

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
_HEAD_SHA = "a" * 40
_MOVED_HEAD_SHA = "c" * 40
_BASE_SHA = "b" * 40
_MOVED_BASE_SHA = "d" * 40


def _clock() -> datetime:
    return datetime.fromisoformat(_CLOCK_FILE.read_text(encoding="utf-8").strip())


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

    def _mode(self) -> str:
        state = json.loads(_ACCEPTANCE_STATE_FILE.read_text(encoding="utf-8"))
        mode = state.get("mode")
        if not isinstance(mode, str):
            raise GitHubAPIError("seeded GitHub state was malformed")
        if mode == "timeout":
            raise GitHubTimeoutError("seeded GitHub read deadline expired")
        if mode == "failure":
            raise GitHubAPIError("seeded GitHub external read failed")
        return mode

    def fetch_pull_request(
        self, owner: str, repo: str, pr_number: int
    ) -> dict[str, Any]:
        mode = self._mode()
        head_sha = _MOVED_HEAD_SHA if mode == "head-moved" else _HEAD_SHA
        return {
            "number": pr_number,
            "title": "ATLAS-243: Review queue acceptance console UI",
            "body": None,
            "state": "open",
            "draft": False,
            "merged": False,
            "mergeable": True,
            "head": {
                "ref": "agent/atl-415-review-acceptance-console",
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
        return _MOVED_BASE_SHA if self._mode() == "main-moved" else _BASE_SHA

    def compare_commits(self, *_args: Any) -> GitHubCompare:
        self._mode()
        return GitHubCompare(
            status=GitHubCompareStatus.AHEAD,
            ahead_by=1,
            behind_by=0,
            merge_base_sha=_BASE_SHA,
        )

    def fetch_workflow_runs(self, *_args: Any) -> list[dict[str, Any]]:
        self._mode()
        return []

    def fetch_check_runs(self, *_args: Any) -> list[dict[str, Any]]:
        self._mode()
        return []

    def fetch_pr_reviews(self, *_args: Any) -> list[dict[str, Any]]:
        self._mode()
        return []

    def fetch_pr_files(self, *_args: Any) -> list[dict[str, Any]]:
        self._mode()
        return [{"filename": "docs/atlas/operator-ui.md"}]


app = create_app(
    database_url=_DATABASE_URL,
    enable_writes=True,
    operator_token=_OPERATOR_TOKEN,
    bind_host="127.0.0.1",
    clock=_clock,
    acceptance_repositories=("acme/atlas",) if _ACCEPTANCE else None,
    acceptance_github_client=_AcceptanceGitHubClient() if _ACCEPTANCE else None,
    acceptance_external_timeout_seconds=0.25,
)

if _RECEIPT_FAILURE:

    def _receipt_failing_service(request: Request) -> _ReceiptFailingDispositionService:
        return _ReceiptFailingDispositionService(request)

    app.dependency_overrides[get_lesson_disposition_service] = _receipt_failing_service

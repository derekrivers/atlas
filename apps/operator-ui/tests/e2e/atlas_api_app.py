"""Test-only live FastAPI factory for Phase 13 fault and race injection."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError

from atlas.api.app import create_app
from atlas.api.dependencies import get_lesson_disposition_service
from atlas.orchestration import LessonDispositionService

_DATABASE_URL = os.environ["ATLAS_DATABASE_URL"]
_OPERATOR_TOKEN = os.environ["ATLAS_OPERATOR_TOKEN"]
_CLOCK_FILE = Path(os.environ["ATLAS_E2E_CLOCK_FILE"])
_RECEIPT_FAILURE = os.environ.get("ATLAS_E2E_RECEIPT_FAILURE") == "1"
_RECEIPT_FAILURE_CANARY = os.environ.get(
    "ATLAS_E2E_RECEIPT_FAILURE_CANARY",
    "seeded-receipt-failure",
)


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


app = create_app(
    database_url=_DATABASE_URL,
    enable_writes=True,
    operator_token=_OPERATOR_TOKEN,
    bind_host="127.0.0.1",
    clock=_clock,
)

if _RECEIPT_FAILURE:

    def _receipt_failing_service(request: Request) -> _ReceiptFailingDispositionService:
        return _ReceiptFailingDispositionService(request)

    app.dependency_overrides[get_lesson_disposition_service] = _receipt_failing_service

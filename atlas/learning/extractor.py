"""Lesson extraction for completion and failure events.

The extractor owns the deterministic half of learning-system.md: it decides
whether a ``done`` transition is notable, assembles a bounded evidence bundle,
renders a versioned prompt, calls an injected LLM client, validates the model's
JSON against the :class:`Lesson` schema after assigning system-owned fields, and
persists the resulting DRAFT lesson. LLM failures are logged once at WARNING and
return ``None``; every invocation stamps the extraction-attempt cursor so callers
do not retry automatic events.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable
from uuid import uuid4

import yaml
from jinja2 import Environment, StrictUndefined
from jinja2.exceptions import UndefinedError

from atlas.core.enums import ActorType, EntityStatus, EvidenceStatus
from atlas.core.models.agent_run import AgentRun
from atlas.core.models.debt_item import DebtItem
from atlas.core.models.evidence import Evidence, EvidenceType
from atlas.core.models.lesson import Lesson
from atlas.core.models.ticket import Ticket, TicketStatus
from atlas.core.models.verification_check import VerificationCheck
from atlas.storage.db import Database
from atlas.storage.repositories import (
    AgentRunRepo,
    EvidenceRepo,
    LessonRepo,
    TicketRepo,
    VerificationCheckRepo,
)
from atlas.verification.completion import ticket_verdict_from_checks

logger = logging.getLogger("atlas.learning.extractor")

PROMPTS_DIR = Path(__file__).parent / "prompts"
LESSON_EXTRACTOR_VERSION = "lesson-extractor-v1.1.0"
DEFAULT_RAW_DIFF_SIZE_CAP_BYTES = 8192
MAX_AGENT_RUNS = 10
MAX_PR_REVIEWS = 20
MAX_VERIFICATION_CHECKS = 20
FAST_CYCLE_MIN_BASELINE = 3
FAST_CYCLE_RATIO = 0.5
EXTRACTOR_ACTOR_ID = "lesson-extractor"

_VERSION_RE = re.compile(r"^lesson-extractor-v\d+\.\d+\.\d+$")
_FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)
_RAW_DIFF_KEY_RE = re.compile(r"(diff|patch)", re.IGNORECASE)


class ExtractionTrigger(StrEnum):
    """Event that requested lesson extraction."""

    DONE = "done"
    REJECTED = "rejected"
    PM_FAILURE_ANALYSIS = "pm_failure_analysis"
    OPERATOR_REQUEST = "operator_request"


class LessonExtractionError(RuntimeError):
    """The extractor could not render, call, parse, validate, or persist."""


@runtime_checkable
class LessonModelClient(Protocol):
    """The learning model-call seam: one prompt in, one text response out."""

    def generate(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class RenderedLessonPrompt:
    """A rendered lesson-extraction prompt and provenance hash."""

    text: str
    prompt_version: str
    prompt_hash: str


def _json_bytes(value: object) -> int:
    return len(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    )


def _model_dump(model: Any) -> dict[str, Any]:
    return cast(dict[str, Any], model.model_dump(mode="json"))


def _without_oversized_raw_diffs(
    payload: Mapping[str, Any], *, raw_diff_size_cap_bytes: int
) -> dict[str, Any]:
    bounded: dict[str, Any] = {}
    for key, value in payload.items():
        size = _json_bytes(value)
        if _RAW_DIFF_KEY_RE.search(key) and size > raw_diff_size_cap_bytes:
            bounded[key] = {
                "_excluded": True,
                "_reason": "raw diff exceeds lesson evidence bundle size cap",
                "_original_bytes": size,
                "_cap_bytes": raw_diff_size_cap_bytes,
            }
        else:
            bounded[key] = value
    if _json_bytes(bounded) <= raw_diff_size_cap_bytes:
        return bounded
    return {
        "_excluded": True,
        "_reason": "raw payload exceeds lesson evidence bundle size cap",
        "_original_bytes": _json_bytes(payload),
        "_cap_bytes": raw_diff_size_cap_bytes,
    }


def _bounded_review(
    evidence: Evidence, *, raw_diff_size_cap_bytes: int
) -> dict[str, Any]:
    payload = _model_dump(evidence)
    payload["raw_payload"] = _without_oversized_raw_diffs(
        evidence.raw_payload,
        raw_diff_size_cap_bytes=raw_diff_size_cap_bytes,
    )
    return payload


def assemble_evidence_bundle(
    *,
    ticket: Ticket,
    agent_runs: Sequence[AgentRun],
    pr_review_history: Sequence[Evidence],
    verification_checks: Sequence[VerificationCheck],
    trigger: ExtractionTrigger,
    failure_event: DebtItem | None = None,
    raw_diff_size_cap_bytes: int = DEFAULT_RAW_DIFF_SIZE_CAP_BYTES,
) -> dict[str, Any]:
    """Build the bounded evidence bundle sent to the LLM.

    The bundle includes the ticket record, recent agent runs, PR review evidence,
    verification checks, and an optional PM failure-analysis event. Agent runs,
    reviews, and checks are capped to fixed counts; raw diff/patch payload fields
    whose serialised size exceeds ``raw_diff_size_cap_bytes`` are replaced with
    exclusion markers instead of sending the raw text.
    """

    ordered_runs = sorted(
        agent_runs,
        key=lambda run: (run.started_at or run.created_at, run.created_at, run.id),
    )[-MAX_AGENT_RUNS:]
    ordered_reviews = sorted(
        pr_review_history,
        key=lambda review: (review.created_at, review.id),
    )[-MAX_PR_REVIEWS:]
    ordered_checks = sorted(
        verification_checks,
        key=lambda check: (check.created_at, check.id),
    )[-MAX_VERIFICATION_CHECKS:]
    return {
        "trigger": trigger.value,
        "ticket": _model_dump(ticket),
        "agent_runs": [_model_dump(run) for run in ordered_runs],
        "pr_review_history": [
            _bounded_review(review, raw_diff_size_cap_bytes=raw_diff_size_cap_bytes)
            for review in ordered_reviews
        ],
        "verification_verdicts": [_model_dump(check) for check in ordered_checks],
        "failure_event": None if failure_event is None else _model_dump(failure_event),
    }


def _source_ticket_tag_vocabulary(ticket: Ticket) -> dict[str, object]:
    return {
        "source_ticket_tags_json": json.dumps(ticket.tags),
        "source_ticket_component_json": json.dumps(ticket.component),
        "source_ticket_has_tag_vocabulary": bool(ticket.tags or ticket.component),
    }


def _load_template(version: str, directory: Path) -> tuple[list[str], str]:
    if not _VERSION_RE.match(version):
        raise LessonExtractionError(f"invalid lesson prompt version {version!r}")
    path = directory / f"{version}.md.j2"
    if not path.is_file():
        raise LessonExtractionError(f"no lesson extraction template for {version!r}")
    raw = path.read_text(encoding="utf-8")
    match = _FRONT_MATTER_RE.match(raw)
    if not match:
        raise LessonExtractionError(f"{path.name} has no YAML front matter")
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as error:
        raise LessonExtractionError(f"{path.name} front matter is invalid") from error
    if not isinstance(meta, dict):
        raise LessonExtractionError(f"{path.name} front matter is not a mapping")
    if meta.get("prompt_version") != version:
        raise LessonExtractionError(f"{path.name} front matter version mismatch")
    if meta.get("template_engine") != "jinja2":
        raise LessonExtractionError(f"{path.name} does not declare jinja2")
    variables = meta.get("template_variables")
    if not isinstance(variables, list) or not variables:
        raise LessonExtractionError(f"{path.name} has no template_variables list")
    return [str(name) for name in variables], raw[match.end() :]


def render_extraction_prompt(
    variables: Mapping[str, object],
    *,
    version: str = LESSON_EXTRACTOR_VERSION,
    prompts_dir: Path | None = None,
) -> RenderedLessonPrompt:
    """Render the versioned lesson-extraction prompt with strict variables."""

    directory = prompts_dir or PROMPTS_DIR
    declared, body = _load_template(version, directory)
    missing = sorted(set(declared) - set(variables))
    extra = sorted(set(variables) - set(declared))
    if missing:
        raise LessonExtractionError(f"missing lesson prompt variables: {missing}")
    if extra:
        raise LessonExtractionError(f"undeclared lesson prompt variables: {extra}")
    try:
        text = (
            Environment(undefined=StrictUndefined)
            .from_string(body)
            .render(dict(variables))
        )
    except UndefinedError as error:
        raise LessonExtractionError(
            f"{version} referenced an undefined variable: {error.message}"
        ) from error
    return RenderedLessonPrompt(
        text=text,
        prompt_version=version,
        prompt_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def _extract_json_object(raw: str) -> str:
    start = raw.find("{")
    if start == -1:
        return raw
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(raw)):
        char = raw[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return raw[start : index + 1]
    return raw


def _cycle_seconds(ticket: Ticket) -> float | None:
    finished = ticket.completed_at or ticket.status_entered_at
    if finished is None:
        return None
    return (finished - ticket.created_at).total_seconds()


def _same_type_or_tag(left: Ticket, right: Ticket) -> bool:
    return left.ticket_type == right.ticket_type or bool(
        set(left.tags) & set(right.tags)
    )


def _has_prior_related_failure(ticket: Ticket, tickets: Sequence[Ticket]) -> bool:
    ticket_finished = (
        ticket.completed_at or ticket.status_entered_at or ticket.updated_at
    )
    for other in tickets:
        if other.id == ticket.id or other.status is not TicketStatus.REJECTED:
            continue
        other_finished = (
            other.completed_at or other.status_entered_at or other.updated_at
        )
        if other_finished >= ticket_finished:
            continue
        if _same_type_or_tag(ticket, other):
            return True
    return False


def _unusually_fast_cycle(ticket: Ticket, tickets: Sequence[Ticket]) -> bool:
    current = _cycle_seconds(ticket)
    if current is None:
        return False
    baseline = [
        seconds
        for other in tickets
        if other.id != ticket.id
        and other.status is TicketStatus.DONE
        and _same_type_or_tag(ticket, other)
        and (seconds := _cycle_seconds(other)) is not None
    ]
    if len(baseline) < FAST_CYCLE_MIN_BASELINE:
        return False
    baseline.sort()
    median = baseline[len(baseline) // 2]
    return current <= median * FAST_CYCLE_RATIO


def notable_done_ticket(
    ticket: Ticket,
    *,
    tickets: Sequence[Ticket],
    verification_checks: Sequence[VerificationCheck],
) -> bool:
    """Return whether a ``done`` ticket merits automatic success extraction.

    The deterministic predicate follows learning-system.md: the ticket must be
    ``done`` and either be a first-attempt PASSED verification after prior
    related failures, or have an unusually fast cycle time compared with prior
    completed tickets of the same type/tag.
    """

    if ticket.status is not TicketStatus.DONE:
        return False
    first_attempt_pass = (
        ticket.review_cycle_count == 0
        and ticket_verdict_from_checks(ticket, verification_checks)
        is EvidenceStatus.PASSED
    )
    return (
        first_attempt_pass and _has_prior_related_failure(ticket, tickets)
    ) or _unusually_fast_cycle(ticket, tickets)


def _pr_reviews_for_ticket(ticket: Ticket, db: Database) -> list[Evidence]:
    reviews = [
        evidence
        for evidence in EvidenceRepo(db).list()
        if evidence.evidence_type is EvidenceType.PR_REVIEW
        and evidence.product_id == ticket.product_id
        and evidence.ticket_id in {ticket.id, None}
    ]
    return sorted(reviews, key=lambda evidence: (evidence.created_at, evidence.id))


def _parse_lesson(
    raw_output: str,
    *,
    ticket: Ticket,
    now: datetime,
) -> Lesson:
    try:
        parsed = json.loads(_extract_json_object(raw_output))
    except json.JSONDecodeError as error:
        raise LessonExtractionError("lesson model output was not valid JSON") from error
    if not isinstance(parsed, dict):
        raise LessonExtractionError("lesson model output must be a JSON object")
    payload = {
        **parsed,
        "id": uuid4(),
        "product_id": ticket.product_id,
        "status": EntityStatus.DRAFT,
        "confidence": None,
        "source_ticket_id": ticket.id,
        "related_ticket_ids": [],
        "created_by_type": ActorType.AGENT,
        "created_by_id": EXTRACTOR_ACTOR_ID,
        "created_at": now,
        "updated_at": now,
    }
    try:
        return Lesson.model_validate(payload)
    except Exception as error:
        raise LessonExtractionError(
            "lesson model output failed Lesson schema"
        ) from error


def _record_extraction_attempt(ticket: Ticket, *, db: Database, now: datetime) -> None:
    """Stamp the ticket-level extraction attempt cursor."""

    TicketRepo(db).mark_lesson_extraction_attempted(ticket.key, attempted_at=now)


def extract_lesson_for_ticket(
    ticket: Ticket,
    *,
    db: Database,
    client: LessonModelClient | None,
    now: datetime,
    trigger: ExtractionTrigger,
    failure_event: DebtItem | None = None,
    force: bool = False,
    raw_diff_size_cap_bytes: int = DEFAULT_RAW_DIFF_SIZE_CAP_BYTES,
) -> Lesson | None:
    """Extract, validate, and persist one DRAFT lesson for ``ticket``.

    ``done`` events are gated by :func:`notable_done_ticket` unless ``force`` is
    true (the CLI/operator request path). All extraction failures are logged at
    WARNING with the ticket key and exception type, and return ``None`` without
    persisting a partial lesson. Every call records an attempt timestamp,
    including deterministic non-extractions and failed model calls, so all
    automatic trigger paths share the same retry cursor.
    """

    try:
        checks = VerificationCheckRepo(db).list_for_ticket(ticket.id)
        if (
            not force
            and trigger is ExtractionTrigger.DONE
            and not notable_done_ticket(
                ticket,
                tickets=TicketRepo(db).list(),
                verification_checks=checks,
            )
        ):
            return None
        if client is None:
            raise LessonExtractionError("no lesson model client configured")
        bundle = assemble_evidence_bundle(
            ticket=ticket,
            agent_runs=AgentRunRepo(db).list_for_ticket(ticket.id),
            pr_review_history=_pr_reviews_for_ticket(ticket, db),
            verification_checks=checks,
            trigger=trigger,
            failure_event=failure_event,
            raw_diff_size_cap_bytes=raw_diff_size_cap_bytes,
        )
        prompt = render_extraction_prompt(
            {
                "trigger": trigger.value,
                "ticket_key": ticket.key,
                "raw_diff_size_cap_bytes": raw_diff_size_cap_bytes,
                **_source_ticket_tag_vocabulary(ticket),
                "lesson_json_schema": json.dumps(
                    Lesson.model_json_schema(),
                    indent=2,
                    sort_keys=True,
                    default=str,
                ),
                "evidence_bundle_json": json.dumps(
                    bundle,
                    indent=2,
                    sort_keys=True,
                    default=str,
                ),
            }
        )
        lesson = _parse_lesson(
            client.generate(prompt.text),
            ticket=ticket,
            now=now,
        )
        return LessonRepo(db).add(lesson)
    except Exception as error:
        logger.warning(
            "lesson extraction failed for %s (%s): %s",
            ticket.key,
            type(error).__name__,
            error,
        )
        return None
    finally:
        _record_extraction_attempt(ticket, db=db, now=now)

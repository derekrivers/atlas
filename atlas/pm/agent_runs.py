"""AgentRun reconstruction from PM sync observations (ATLAS-166).

Atlas does not receive a Symphony callback and does not parse transcripts. The
sync tick already observes the durable facts needed to attribute dispatched
work: status-transition rows, the current Linear issue description that Atlas
itself wrote, Verification/Evidence rows, and the ticket record. This module
turns those observations into one ``AgentRun`` row per dispatch transition.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from atlas.core.models import (
    AgentProvider,
    AgentRun,
    AgentRunStatus,
    Evidence,
    Ticket,
    TicketStatus,
    TicketStatusTransition,
)
from atlas.linear.ownership import PACK_HEADER_PREFIX
from atlas.storage import (
    AgentRunRepo,
    Database,
    EvidenceRepo,
    TicketRepo,
    TicketStatusTransitionRepo,
    VerificationCheckRepo,
)

RECONSTRUCTION_SOURCE = "agent_run_reconstruction"

_UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_PACK_HEADER_RE = re.compile(
    rf"{re.escape(PACK_HEADER_PREFIX)} \| "
    rf"pack_id: (?P<pack_id>{_UUID_RE}) \| "
    r"rendered_at: (?P<rendered_at>[^\n]+)",
    re.IGNORECASE,
)
_PR_TEXT_RE = re.compile(
    r"(?:/pull/|pull[/#:\s-]+|pr[/#:\s-]+)(?P<number>\d+)", re.IGNORECASE
)
_PR_NUMBER_KEYS = frozenset(
    {"number", "pr_number", "pull_number", "pull_request_number"}
)
_HANDOFF_STATUSES = frozenset(
    {TicketStatus.REVIEW_REQUIRED.value, TicketStatus.NEEDS_HUMAN_DECISION.value}
)


@dataclass(frozen=True)
class AgentRunReconstructionResult:
    """How many AgentRun rows this reconstruction pass inserted or updated."""

    created: int = 0
    updated: int = 0


@dataclass(frozen=True)
class _DispatchCycle:
    dispatch: TicketStatusTransition
    handoff: TicketStatusTransition | None
    next_dispatch: TicketStatusTransition | None


@dataclass(frozen=True)
class _PackHeader:
    pack_id: UUID | None
    rendered_at: str | None


def _pack_header(description: str | None) -> _PackHeader:
    if not description:
        return _PackHeader(pack_id=None, rendered_at=None)
    match = _PACK_HEADER_RE.search(description)
    if match is None:
        return _PackHeader(pack_id=None, rendered_at=None)
    try:
        pack_id = UUID(match.group("pack_id"))
    except ValueError:
        pack_id = None
    return _PackHeader(pack_id=pack_id, rendered_at=match.group("rendered_at"))


def _pr_number_from_text(value: str | None) -> int | None:
    if not value:
        return None
    match = _PR_TEXT_RE.search(value)
    if match is None:
        return None
    return int(match.group("number"))


def _pr_number_from_payload(value: Any, *, depth: int = 0) -> int | None:
    if depth > 6:
        return None
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key).lower()
            if key_text in _PR_NUMBER_KEYS and isinstance(nested, int):
                return nested
            if key_text == "pull_request":
                number = _pr_number_from_payload(nested, depth=depth + 1)
                if number is not None:
                    return number
        for nested in value.values():
            number = _pr_number_from_payload(nested, depth=depth + 1)
            if number is not None:
                return number
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
        for nested in value:
            number = _pr_number_from_payload(nested, depth=depth + 1)
            if number is not None:
                return number
    elif isinstance(value, str):
        return _pr_number_from_text(value)
    return None


def _pr_number(evidence: Sequence[Evidence]) -> int | None:
    for record in sorted(evidence, key=lambda item: (item.created_at, item.id)):
        for value in (record.source_uri, record.external_run_id, record.summary):
            number = _pr_number_from_text(value)
            if number is not None:
                return number
        number = _pr_number_from_payload(record.raw_payload)
        if number is not None:
            return number
    return None


def _head_commit(evidence: Sequence[Evidence]) -> str | None:
    for record in sorted(
        evidence, key=lambda item: (item.created_at, item.id), reverse=True
    ):
        if record.commit_sha:
            return record.commit_sha
    return None


def _details(summary: str | None) -> dict[str, Any]:
    if not summary:
        return {}
    try:
        payload = json.loads(summary)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    if payload.get("source") != RECONSTRUCTION_SOURCE:
        return {}
    return payload


def agent_run_observation(run: AgentRun) -> dict[str, Any]:
    """Return the structured observation payload stored in ``output_summary``.

    Invalid or non-reconstruction summaries return ``{}``, making this safe for
    report/tests and for future AgentRun producers that may use free text.
    """

    return _details(run.output_summary)


def _summary(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"))


def _cycles(
    transitions: Sequence[TicketStatusTransition],
) -> list[_DispatchCycle]:
    dispatch_indexes = [
        index
        for index, transition in enumerate(transitions)
        if transition.to_status == TicketStatus.IN_PROGRESS.value
    ]
    cycles: list[_DispatchCycle] = []
    for offset, index in enumerate(dispatch_indexes):
        next_index = (
            dispatch_indexes[offset + 1] if offset + 1 < len(dispatch_indexes) else None
        )
        handoff = next(
            (
                transition
                for transition in transitions[index + 1 : next_index]
                if transition.to_status in _HANDOFF_STATUSES
            ),
            None,
        )
        cycles.append(
            _DispatchCycle(
                dispatch=transitions[index],
                handoff=handoff,
                next_dispatch=transitions[next_index]
                if next_index is not None
                else None,
            )
        )
    return cycles


def _evidence_for_cycle(
    evidence: Sequence[Evidence], cycle: _DispatchCycle
) -> list[Evidence]:
    start = cycle.dispatch.occurred_at
    end = cycle.next_dispatch.occurred_at if cycle.next_dispatch is not None else None
    return [
        record
        for record in evidence
        if record.created_at >= start and (end is None or record.created_at < end)
    ]


def _run_status(handoff: TicketStatusTransition | None) -> AgentRunStatus:
    if handoff is None:
        return AgentRunStatus.RUNNING
    if handoff.to_status == TicketStatus.NEEDS_HUMAN_DECISION.value:
        return AgentRunStatus.NEEDS_HUMAN
    return AgentRunStatus.SUCCEEDED


def _new_run(
    ticket: Ticket,
    cycle: _DispatchCycle,
    *,
    pack: _PackHeader,
    evidence: Sequence[Evidence],
    now: datetime,
) -> AgentRun:
    handoff_state = cycle.handoff.to_status if cycle.handoff is not None else None
    payload: dict[str, Any] = {
        "source": RECONSTRUCTION_SOURCE,
        "dispatch_transition_id": str(cycle.dispatch.id),
        "handoff_transition_id": str(cycle.handoff.id) if cycle.handoff else None,
        "handoff_state": handoff_state,
        "pr_number": _pr_number(evidence),
        "head_commit": _head_commit(evidence),
        "input_context_pack_rendered_at": pack.rendered_at,
    }
    return AgentRun(
        id=uuid4(),
        product_id=ticket.product_id,
        ticket_id=ticket.id,
        provider=AgentProvider.SYMPHONY,
        status=_run_status(cycle.handoff),
        objective=ticket.objective,
        input_context_pack_id=pack.pack_id,
        output_summary=_summary(payload),
        started_at=cycle.dispatch.occurred_at,
        completed_at=cycle.handoff.occurred_at if cycle.handoff else None,
        created_at=now,
    )


def _merge(existing: AgentRun, observed: AgentRun) -> AgentRun:
    existing_payload = _details(existing.output_summary)
    observed_payload = _details(observed.output_summary)
    merged_payload = dict(existing_payload)
    for key, value in observed_payload.items():
        if value is not None or key not in merged_payload:
            merged_payload[key] = value

    completed_at = observed.completed_at or existing.completed_at
    status = observed.status if observed.completed_at is not None else existing.status
    if existing.completed_at is not None and observed.completed_at is None:
        status = existing.status

    return existing.model_copy(
        update={
            "provider": existing.provider,
            "status": status,
            "objective": observed.objective or existing.objective,
            "input_context_pack_id": (
                observed.input_context_pack_id or existing.input_context_pack_id
            ),
            "output_summary": _summary(merged_payload),
            "started_at": existing.started_at or observed.started_at,
            "completed_at": completed_at,
        }
    )


def _same_run(left: AgentRun, right: AgentRun) -> bool:
    return left.model_dump() == right.model_dump()


def _evidence_by_ticket(db: Database) -> dict[UUID, list[Evidence]]:
    evidence = EvidenceRepo(db).list()
    by_id = {record.id: record for record in evidence}
    grouped: dict[UUID, dict[UUID, Evidence]] = defaultdict(dict)
    for record in evidence:
        if record.ticket_id is not None:
            grouped[record.ticket_id][record.id] = record
    for check in VerificationCheckRepo(db).list():
        for evidence_id in check.evidence_ids:
            linked = by_id.get(evidence_id)
            if linked is not None:
                grouped[check.ticket_id][linked.id] = linked
    return {
        ticket_id: sorted(records.values(), key=lambda item: (item.created_at, item.id))
        for ticket_id, records in grouped.items()
    }


def reconstruct_agent_runs(
    *,
    tickets: TicketRepo,
    db: Database,
    issue_descriptions_by_id: Mapping[str, str | None],
    now: datetime,
) -> AgentRunReconstructionResult:
    """Create/update observed AgentRun rows for dispatch cycles.

    This function performs no Linear calls. The caller passes descriptions from
    the already-fetched board; all other inputs are local storage reads. Missing
    descriptions, evidence, verification checks, or handoff transitions simply
    leave nullable fields empty.
    """

    transitions_by_ticket: dict[UUID, list[TicketStatusTransition]] = defaultdict(list)
    for transition in TicketStatusTransitionRepo(db).list_all():
        transitions_by_ticket[transition.ticket_id].append(transition)

    evidence = _evidence_by_ticket(db)
    runs = AgentRunRepo(db)
    created = 0
    updated = 0

    for ticket in tickets.list():
        description = (
            issue_descriptions_by_id.get(ticket.external_linear_id)
            if ticket.external_linear_id is not None
            else None
        )
        pack = _pack_header(description)
        existing_by_dispatch = {
            str(agent_run_observation(run).get("dispatch_transition_id")): run
            for run in runs.list_for_ticket(ticket.id)
            if agent_run_observation(run).get("dispatch_transition_id") is not None
        }
        for cycle in _cycles(transitions_by_ticket.get(ticket.id, [])):
            cycle_evidence = _evidence_for_cycle(evidence.get(ticket.id, []), cycle)
            observed = _new_run(
                ticket,
                cycle,
                pack=pack,
                evidence=cycle_evidence,
                now=now,
            )
            dispatch_id = str(cycle.dispatch.id)
            existing = existing_by_dispatch.get(dispatch_id)
            if existing is None:
                runs.add(observed)
                existing_by_dispatch[dispatch_id] = observed
                created += 1
                continue
            merged = _merge(existing, observed)
            if not _same_run(existing, merged):
                runs.replace(merged)
                existing_by_dispatch[dispatch_id] = merged
                updated += 1

    return AgentRunReconstructionResult(created=created, updated=updated)

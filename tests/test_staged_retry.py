"""ATLAS-109: bounded directed retry on a stage projection-bound graze.

When a tickets stage emits valid JSON that violates a §3.11 field bound (the
measured case: a ticket with 8 ``acceptance_criteria`` against the ≤7 cap), the
orchestrator re-calls that stage WITH a directed correction up to
``MAX_STAGE_ATTEMPTS`` total attempts, then fails honestly. The cap is enforced,
never relaxed. Two failure classes must NOT retry: a truncation (capacity, not a
graze — ATLAS-101 behaviour) and a genuine non-JSON output (structurally broken,
a re-roll won't fix it). Every test injects a sequenced fake client, so CI makes
ZERO live calls.
"""

from __future__ import annotations

import json

import pytest

# Reuse the staged fixtures: the sequenced fake client and the canned item
# builders that already back the §4.1 worked example.
from test_staged_pipeline import SequencedFakeClient, _context, _epic, _ticket

from atlas.planning.client import TruncatedOutputError
from atlas.planning.proposal import parse_proposal
from atlas.planning.staged import (
    MAX_STAGE_ATTEMPTS,
    STAGE_TICKETS_VERSION,
    StageOutputError,
    StageProjectionError,
    StageTruncatedError,
    TemplateStagedGenerator,
)

_CORRECTION_HEADING = "Correction — your previous attempt was rejected"


def _over_bound_ticket(title: str, epic_ref: str) -> dict[str, object]:
    """A ticket grazing the ≤7 acceptance_criteria cap by exactly one (the
    measured overshoot shape): valid JSON, one bound violated."""
    ticket = _ticket(title, epic_ref)
    ticket["acceptance_criteria"] = [f"Criterion {i}." for i in range(8)]
    return ticket


def _epics_output() -> str:
    return json.dumps({"epics": [_epic("Planning Engine")], "planner_notes": []})


def _tickets_output(ticket: dict[str, object]) -> str:
    return json.dumps({"tickets": [ticket], "planner_notes": []})


def _deps_output() -> str:
    return json.dumps({"dependencies": [], "planner_notes": []})


# --- the projection graze retries with a directed correction and succeeds -----


def test_tickets_stage_retries_a_graze_and_assembles_the_compliant_output() -> None:
    # Over-bound first, compliant on the retry: the compliant output is the one
    # assembled — the stage succeeds, no data dropped, the ≤7 cap honoured.
    client = SequencedFakeClient(
        [
            _epics_output(),
            _tickets_output(_over_bound_ticket("Ingestion", "new_epic:0")),
            _tickets_output(_ticket("Ingestion", "new_epic:0")),
            _deps_output(),
        ]
    )
    result = TemplateStagedGenerator().generate(client=client, context=_context())

    proposal = parse_proposal(result.assembled_json)
    assert [t.title for t in proposal.tickets] == ["Ingestion"]
    # The assembled ticket is the compliant one (1 criterion), not the graze.
    assert len(proposal.tickets[0].acceptance_criteria) == 1
    # Four calls: epics, the grazing tickets call, the directed retry, deps.
    assert len(client.prompts) == 4
    # Provenance (gap 3): the retry attempt is a distinct, self-describing
    # StageRecord — the graze and its correction are auditable, not hidden.
    assert [r.stage for r in result.stage_records] == [
        "epics",
        "tickets:new_epic:0",
        "tickets:new_epic:0 (retry 1)",
        "dependencies",
    ]
    calls = [request.logical_call for request in client.requests]
    assert [call.identity.stage for call in calls] == [
        "epics",
        "tickets.new_epic.0",
        "tickets.new_epic.0",
        "dependencies",
    ]
    assert [call.identity.logical_attempt_no for call in calls] == [0, 0, 1, 0]
    assert len({call.identity.execution.execution_id for call in calls}) == 1
    assert all(call.physical_attempts == () for call in calls)

    first_segments = {item.name: item for item in calls[1].prompt_segments}
    retry_segments = {item.name: item for item in calls[2].prompt_segments}
    for stable in ("documents", "anchors", "backlog", "schema"):
        assert retry_segments[stable] == first_segments[stable]
    assert retry_segments["dynamic_stage"] != first_segments["dynamic_stage"]
    # Each attempt hashed its own raw bytes (ATLAS-108 invariant): the grazing
    # attempt and the compliant attempt have different raw_output_hashes.
    graze_hash = result.stage_records[1].raw_output_hash
    retry_hash = result.stage_records[2].raw_output_hash
    assert graze_hash != retry_hash


# --- a persistent violation fails honestly after the attempt limit -----------


def test_tickets_stage_fails_honestly_after_exhausting_retries() -> None:
    # Over-bound on every attempt: after MAX_STAGE_ATTEMPTS the stage raises the
    # typed StageOutputError naming the stage and the violation — enforced, not
    # relaxed. Dependencies is never reached.
    over = _tickets_output(_over_bound_ticket("Ingestion", "new_epic:0"))
    client = SequencedFakeClient([_epics_output(), over, over, over, _deps_output()])

    with pytest.raises(StageProjectionError) as excinfo:
        TemplateStagedGenerator().generate(client=client, context=_context())

    error = excinfo.value
    # It is the existing typed failure (a StageOutputError), not a new type.
    assert isinstance(error, StageOutputError)
    assert "tickets:new_epic:0" in error.stage  # names the stage
    assert "acceptance_criteria" in str(error)  # names the violation
    # Exactly MAX_STAGE_ATTEMPTS tickets calls after the single epics call;
    # the dependencies stage never ran.
    assert len(client.prompts) == 1 + MAX_STAGE_ATTEMPTS


# --- truncation MUST NOT retry (ATLAS-101 preserved) -------------------------


def test_truncation_on_the_tickets_stage_does_not_retry() -> None:
    # The tickets call truncates: a capacity problem (ATLAS-106 territory), not a
    # compliance graze. It fails immediately, naming the stage — no re-roll.
    client = SequencedFakeClient(
        [
            _epics_output(),
            _tickets_output(_ticket("Ingestion", "new_epic:0")),
            _deps_output(),
        ],
        raise_on=1,
        raise_error=TruncatedOutputError(
            raw_output='{"tickets": [cut', max_tokens=64000
        ),
    )

    with pytest.raises(StageTruncatedError) as excinfo:
        TemplateStagedGenerator().generate(client=client, context=_context())

    assert excinfo.value.stage == "tickets:new_epic:0"
    # No retry: the epics call plus the one truncated tickets call, nothing more.
    assert len(client.prompts) == 2


# --- a genuine non-JSON output MUST NOT retry --------------------------------


def test_non_json_on_the_tickets_stage_does_not_retry() -> None:
    # Structurally broken output (no JSON object at all): a re-roll won't make it
    # JSON, so it fails immediately as the base StageOutputError — NOT the
    # retryable StageProjectionError.
    client = SequencedFakeClient(
        [
            _epics_output(),
            "completely invalid output with no object",
            _deps_output(),
        ]
    )

    with pytest.raises(StageOutputError) as excinfo:
        TemplateStagedGenerator().generate(client=client, context=_context())

    error = excinfo.value
    assert not isinstance(error, StageProjectionError)  # decode failure, not bound
    assert "not valid JSON" in str(error)
    # No retry: the epics call plus the one non-JSON tickets call.
    assert len(client.prompts) == 2


# --- the correction is directed: absent first, present and actionable on retry -


def test_correction_is_absent_on_first_attempt_and_directed_on_retry() -> None:
    client = SequencedFakeClient(
        [
            _epics_output(),
            _tickets_output(_over_bound_ticket("Ingestion", "new_epic:0")),
            _tickets_output(_ticket("Ingestion", "new_epic:0")),
            _deps_output(),
        ]
    )
    TemplateStagedGenerator().generate(client=client, context=_context())

    first_tickets_prompt = client.prompts[1]
    retry_tickets_prompt = client.prompts[2]

    # The correction block renders only on the retry.
    assert _CORRECTION_HEADING not in first_tickets_prompt
    assert _CORRECTION_HEADING in retry_tickets_prompt
    # Directed (D2): names the field, the offending ticket, and the bound, and
    # forecloses resolving the violation the wrong way.
    assert "acceptance_criteria" in retry_tickets_prompt
    assert 'ticket "Ingestion"' in retry_tickets_prompt
    assert "at most 7" in retry_tickets_prompt
    assert "do NOT pad" in retry_tickets_prompt
    assert "do NOT drop" in retry_tickets_prompt


# --- the live staged tickets version is v1.6.0 (re-plan seeding) -------------


def test_orchestrator_uses_tickets_v1_6_0() -> None:
    assert STAGE_TICKETS_VERSION == "planner-stage-tickets-v1.6.0"

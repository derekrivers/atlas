"""Staged-generation progress feedback (additive observability).

The staged path (ADR-0010) is otherwise silent for minutes. An injected,
typed ``PlanProgress`` callback emits one event per stage boundary and per epic
in stage 2; the CLI renders each to stderr. These tests pin the event sequence,
the per-attempt retry emission, the ``None``-callback no-op (so existing staged
tests are unaffected), and the pure CLI line-formatter. Every test injects the
sequenced fake client, so CI makes ZERO live calls.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from test_staged_pipeline import (
    SequencedFakeClient,
    _context,
    _ticket,
    worked_example_stage_outputs,
)
from test_staged_retry import _epics_output, _over_bound_ticket, _tickets_output

from atlas.cli import _format_plan_progress
from atlas.planning.progress import (
    STAGE_ASSEMBLY,
    STAGE_DEPENDENCIES,
    STAGE_EPICS,
    STAGE_TICKETS,
    PlanProgress,
)
from atlas.planning.staged import (
    StageProjectionError,
    StageTicketsOutput,
    TemplateStagedGenerator,
    _graze_summary,
)


def _deps_output() -> str:
    import json

    return json.dumps({"dependencies": [], "planner_notes": []})


# --- 1. event sequence & ordering --------------------------------------------


def test_emits_each_stage_boundary_in_order_with_1_based_epic_positions() -> None:
    # The §4.1 worked example has two epics, so stage 2 emits two ticket events,
    # 1-based and in epic-emission order, each carrying its epic's title.
    events: list[PlanProgress] = []
    recorder = events.append
    client = SequencedFakeClient(worked_example_stage_outputs())

    TemplateStagedGenerator().generate(
        client=client, context=_context(), on_progress=recorder
    )

    # Exactly this sequence: a 0-based index (("tickets", 0, 2)), reordered
    # epics, a missing dependencies/assembly event, or a total that isn't the
    # epic count would all fail here.
    assert [(e.stage, e.index, e.total) for e in events] == [
        (STAGE_EPICS, None, None),
        (STAGE_TICKETS, 1, 2),
        (STAGE_TICKETS, 2, 2),
        (STAGE_DEPENDENCIES, None, None),
        (STAGE_ASSEMBLY, None, None),
    ]
    # Each tickets event's detail is the matching epic title, in emission order.
    ticket_details = [e.detail for e in events if e.stage == STAGE_TICKETS]
    assert ticket_details == ["Planning Engine", "Dependency Engine"]
    # No-behaviour-change guard: every first-attempt event carries reason=None,
    # so the rendered first-try lines are byte-identical to before this add-on.
    assert all(e.reason is None for e in events)


# --- 2. a retry emits its own attempt event ----------------------------------


def test_a_projection_graze_retry_emits_a_second_tickets_event() -> None:
    # One epic whose first ticket grazes the ≤7 acceptance_criteria bound, then a
    # compliant retry. The retry must be reported: two tickets events fire for
    # the epic, attempt=0 then attempt=1, same index/total/detail.
    events: list[PlanProgress] = []
    client = SequencedFakeClient(
        [
            _epics_output(),
            _tickets_output(_over_bound_ticket("Ingestion", "new_epic:0")),
            _tickets_output(_ticket("Ingestion", "new_epic:0")),
            _deps_output(),
        ]
    )

    TemplateStagedGenerator().generate(
        client=client, context=_context(), on_progress=events.append
    )

    tickets_events = [e for e in events if e.stage == STAGE_TICKETS]
    # Two events, not one (the retry is not silently un-reported), and the retry
    # carries the same position — not a different index.
    assert [e.attempt for e in tickets_events] == [0, 1]
    assert all(
        (e.index, e.total, e.detail) == (1, 1, "Planning Engine")
        for e in tickets_events
    )
    # The first attempt carries no reason; the retry names the field it repairs
    # (the over-bound fixture violates only acceptance_criteria). A retry with
    # reason is None when a graze occurred would be the wrong answer.
    first, retry = tickets_events
    assert first.reason is None
    assert retry.reason is not None
    assert "acceptance_criteria" in retry.reason


# --- 2. _graze_summary is pure and names the violated field ------------------


def test_graze_summary_names_the_violated_field() -> None:
    # The over-bound fixture trips only the ≤7 acceptance_criteria bound; the
    # summary is exactly that field name — not an empty string, not the verbose
    # model-facing correction paragraph.
    over = _over_bound_ticket("Ingestion", "new_epic:0")
    with pytest.raises(ValidationError) as excinfo:
        StageTicketsOutput.model_validate({"tickets": [over], "planner_notes": []})
    error = StageProjectionError(
        stage="tickets:new_epic:0",
        records=[],
        raw_output=_tickets_output(over),
        message="grazed",
        validation_error=excinfo.value,
    )
    assert _graze_summary(error) == "acceptance_criteria"


# --- 3. a None callback is a no-op (guards the back-compat default) -----------


def test_none_callback_yields_the_same_assembly_and_raises_nothing() -> None:
    # generate(on_progress=None) is byte-identical to the no-callback call: the
    # assembled JSON matches and nothing is raised.
    with_none = (
        TemplateStagedGenerator()
        .generate(
            client=SequencedFakeClient(worked_example_stage_outputs()),
            context=_context(),
            on_progress=None,
        )
        .assembled_json
    )
    without = (
        TemplateStagedGenerator()
        .generate(
            client=SequencedFakeClient(worked_example_stage_outputs()),
            context=_context(),
        )
        .assembled_json
    )
    assert with_none == without


# --- 4. the CLI line formatter is pure ---------------------------------------


def test_format_plan_progress_maps_each_stage_to_its_line() -> None:
    assert _format_plan_progress(PlanProgress(STAGE_EPICS)) == (
        "Stage 1/3 · epics — generating…"
    )
    assert (
        _format_plan_progress(PlanProgress(STAGE_TICKETS, 4, 10, "Knowledge Core"))
        == "Stage 2/3 · tickets — epic 4/10: Knowledge Core"
    )
    assert _format_plan_progress(PlanProgress(STAGE_DEPENDENCIES)) == (
        "Stage 3/3 · dependencies — generating…"
    )
    assert _format_plan_progress(PlanProgress(STAGE_ASSEMBLY)) == (
        "Stage 3/3 · assembling proposal…"
    )


def test_format_plan_progress_renders_retry_suffix_only_when_retrying() -> None:
    # attempt=1 (a real retry) appends "(retry 1)"; attempt=0 (the first try)
    # must NOT render "(retry 0)".
    first = _format_plan_progress(PlanProgress(STAGE_TICKETS, 1, 2, "E", attempt=0))
    retry = _format_plan_progress(PlanProgress(STAGE_TICKETS, 1, 2, "E", attempt=1))
    assert first == "Stage 2/3 · tickets — epic 1/2: E"
    assert "(retry" not in first
    assert retry == "Stage 2/3 · tickets — epic 1/2: E (retry 1)"


def test_format_plan_progress_renders_the_graze_reason_on_a_retry() -> None:
    # attempt=1 with a reason names the grazed field in the suffix; attempt=1
    # without a reason stays a bare (retry 1); attempt=0 never shows a reason.
    with_reason = _format_plan_progress(
        PlanProgress(STAGE_TICKETS, 1, 2, "E", attempt=1, reason="acceptance_criteria")
    )
    assert with_reason is not None
    assert "(retry 1" in with_reason
    assert "acceptance_criteria" in with_reason
    assert with_reason == (
        "Stage 2/3 · tickets — epic 1/2: E (retry 1 — acceptance_criteria)"
    )

    bare = _format_plan_progress(
        PlanProgress(STAGE_TICKETS, 1, 2, "E", attempt=1, reason=None)
    )
    assert bare == "Stage 2/3 · tickets — epic 1/2: E (retry 1)"

    # A reason on the first try must never render a retry suffix (no (retry 0 …)).
    first = _format_plan_progress(
        PlanProgress(STAGE_TICKETS, 1, 2, "E", attempt=0, reason="acceptance_criteria")
    )
    assert first == "Stage 2/3 · tickets — epic 1/2: E"
    assert "retry" not in first
    assert "acceptance_criteria" not in first

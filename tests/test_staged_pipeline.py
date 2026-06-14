"""ATLAS-104: staged generation through the orchestrator and run_plan.

Every test injects fakes — a sequenced fake PlannerClient returning canned
per-stage JSON, or a fake generator — so CI makes ZERO live calls. The
staged path composes the three ATLAS-103 templates, assembles one §3.11
envelope, and flows into the unchanged parse -> gates -> reconcile ->
PlanRun path. The single-call path stays the default and is unaffected.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from planner_fakes import FAKE_IDENTITY
from test_models_validation import epic_kwargs
from test_plan_pipeline import NOW, fixture_repo, fresh_db, proposal_json

from atlas import cli
from atlas.core.models import Epic, PlanRunStatus
from atlas.planning.client import TruncatedOutputError
from atlas.planning.pipeline import StagedReplanUnsupportedError, run_plan
from atlas.planning.proposal import parse_proposal
from atlas.planning.staged import (
    STAGE_DEPENDENCIES_VERSION,
    STAGE_EPICS_VERSION,
    STAGE_TICKETS_VERSION,
    StageContext,
    TemplateStagedGenerator,
)
from atlas.storage import EpicRepo, PlanRunRepo

ANCHOR_EPIC = "docs/atlas/plan.md#planning"
ANCHOR_TICKET = "docs/atlas/plan.md#backlog"


def _epic(title: str) -> dict[str, Any]:
    return {
        "key": None,
        "title": title,
        "description": f"{title} description.",
        "objective": f"{title} objective.",
        "priority": 10,
        "risk_level": "medium",
        "source_anchor": ANCHOR_EPIC,
    }


def _ticket(title: str, epic_ref: str) -> dict[str, Any]:
    return {
        "key": None,
        "epic_ref": epic_ref,
        "title": title,
        "objective": f"{title} objective.",
        "context": "Phase 2.5.",
        "ticket_type": "feature",
        "risk_level": "medium",
        "priority": 10,
        "source_anchor": ANCHOR_TICKET,
        "relevant_docs": [],
        "acceptance_criteria": [f"{title} is done."],
        "non_goals": ["Out of scope."],
        "test_requirements": ["Tested."],
        "implementation_notes": [],
        "documentation_requirements": [],
        "definition_of_done": ["Merged."],
    }


def worked_example_stage_outputs() -> list[str]:
    """Canned per-stage JSON for the §4.1 worked example, in call order:
    epics, tickets(new_epic:0), tickets(new_epic:1), dependencies."""
    return [
        json.dumps(
            {
                "epics": [_epic("Planning Engine"), _epic("Dependency Engine")],
                "planner_notes": [],
            }
        ),
        json.dumps(
            {
                "tickets": [
                    _ticket("Document ingestion", "new_epic:0"),
                    _ticket("Deterministic reconciler", "new_epic:0"),
                ],
                "planner_notes": [],
            }
        ),
        json.dumps(
            {
                "tickets": [
                    _ticket("Graph schema and build", "new_epic:1"),
                    _ticket("Readiness detection", "new_epic:1"),
                ],
                "planner_notes": [],
            }
        ),
        json.dumps(
            {
                "dependencies": [
                    {
                        "source": "new:3",
                        "target": "new:1",
                        "dependency_type": "depends_on",
                        "reason": "Readiness detection needs the reconciler first.",
                    }
                ],
                "planner_notes": [],
            }
        ),
    ]


class SequencedFakeClient:
    """Returns canned outputs in call order; optionally raises a given error
    on the nth call. Records every prompt — the proof CI never calls live."""

    def __init__(
        self,
        outputs: list[str],
        *,
        raise_on: int | None = None,
        raise_error: Exception | None = None,
    ) -> None:
        self._outputs = outputs
        self._raise_on = raise_on
        self._raise_error = raise_error
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        index = len(self.prompts)
        self.prompts.append(prompt)
        if self._raise_on is not None and index == self._raise_on:
            assert self._raise_error is not None
            raise self._raise_error
        return self._outputs[index]


# --- the orchestrator assembles the §4.1 envelope (zero live calls) ----------


def test_template_generator_assembles_section_4_1() -> None:
    client = SequencedFakeClient(worked_example_stage_outputs())
    generator = TemplateStagedGenerator()
    result = generator.generate(
        client=client,
        context=_context(),
    )
    proposal = parse_proposal(result.assembled_json)
    assert len(proposal.epics) == 2
    assert [t.title for t in proposal.tickets] == [
        "Document ingestion",
        "Deterministic reconciler",
        "Graph schema and build",
        "Readiness detection",
    ]
    assert proposal.dependencies[0].source == "new:3"
    # Four calls: epics, two ticket batches, dependencies — all to the fake.
    assert len(client.prompts) == 4
    # Per-stage records are the ATLAS-105 seam: one per call.
    assert [r.stage for r in result.stage_records] == [
        "epics",
        "tickets:new_epic:0",
        "tickets:new_epic:1",
        "dependencies",
    ]
    # Composite prompt_version names each staged template once, in order.
    assert result.prompt_version == (
        f"staged[{STAGE_EPICS_VERSION}+{STAGE_TICKETS_VERSION}"
        f"+{STAGE_DEPENDENCIES_VERSION}]"
    )


def _context() -> StageContext:
    return StageContext(
        product_key="ATLAS",
        documents=[
            {"path": "docs/atlas/plan.md", "sha": "sha-1", "content": "# Planning\n"}
        ],
    )


# --- run_plan staged path: a proposed PlanRun with a coherent chain ----------


def test_run_plan_staged_path_persists_proposed_planrun(tmp_path: Any) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)

    # Deterministic assembled JSON for the §5.3 raw_output_hash assertion.
    expected_json = (
        TemplateStagedGenerator()
        .generate(
            client=SequencedFakeClient(worked_example_stage_outputs()),
            context=_context(),
        )
        .assembled_json
    )
    expected_hash = hashlib.sha256(expected_json.encode("utf-8")).hexdigest()

    result = run_plan(
        repo_root=repo,
        database=database,
        client=SequencedFakeClient(worked_example_stage_outputs()),
        identity=FAKE_IDENTITY,
        now=NOW,
        staged_generator=TemplateStagedGenerator(),
    )

    assert result.status is PlanRunStatus.PROPOSED
    run = result.plan_run
    # AT-5 provenance chain over the staged path: input_doc_shas -> composite
    # prompt_hash -> assembled raw_output_hash -> proposal.
    assert run.input_doc_shas  # documents were read
    assert run.prompt_version.startswith("staged[")
    assert run.raw_output_hash == expected_hash  # hash is over the assembled JSON
    assert len(run.proposal["tickets"]) == 4
    # The run is actually persisted.
    assert PlanRunRepo(database).get(run.id) is not None


def test_run_plan_staged_path_records_truncation_naming_the_stage(
    tmp_path: Any,
) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    # Truncate the second call: the ticket batch for new_epic:0.
    client = SequencedFakeClient(
        worked_example_stage_outputs(),
        raise_on=1,
        raise_error=TruncatedOutputError(
            raw_output='{"tickets": [cut', max_tokens=64000
        ),
    )

    result = run_plan(
        repo_root=repo,
        database=database,
        client=client,
        identity=FAKE_IDENTITY,
        now=NOW,
        staged_generator=TemplateStagedGenerator(),
    )

    assert result.status is PlanRunStatus.FAILED
    assert result.failure_reason is not None
    reason = json.loads(result.failure_reason)
    assert reason["stage"] == "truncation"
    assert reason["generation_stage"] == "tickets:new_epic:0"
    # A failed staged run is still auditable: provenance was recorded.
    assert result.plan_run.raw_output_hash
    assert result.plan_run.prompt_version.startswith("staged[")


def test_run_plan_staged_path_records_protocol_violation(tmp_path: Any) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    outputs = worked_example_stage_outputs()
    # Stage 3 references a ticket the environment never assigned.
    outputs[3] = json.dumps(
        {
            "dependencies": [
                {
                    "source": "new:99",
                    "target": "new:1",
                    "dependency_type": "depends_on",
                    "reason": "Out-of-range source.",
                }
            ],
            "planner_notes": [],
        }
    )
    result = run_plan(
        repo_root=repo,
        database=database,
        client=SequencedFakeClient(outputs),
        identity=FAKE_IDENTITY,
        now=NOW,
        staged_generator=TemplateStagedGenerator(),
    )
    assert result.status is PlanRunStatus.FAILED
    assert result.failure_reason is not None
    reason = json.loads(result.failure_reason)
    assert reason["stage"] == "staged_generation"
    assert "out of range" in reason["error"]


def test_run_plan_staged_refuses_a_nonempty_backlog(tmp_path: Any) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    EpicRepo(database).add(Epic(**epic_kwargs() | {"key": "ATLAS-E1"}))
    client = SequencedFakeClient(worked_example_stage_outputs())
    with pytest.raises(StagedReplanUnsupportedError, match="first-run only"):
        run_plan(
            repo_root=repo,
            database=database,
            client=client,
            identity=FAKE_IDENTITY,
            now=NOW,
            staged_generator=TemplateStagedGenerator(),
        )
    # Clean exit: the staged generator was never called, and no PlanRun exists.
    assert client.prompts == []
    assert PlanRunRepo(database).list() == []


# --- the single-call path stays the default and is unchanged -----------------


def test_default_path_is_single_call_when_no_generator(tmp_path: Any) -> None:
    from planner_fakes import FakePlannerClient

    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    result = run_plan(
        repo_root=repo,
        database=database,
        client=FakePlannerClient(proposal_json()),
        identity=FAKE_IDENTITY,
        now=NOW,
    )
    assert result.status is PlanRunStatus.PROPOSED
    # The single-call prompt_version is the live single-call template, not a
    # composite — staged is purely additive.
    assert result.plan_run.prompt_version == "planner-v1.1.0"


# --- the CLI --staged flag routes to the staged path -------------------------


def test_cli_plan_staged_flag_routes_to_staged_path(tmp_path: Any) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    client = SequencedFakeClient(worked_example_stage_outputs())
    exit_code = cli.main(
        ["plan", "--staged", "--repo", str(repo)],
        database=database,
        client=client,
        identity=FAKE_IDENTITY,
    )
    assert exit_code == 0
    # The flag constructed a real TemplateStagedGenerator over the fake client:
    # four staged calls, a persisted run.
    assert len(client.prompts) == 4
    assert len(PlanRunRepo(database).list()) == 1

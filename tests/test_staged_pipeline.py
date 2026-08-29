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
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import yaml
from planner_fakes import FAKE_IDENTITY
from test_models_validation import epic_kwargs, ticket_kwargs
from test_plan_pipeline import (
    NOW,
    PRODUCT_MD,
    fixture_repo,
    fresh_db,
    make_repo,
    proposal_json,
)

import atlas.planning.pipeline as pipeline_module
from atlas import cli
from atlas.core.anchors import AnchorIndex
from atlas.core.models import Epic, PlanRunStatus, Ticket
from atlas.core.models.planner_call_telemetry import (
    PlannerExecutionParameters,
    PlannerIdentity,
    PlanningExecutionIdentity,
)
from atlas.planning.client import (
    PlannerCallRequest,
    PlannerCallResult,
    TruncatedOutputError,
)
from atlas.planning.ingestion import collect_input_documents
from atlas.planning.pipeline import StagedReplanUnsupportedError, run_plan
from atlas.planning.proposal import parse_proposal
from atlas.planning.renderer import UnknownTemplateVersionError
from atlas.planning.seed import render_backlog_yaml
from atlas.planning.staged import (
    STAGE_DEPENDENCIES_VERSION,
    STAGE_EPICS_VERSION,
    STAGE_TICKETS_VERSION,
    StageContext,
    StageEpicsOutput,
    TemplateStagedGenerator,
)
from atlas.storage import EpicRepo, PlanRunRepo, TicketRepo

ANCHOR_EPIC = "docs/atlas/plan.md#planning"
ANCHOR_TICKET = "docs/atlas/plan.md#backlog"
TEST_EXECUTION_IDENTITY = PlanningExecutionIdentity(execution_id=uuid4())
TEST_PLANNER_IDENTITY = PlannerIdentity(provider="fake", model="fake-model-1")
TEST_EXECUTION_PARAMETERS = PlannerExecutionParameters(
    temperature=0,
    max_output_tokens=1024,
    streaming=False,
)
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "atlas/planning/prompts"


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
        "tags": ["planning"],
        "component": "planning",
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
        self.requests: list[PlannerCallRequest] = []

    def generate(self, prompt: str, request: PlannerCallRequest) -> PlannerCallResult:
        index = len(self.prompts)
        self.prompts.append(prompt)
        self.requests.append(request)
        if self._raise_on is not None and index == self._raise_on:
            assert self._raise_error is not None
            raise self._raise_error
        return PlannerCallResult(
            raw_output=self._outputs[index],
            logical_call=request.logical_call,
        )


# --- the orchestrator assembles the §4.1 envelope (zero live calls) ----------


def test_template_generator_assembles_section_4_1() -> None:
    client = SequencedFakeClient(worked_example_stage_outputs())
    generator = TemplateStagedGenerator()
    context = _context()
    result = generator.generate(
        client=client,
        context=context,
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
    # Every ordinary staged call belongs to the one durable execution, carries
    # its exact canonical stage, and stays at logical attempt zero.
    calls = [request.logical_call for request in client.requests]
    assert [call.identity.execution for call in calls] == [
        context.execution_identity
    ] * 4
    assert [call.identity.stage for call in calls] == [
        "epics",
        "tickets.new_epic.0",
        "tickets.new_epic.1",
        "dependencies",
    ]
    assert [call.identity.logical_attempt_no for call in calls] == [0, 0, 0, 0]
    assert [call.template.prompt_version for call in calls] == [
        STAGE_EPICS_VERSION,
        STAGE_TICKETS_VERSION,
        STAGE_TICKETS_VERSION,
        STAGE_DEPENDENCIES_VERSION,
    ]
    for prompt, call in zip(client.prompts, calls, strict=True):
        assert call.prompt_size.byte_count == len(prompt.encode("utf-8"))
        assert call.prompt_size.character_count == len(prompt)
        assert {segment.name for segment in call.prompt_segments} == {
            "documents",
            "anchors",
            "backlog",
            "schema",
            "dynamic_stage",
        }
        prompt_identity = next(
            item for item in call.input_identities if item.name == "rendered_prompt"
        )
        assert (
            prompt_identity.digest == hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        )


def test_staged_template_preflight_fails_before_any_provider_call(
    tmp_path: Path,
) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    for version in (STAGE_EPICS_VERSION, STAGE_TICKETS_VERSION):
        name = f"{version}.md.j2"
        (prompts_dir / name).write_text(
            (PROMPTS_DIR / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    client = SequencedFakeClient(worked_example_stage_outputs())

    with pytest.raises(UnknownTemplateVersionError, match=STAGE_DEPENDENCIES_VERSION):
        run_plan(
            repo_root=repo,
            database=database,
            client=client,
            identity=FAKE_IDENTITY,
            now=NOW,
            staged_generator=TemplateStagedGenerator(),
            prompts_dir=prompts_dir,
        )

    assert client.prompts == []
    assert client.requests == []


# --- ATLAS-108: fence-tolerant parsing on the staged path --------------------


def test_real_fenced_epics_output_parses_to_correct_stage_output() -> None:
    # The exact live failure: the epics stage returned valid JSON in a ```json
    # fence. The tolerant extraction recovers the correct StageEpicsOutput.
    from proposal_fixtures import FENCED_EPICS_OUTPUT

    from atlas.planning import extract_json_object

    output = StageEpicsOutput.model_validate(
        json.loads(extract_json_object(FENCED_EPICS_OUTPUT))
    )
    assert [e.title for e in output.epics] == ["Knowledge Core", "Dependency Engine"]


def test_staged_run_tolerates_a_fence_on_every_stage() -> None:
    # All three stage call sites use the tolerant parse: fence each of the four
    # canned outputs and the run still assembles and persists.
    from proposal_fixtures import fenced

    fenced_outputs = [fenced(o) for o in worked_example_stage_outputs()]
    client = SequencedFakeClient(fenced_outputs)
    result = TemplateStagedGenerator().generate(client=client, context=_context())
    proposal = parse_proposal(result.assembled_json)
    assert [t.title for t in proposal.tickets] == [
        "Document ingestion",
        "Deterministic reconciler",
        "Graph schema and build",
        "Readiness detection",
    ]


def test_staged_hash_invariant_is_over_the_unstripped_stage_bytes() -> None:
    # Gap 2 on the staged path: each StageRecord.raw_output_hash is the hash of
    # the FENCED bytes the model sent, not the stripped object — provenance
    # records what the model actually returned, fence and all.
    from proposal_fixtures import fenced

    fenced_outputs = [fenced(o) for o in worked_example_stage_outputs()]
    client = SequencedFakeClient(fenced_outputs)
    result = TemplateStagedGenerator().generate(client=client, context=_context())
    for record, sent in zip(result.stage_records, fenced_outputs, strict=True):
        expected = hashlib.sha256(sent.encode("utf-8")).hexdigest()
        assert record.raw_output_hash == expected


def _context() -> StageContext:
    return StageContext(
        execution_identity=TEST_EXECUTION_IDENTITY,
        planner_identity=TEST_PLANNER_IDENTITY,
        execution_parameters=TEST_EXECUTION_PARAMETERS,
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


def test_run_plan_staged_renders_the_anchor_list_and_passes_gate_4(
    tmp_path: Any,
) -> None:
    # ATLAS-111: the pipeline renders the valid-anchor list (from the same
    # AnchorIndex gate 4 validates against) into the epics and tickets stages,
    # and a run whose stage outputs anchor to entries IN that list passes gate 4
    # end to end (reaches PROPOSED). The worked-example anchors
    # (docs/atlas/plan.md#planning / #backlog) resolve against the fixture.
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    client = SequencedFakeClient(worked_example_stage_outputs())

    result = run_plan(
        repo_root=repo,
        database=database,
        client=client,
        identity=FAKE_IDENTITY,
        now=NOW,
        staged_generator=TemplateStagedGenerator(),
    )

    # Gate 4 passes (anchors drawn from the rendered list resolve).
    assert result.status is PlanRunStatus.PROPOSED
    # The epics stage (prompt 0) and a tickets stage (prompt 1) each carry the
    # valid-anchor list, and the exact anchors the outputs used appear in it.
    epics_prompt, tickets_prompt = client.prompts[0], client.prompts[1]
    assert "## Valid source anchors" in epics_prompt
    assert "## Valid source anchors" in tickets_prompt
    assert ANCHOR_EPIC in epics_prompt  # docs/atlas/plan.md#planning
    assert ANCHOR_TICKET in tickets_prompt  # docs/atlas/plan.md#backlog
    # Select-not-construct instruction, not the old slug-construction rule
    # (whitespace-normalised: the instruction wraps across lines).
    assert "Do NOT construct, slugify, or guess an anchor" in " ".join(
        tickets_prompt.split()
    )


def test_run_plan_staged_path_persists_generation_stages(tmp_path: Any) -> None:
    # ATLAS-105: the per-stage records ATLAS-104 produces are persisted on
    # PlanRun.generation_stages — one record per call, byte-matching the
    # generator's stage_records — and survive a round-trip through storage.
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)

    # Mirror run_plan's own document payload so the expected per-stage prompt
    # hashes are computed against the same rendered prompts (prompt_hash depends
    # on the ingested documents, raw_output_hash on the canned stage outputs).
    documents = collect_input_documents(repo)
    payload = [
        {"path": doc.path, "sha": doc.sha, "content": doc.content} for doc in documents
    ]
    # Mirror run_plan's valid-anchor wiring (ATLAS-111): the prompt_hash now
    # depends on the rendered anchor list, so the expected generator must be fed
    # the same anchors the pipeline derives from its AnchorIndex.
    valid_anchors = AnchorIndex.build(documents).anchor_choices()
    expected_records = (
        TemplateStagedGenerator()
        .generate(
            client=SequencedFakeClient(worked_example_stage_outputs()),
            context=StageContext(
                execution_identity=TEST_EXECUTION_IDENTITY,
                planner_identity=TEST_PLANNER_IDENTITY,
                execution_parameters=TEST_EXECUTION_PARAMETERS,
                product_key="ATLAS",
                documents=payload,
                valid_anchors=valid_anchors,
            ),
        )
        .stage_records
    )
    expected_stages = [
        {
            "stage": r.stage,
            "prompt_version": r.prompt_version,
            "prompt_hash": r.prompt_hash,
            "raw_output_hash": r.raw_output_hash,
        }
        for r in expected_records
    ]

    result = run_plan(
        repo_root=repo,
        database=database,
        client=SequencedFakeClient(worked_example_stage_outputs()),
        identity=FAKE_IDENTITY,
        now=NOW,
        staged_generator=TemplateStagedGenerator(),
    )

    # Four stages, in call order, each carrying the verbatim stage label that
    # disambiguates the two ticket batches (same prompt_version, distinct stage).
    assert [s["stage"] for s in result.plan_run.generation_stages] == [
        "epics",
        "tickets:new_epic:0",
        "tickets:new_epic:1",
        "dependencies",
    ]
    assert result.plan_run.generation_stages == expected_stages
    # Persisted, not just in-memory: it reads back identically.
    stored = PlanRunRepo(database).get(result.plan_run.id)
    assert stored is not None
    assert stored.generation_stages == expected_stages
    # The top-level composite chain ATLAS-104 established is unchanged: the
    # composite prompt_hash is derived from these per-stage prompt_hashes, and
    # the assembled raw_output_hash differs from every per-stage hash.
    assert result.plan_run.prompt_version.startswith("staged[")
    per_stage_raw_hashes = {s["raw_output_hash"] for s in expected_stages}
    assert result.plan_run.raw_output_hash not in per_stage_raw_hashes


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


# --- re-plan seeding (ATLAS-144): a non-empty backlog seeds and re-plans ------

NEW_TICKET_TITLE = "New work"
NEW_TICKET_ANCHOR = "docs/atlas/plan.md#new-work"

# A doc with a distinct heading per fixture item, so every epic/ticket anchors
# to its own resolvable section (gate 4) and the reconciler cannot cross-match a
# dropped ticket to the new one by shared anchor/title — the AC-6 probe needs
# each identity to be genuinely distinct.
REPLAN_PLAN_MD = (
    "# Planning\n\n"
    "## Epic one\n\nEpic one.\n\n"
    "## Epic two\n\nEpic two.\n\n"
    "## Ticket one\n\nTicket one.\n\n"
    "## Ticket two\n\nTicket two.\n\n"
    "## Ticket three\n\nTicket three.\n\n"
    "## Ticket four\n\nTicket four.\n\n"
    "## New work\n\nNew work.\n"
)


def replan_repo(tmp_path: Any) -> Any:
    return make_repo(
        tmp_path, {"PRODUCT.md": PRODUCT_MD, "docs/atlas/plan.md": REPLAN_PLAN_MD}
    )


def seed_backlog(database: Any) -> tuple[list[Epic], list[Ticket]]:
    """A non-empty fixture store for ``replan_repo``: 2 epics / 4 tickets, real
    keys, every ticket attached to an epic, none frozen, each anchored to its
    own distinct heading (so a restated proposal passes gate 4 and identities do
    not collide). Returns them for the caller's assertions."""
    epics = []
    for key, title, slug in (
        ("ATLAS-E1", "Epic one", "epic-one"),
        ("ATLAS-E2", "Epic two", "epic-two"),
    ):
        epic = Epic(
            **epic_kwargs()
            | {
                "id": uuid4(),
                "key": key,
                "title": title,
                "source_anchor": f"docs/atlas/plan.md#{slug}",
            }
        )
        EpicRepo(database).add(epic)
        epics.append(epic)
    tickets = []
    for key, title, slug, epic in (
        ("ATLAS-1", "Ticket one", "ticket-one", epics[0]),
        ("ATLAS-2", "Ticket two", "ticket-two", epics[0]),
        ("ATLAS-3", "Ticket three", "ticket-three", epics[1]),
        ("ATLAS-4", "Ticket four", "ticket-four", epics[1]),
    ):
        ticket = Ticket(
            **ticket_kwargs()
            | {
                "id": uuid4(),
                "key": key,
                "title": title,
                "epic_id": epic.id,
                "status": "backlog",
                "source_anchor": f"docs/atlas/plan.md#{slug}",
            }
        )
        TicketRepo(database).add(ticket)
        tickets.append(ticket)
    return epics, tickets


class SeedFollowingClient:
    """A faithful compliant-model fake: it READS the ``<current_backlog>`` seed
    the environment rendered into each stage prompt and re-emits exactly those
    items under their echoed keys and anchors (plus one brand-new ticket on the
    first tickets stage). Because its output is a function of the RENDERED seed,
    mutating the seed renderer propagates all the way to the assembled proposal
    — which is what makes the AC-6 drop-a-ticket probe a genuine data-loss
    test, not a tautology. Records every prompt (CI never calls live)."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.requests: list[PlannerCallRequest] = []
        self._tickets_calls = 0

    def generate(self, prompt: str, request: PlannerCallRequest) -> PlannerCallResult:
        self.prompts.append(prompt)
        self.requests.append(request)
        if "stage 1 of 3" in prompt:
            raw_output = self._epics(prompt)
        elif "stage 2 of 3" in prompt:
            raw_output = self._tickets(prompt)
        else:
            raw_output = json.dumps({"dependencies": [], "planner_notes": []})
        return PlannerCallResult(
            raw_output=raw_output,
            logical_call=request.logical_call,
        )

    @staticmethod
    def _seed(prompt: str) -> dict[str, Any]:
        match = re.search(r"<current_backlog>\n(.*?)\n</current_backlog>", prompt, re.S)
        return yaml.safe_load(match.group(1)) if match else {}

    def _epics(self, prompt: str) -> str:
        seed = self._seed(prompt)
        epics = [
            _epic(entry["title"])
            | {"key": entry["key"], "source_anchor": entry["source_anchor"]}
            for entry in seed.get("epics", [])
        ]
        return json.dumps({"epics": epics, "planner_notes": []})

    def _tickets(self, prompt: str) -> str:
        self._tickets_calls += 1
        match = re.search(r'<target_epic index="([^"]+)">', prompt)
        assert match is not None
        epic_index = match.group(1)
        seed = self._seed(prompt)
        tickets = [
            _ticket(entry["title"], epic_index)
            | {"key": entry["key"], "source_anchor": entry["source_anchor"]}
            for entry in seed.get("tickets", [])
        ]
        if self._tickets_calls == 1:  # one brand-new ticket, keyless
            tickets.append(
                _ticket(NEW_TICKET_TITLE, epic_index)
                | {"source_anchor": NEW_TICKET_ANCHOR}
            )
        return json.dumps({"tickets": tickets, "planner_notes": []})


def _archived(diff: Any, kind: str) -> set[str]:
    return {
        entry.identity
        for entry in diff.entries
        if entry.entry_type == "PROPOSE_ARCHIVE" and entry.kind == kind
    }


def _added_tickets(diff: Any) -> list[Any]:
    return [
        entry
        for entry in diff.entries
        if entry.entry_type == "ADD" and entry.kind == "ticket"
    ]


def test_seeded_replan_no_archive_of_restated_tickets(tmp_path: Any) -> None:
    # AC-1: a seeded non-empty backlog re-plans to ONE full-state proposal in
    # which every pre-existing item is restated under its real key and the new
    # ticket carries no key; reconcile archives NOTHING restated and yields
    # exactly one CREATE. Byte-level assertions on the diff and proposal, not
    # counts alone.
    repo = replan_repo(tmp_path)
    database = fresh_db(tmp_path)
    seed_backlog(database)
    client = SeedFollowingClient()

    result = run_plan(
        repo_root=repo,
        database=database,
        client=client,
        identity=FAKE_IDENTITY,
        now=NOW,
        staged_generator=TemplateStagedGenerator(),
    )

    assert result.status is PlanRunStatus.PROPOSED
    assert result.diff is not None
    # No existing epic or ticket is archived — the seed made the model restate
    # them all. (This is the exact assertion AC-6 drives red.)
    assert _archived(result.diff, "ticket") == set()
    assert _archived(result.diff, "epic") == set()
    # Exactly one CREATE, and it is the new ticket (keyless, new:<n> identity).
    adds = _added_tickets(result.diff)
    assert len(adds) == 1
    assert adds[0].title == NEW_TICKET_TITLE
    assert adds[0].identity.startswith("new:")
    # The assembled proposal restates every existing key and carries exactly one
    # keyless ticket.
    proposal_tickets = result.plan_run.proposal["tickets"]
    keys = [t["key"] for t in proposal_tickets]
    assert {"ATLAS-1", "ATLAS-2", "ATLAS-3", "ATLAS-4"} <= set(keys)
    assert keys.count(None) == 1
    # Four staged calls (epics, two ticket batches, dependencies).
    assert len(client.prompts) == 4


def test_seeded_replan_records_seeded_versions_and_differs_from_first_run(
    tmp_path: Any,
) -> None:
    # AC-5 (amended): a seeded run pins the bumped seeded template versions, and
    # its epics-stage prompt_hash differs from a first run's over the same
    # corpus — the seed is load-bearing in provenance, so seeded and first runs
    # are distinguishable without a new PlanRun field.
    repo = fixture_repo(tmp_path)
    seeded_db = fresh_db(tmp_path / "seeded")
    seed_backlog(seeded_db)
    seeded = run_plan(
        repo_root=repo,
        database=seeded_db,
        client=SeedFollowingClient(),
        identity=FAKE_IDENTITY,
        now=NOW,
        staged_generator=TemplateStagedGenerator(),
    )
    first = run_plan(
        repo_root=repo,
        database=fresh_db(tmp_path / "first"),
        client=SequencedFakeClient(worked_example_stage_outputs()),
        identity=FAKE_IDENTITY,
        now=NOW,
        staged_generator=TemplateStagedGenerator(),
    )

    assert STAGE_EPICS_VERSION in seeded.plan_run.prompt_version
    assert STAGE_TICKETS_VERSION in seeded.plan_run.prompt_version
    # Same epics template version on both runs …
    seeded_epics = seeded.plan_run.generation_stages[0]
    first_epics = first.plan_run.generation_stages[0]
    assert seeded_epics["prompt_version"] == first_epics["prompt_version"]
    # … but the seed makes the rendered prompt (hence the prompt_hash) differ.
    assert seeded_epics["prompt_hash"] != first_epics["prompt_hash"]


def test_seeded_first_run_renders_empty_seed_and_same_envelope(tmp_path: Any) -> None:
    # AC-3: an empty backlog renders empty seed lists (the templates' first-run
    # branch, no <current_backlog>), and the assembled envelope for the same
    # fake emissions is identical to the standalone generator's output.
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    client = SequencedFakeClient(worked_example_stage_outputs())
    result = run_plan(
        repo_root=repo,
        database=database,
        client=client,
        identity=FAKE_IDENTITY,
        now=NOW,
        staged_generator=TemplateStagedGenerator(),
    )
    assert result.status is PlanRunStatus.PROPOSED
    # Empty seed: the epics stage rendered the first-run branch.
    assert "<current_backlog>" not in client.prompts[0]
    assert "This is the first planning run" in client.prompts[0]
    # The envelope matches the standalone generator over the same emissions.
    documents = collect_input_documents(repo)
    payload = [
        {"path": doc.path, "sha": doc.sha, "content": doc.content} for doc in documents
    ]
    valid_anchors = AnchorIndex.build(documents).anchor_choices()
    standalone = (
        TemplateStagedGenerator()
        .generate(
            client=SequencedFakeClient(worked_example_stage_outputs()),
            context=StageContext(
                execution_identity=TEST_EXECUTION_IDENTITY,
                planner_identity=TEST_PLANNER_IDENTITY,
                execution_parameters=TEST_EXECUTION_PARAMETERS,
                product_key="ATLAS",
                documents=payload,
                valid_anchors=valid_anchors,
            ),
        )
        .assembled_json
    )
    assert result.plan_run.proposal == parse_proposal(standalone).model_dump(
        mode="json"
    )


def test_ac6_dropping_a_seeded_ticket_surfaces_a_propose_archive(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # AC-6, red-first (a genuine mutation, never `assert False`): drop ATLAS-1
    # from the stage-2 seed renderer. The seed-following model then never
    # restates it, so it is omitted from the full-state proposal and the
    # reconciler proposes to ARCHIVE it — the exact data-loss shape seeding
    # prevents. This makes AC-1's `_archived(..., "ticket") == set()` assertion
    # (test_seeded_replan_no_archive_of_restated_tickets) FAIL.
    repo = replan_repo(tmp_path)
    database = fresh_db(tmp_path)
    seed_backlog(database)

    def dropping_render(**kwargs: Any) -> str | None:
        if kwargs.get("projection") == "tickets":
            kwargs["tickets"] = [t for t in kwargs["tickets"] if t.key != "ATLAS-1"]
        return render_backlog_yaml(**kwargs)

    monkeypatch.setattr(pipeline_module, "render_backlog_yaml", dropping_render)

    result = run_plan(
        repo_root=repo,
        database=database,
        client=SeedFollowingClient(),
        identity=FAKE_IDENTITY,
        now=NOW,
        staged_generator=TemplateStagedGenerator(),
    )

    assert result.diff is not None
    # The dropped ticket is now proposed for archive — AC-1's no-archive
    # assertion would fail here, proving the seed is load-bearing.
    assert "ATLAS-1" in _archived(result.diff, "ticket")


def test_run_plan_staged_refuses_an_epic_less_ticket(tmp_path: Any) -> None:
    # AC-4 / A-3(ii): the one shape staged seeding cannot express is an
    # epic-less ticket (no per-epic batch). The pipeline refuses BEFORE
    # generation (repurposed StagedReplanUnsupportedError) rather than omit it —
    # a wrong-answer assertion that it is NEVER silently archived, because the
    # generator is never even called.
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    EpicRepo(database).add(Epic(**epic_kwargs() | {"id": uuid4(), "key": "ATLAS-E1"}))
    TicketRepo(database).add(
        Ticket(
            **ticket_kwargs()
            | {
                "id": uuid4(),
                "key": "ATLAS-9",
                "epic_id": None,
                "ticket_type": "tech_debt",
                "status": "backlog",
            }
        )
    )
    client = SeedFollowingClient()
    with pytest.raises(StagedReplanUnsupportedError, match="ATLAS-9"):
        run_plan(
            repo_root=repo,
            database=database,
            client=client,
            identity=FAKE_IDENTITY,
            now=NOW,
            staged_generator=TemplateStagedGenerator(),
        )
    # Clean exit: no generation, no PlanRun — the ticket is never archived.
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
    # The single-call prompt_version is the live single-call template (CURRENT,
    # bumped to v1.2.0 by ATLAS-111), not a composite — staged is purely additive.
    assert result.plan_run.prompt_version == "planner-v1.2.0"


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

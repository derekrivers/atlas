"""ATLAS-29: acceptance suite AT-1..AT-7 (spec §7), end to end.

AT-2/3/4/5/6 are deterministic — a constructed proposal through a fake
PlannerClient against seeded fixture documents, driven through the
`atlas` CLI (`cli.main`) and read back from the persisted PlanRun /
backlog — so they run on every PR with no model call. AT-1/AT-7 depend on
the model's real output and are gated behind ATLAS_LIVE_TESTS=1 (the
ATLAS-26 mechanism), skipped in CI. The AT-7 coverage metric (§7.1) is
exercised in CI by a synthetic-pair percentage test and a real-roadmap
enumeration-count test.

These assert the integrated plan/apply behaviour, not the component AT
shadows (test_reconciler / test_apply / test_provenance), which exercise
reconcile()/run_apply with hand-built inputs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from acceptance_metrics import anchor_coverage, enumerate_roadmap_tickets
from planner_fakes import FAKE_IDENTITY, FakePlannerClient
from test_apply import _epic_model_kwargs, _ticket_model_kwargs
from test_plan_pipeline import (
    _epic,
    _ticket,
    fixture_repo,
    fresh_db,
    git,
    proposal_json,
)

from atlas.cli import EXIT_OK, EXIT_PRECONDITION, main
from atlas.core.models import Epic, PlanRunStatus, Ticket
from atlas.planning.client import AnthropicPlannerClient
from atlas.planning.ingestion import AnchorIndex, collect_input_documents
from atlas.planning.pipeline import run_plan
from atlas.planning.proposal import Proposal
from atlas.planning.reconciler import Backlog, reconcile
from atlas.storage import (
    Database,
    EpicRepo,
    KeyCounterRepo,
    PlanRunRepo,
    ProductRepo,
    TicketRepo,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
ROADMAP = REPO_ROOT / "docs" / "atlas" / "implementation-roadmap.md"


# --- CLI drivers ------------------------------------------------------------


def cli_plan(repo: Path, database: Database, proposal: str) -> int:
    return main(
        ["plan", "--repo", str(repo)],
        database=database,
        client=FakePlannerClient(proposal),
        identity=FAKE_IDENTITY,
    )


def cli_apply(repo: Path, database: Database, *, yes: bool = True) -> int:
    argv = ["apply", "--repo", str(repo)]
    if yes:
        argv.append("--yes")
    return main(argv, database=database)


def proposal_with(**overrides: Any) -> str:
    payload = {
        "epics": [_epic()],
        "tickets": [_ticket()],
        "dependencies": [],
        "planner_notes": [],
    } | overrides
    return json.dumps(payload)


# --- AT-2 Stability ---------------------------------------------------------


def test_at2_unchanged_docs_yield_empty_diff(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    assert cli_plan(repo, database, proposal_json()) == EXIT_OK
    assert cli_apply(repo, database) == EXIT_OK
    # Second plan on unchanged docs with the same proposal.
    assert cli_plan(repo, database, proposal_json()) == EXIT_OK

    second = PlanRunRepo(database).latest_proposed()
    assert second is not None
    assert second.diff_summary["counts"] == {
        "ADD": 0,
        "MODIFY": 0,
        "PROPOSE_ARCHIVE": 0,
        "CONFLICT": 0,
    }
    assert second.diff_summary["entries"] == []  # empty: no key churn


def test_at2_free_text_change_is_modify_only_no_key_churn(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    cli_plan(repo, database, proposal_json())
    cli_apply(repo, database)
    # Re-plan changing only `context` (free text, NOT part of the
    # title+objective similarity), so the items still match at similarity
    # 1.0 (≥ 0.95) and the only entry is a MODIFY.
    cli_plan(
        repo, database, proposal_with(tickets=[_ticket(context="Revised context.")])
    )

    second = PlanRunRepo(database).latest_proposed()
    assert second is not None
    entries = second.diff_summary["entries"]
    assert entries  # there is a change
    for entry in entries:
        assert entry["type"] == "MODIFY"
        assert not entry["identity"].startswith("new")  # no key churn
    assert second.diff_summary["counts"]["ADD"] == 0
    assert second.diff_summary["counts"]["PROPOSE_ARCHIVE"] == 0


# --- AT-3 Locality ----------------------------------------------------------


def test_at3_edit_one_section_localises_the_diff(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    cli_plan(repo, database, proposal_json())
    cli_apply(repo, database)

    # Edit only the #backlog section and commit (the slug is unchanged).
    (repo / "docs" / "atlas" / "plan.md").write_text(
        "# Planning\n\n## Backlog\n\nEdited backlog body.\n", encoding="utf-8"
    )
    git(repo, "commit", "-aqm", "edit backlog section")

    # The planner adds work anchored to the edited section.
    cli_plan(
        repo,
        database,
        proposal_with(
            tickets=[
                _ticket(),
                _ticket(
                    title="New backlog work",
                    objective="extra backlog scope",
                    source_anchor="docs/atlas/plan.md#backlog",
                ),
            ]
        ),
    )
    run = PlanRunRepo(database).latest_proposed()
    assert run is not None
    changed = [e for e in run.diff_summary["entries"] if e["type"] in ("ADD", "MODIFY")]
    assert changed  # a change was produced
    for entry in changed:
        if entry["kind"] in ("epic", "ticket"):
            assert entry["anchor"] == "docs/atlas/plan.md#backlog"


# --- AT-4 Immutability ------------------------------------------------------


def test_at4_conflict_surfaces_and_apply_refuses(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    EpicRepo(database).add(epic)
    frozen = Ticket(
        **_ticket_model_kwargs(product.id, epic.id, key="ATLAS-1")
        | {"status": "in_progress"}
    )
    TicketRepo(database).add(frozen)

    # A proposal touching the frozen ticket (echoed key, changed title).
    proposal = json.dumps(
        {
            "epics": [_epic(key="ATLAS-E1")],
            "tickets": [_ticket(key="ATLAS-1", epic_ref="ATLAS-E1", title="Renamed")],
            "dependencies": [],
            "planner_notes": [],
        }
    )
    # plan succeeds (gates pass); the diff carries CONFLICT.
    assert cli_plan(repo, database, proposal) == EXIT_OK
    run = PlanRunRepo(database).latest_proposed()
    assert run is not None
    assert any(e["type"] == "CONFLICT" for e in run.diff_summary["entries"])

    # apply refuses the CONFLICT diff and writes nothing.
    assert cli_apply(repo, database) == EXIT_PRECONDITION
    planning = repo / "docs" / "planning"
    assert not planning.exists() or not list(planning.iterdir())


# --- AT-5 Provenance --------------------------------------------------------


def test_at5_applied_input_shas_match_tree_and_stale_refused(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    cli_plan(repo, database, proposal_json())
    assert cli_apply(repo, database) == EXIT_OK

    applied = PlanRunRepo(database).latest_applied()
    assert applied is not None
    head_shas = {
        "PRODUCT.md": git(repo, "rev-parse", "HEAD:PRODUCT.md").strip(),
        "docs/atlas/plan.md": git(repo, "rev-parse", "HEAD:docs/atlas/plan.md").strip(),
    }
    assert applied.input_doc_shas == head_shas  # match the git tree

    # A fresh plan, then a doc edit, makes apply refuse the stale plan.
    cli_plan(repo, database, proposal_json())
    (repo / "PRODUCT.md").write_text(
        "# Atlas\n\n## Vision\n\nChanged.\n", encoding="utf-8"
    )
    git(repo, "commit", "-aqm", "edit product")
    assert cli_apply(repo, database) == EXIT_PRECONDITION  # stale refused


# --- AT-6 Key authority -----------------------------------------------------


def test_at6_no_model_key_and_counter_monotonic_across_archive(
    tmp_path: Path,
) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    cli_plan(repo, database, proposal_json())
    cli_apply(repo, database)
    assert {ticket.key for ticket in TicketRepo(database).list()} == {"ATLAS-1"}

    # Cycle 2 full-state: the epic is echoed, ATLAS-1 is dropped (archived),
    # and a distinct new ticket (different anchor + text, so it does not
    # match ATLAS-1) is added.
    cli_plan(
        repo,
        database,
        proposal_with(
            epics=[_epic(key="ATLAS-E1")],
            tickets=[
                _ticket(
                    title="Telemetry dashboard",
                    objective="Collect runtime metrics",
                    source_anchor="docs/atlas/plan.md#planning",
                    epic_ref="ATLAS-E1",
                )
            ],
        ),
    )
    run = PlanRunRepo(database).latest_proposed()
    assert run is not None
    entries = run.diff_summary["entries"]
    assert any(
        e["type"] == "PROPOSE_ARCHIVE" and e["identity"] == "ATLAS-1" for e in entries
    )
    assert any(e["type"] == "ADD" and e["kind"] == "ticket" for e in entries)

    assert cli_apply(repo, database) == EXIT_OK
    # The new key is minted from the counter, never reissuing ATLAS-1.
    assert {ticket.key for ticket in TicketRepo(database).list()} == {
        "ATLAS-1",
        "ATLAS-2",
    }
    assert KeyCounterRepo(database).high_water_marks()["ATLAS"] == 2
    # No key originates from the model: the stored proposal keys are null.
    applied = PlanRunRepo(database).latest_applied()
    assert applied is not None
    assert all(ticket["key"] is None for ticket in applied.proposal["tickets"])


# --- AT-7 coverage metric (deterministic legs) ------------------------------

_SYNTHETIC_ROADMAP = """\
# Phase X

## Epic: Alpha

ATLAS-1 First thing
ATLAS-2 Second thing

## Epic: Beta

ATLAS-3 Third thing
ATLAS-4 Fourth thing
"""


def test_anchor_coverage_percentage_on_synthetic_pair() -> None:
    tickets = enumerate_roadmap_tickets(_SYNTHETIC_ROADMAP, path="r.md")
    assert [t.key for t in tickets] == ["ATLAS-1", "ATLAS-2", "ATLAS-3", "ATLAS-4"]
    assert tickets[0].anchor == "r.md#epic-alpha"
    assert tickets[2].anchor == "r.md#epic-beta"
    # Proposal covering only Alpha -> 2 of 4 hand-written tickets.
    assert anchor_coverage({"r.md#epic-alpha"}, _SYNTHETIC_ROADMAP, path="r.md") == 0.5
    assert (
        anchor_coverage(
            {"r.md#epic-alpha", "r.md#epic-beta"}, _SYNTHETIC_ROADMAP, path="r.md"
        )
        == 1.0
    )
    assert anchor_coverage(set(), _SYNTHETIC_ROADMAP, path="r.md") == 0.0


def test_enumeration_pins_real_roadmap_count() -> None:
    # The denominator is a prose parser; a roadmap reformat the parser
    # misses changes this hand-verified count and fires the test.
    tickets = enumerate_roadmap_tickets(ROADMAP.read_text(encoding="utf-8"))
    # 85 milestone tickets + 7 post-milestone hardening tickets (ATLAS-101..107,
    # ATLAS-102's roadmap addition). The pin fires on exactly this kind of change.
    assert len(tickets) == 92
    keys = [t.key for t in tickets]
    assert len(keys) == len(set(keys))  # unique
    assert "ATLAS-20" not in keys  # retired lines are not tickets
    by_key = {t.key: t for t in tickets}
    assert by_key["ATLAS-21"].anchor == (
        "docs/atlas/implementation-roadmap.md"
        "#epic-generative-planning-with-deterministic-reconciliation"
    )


# --- no live call in CI -----------------------------------------------------


def test_ci_config_makes_no_live_call() -> None:
    # The live ATs below are skipped unless ATLAS_LIVE_TESTS=1; CI sets
    # neither that flag nor an API key, so CI provably never calls out.
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "ATLAS_LIVE_TESTS" not in ci
    assert "ANTHROPIC_API_KEY" not in ci


# --- AT-1 / AT-7 live (operator-run) ----------------------------------------

LIVE = os.environ.get("ATLAS_LIVE_TESTS") == "1"
_LIVE_REASON = "live model call; set ATLAS_LIVE_TESTS=1 and run on a clean tree"


@pytest.fixture(scope="module")
def live_plan_run(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    """One real `atlas plan` against the committed repo, shared by AT-1 and
    AT-7. Requires a clean working tree (ingestion runs HEAD-atomic)."""
    if not LIVE:
        pytest.skip(_LIVE_REASON)
    tmp = tmp_path_factory.mktemp("live")
    database = Database(f"sqlite:///{tmp}/atlas.db")
    database.create_all()
    from test_models_validation import product_kwargs

    from atlas.core.models import Product

    ProductRepo(database).add(Product(**product_kwargs()))
    from datetime import UTC, datetime

    result = run_plan(
        repo_root=REPO_ROOT,
        database=database,
        client=AnthropicPlannerClient(),
        identity=AnthropicPlannerClient().identity,
        now=datetime.now(UTC),
    )
    return result


@pytest.mark.skipif(not LIVE, reason=_LIVE_REASON)
def test_at1_real_proposal_passes_all_gates(live_plan_run: Any) -> None:
    # Reaching `proposed` means every gate passed (a gate failure records a
    # failed run instead). Plus: the projected graph is acyclic and every
    # ticket resolves to a document anchor.
    assert live_plan_run.status is PlanRunStatus.PROPOSED
    proposal = Proposal.model_validate(live_plan_run.plan_run.proposal)
    index = AnchorIndex.build(collect_input_documents(REPO_ROOT))
    for ticket in proposal.tickets:
        index.resolve(ticket.source_anchor)  # raises if unresolvable
    # An empty/acyclic projection is gate 2's; reconcile produced the diff.
    backlog = Backlog()
    reconcile(proposal, backlog)  # deterministic, no raise


@pytest.mark.skipif(not LIVE, reason=_LIVE_REASON)
def test_at7_real_proposal_covers_roadmap(live_plan_run: Any) -> None:
    proposal = Proposal.model_validate(live_plan_run.plan_run.proposal)
    anchors = {ticket.source_anchor for ticket in proposal.tickets}
    coverage = anchor_coverage(anchors, ROADMAP.read_text(encoding="utf-8"))
    assert coverage >= 0.90, f"AT-7 coverage {coverage:.2%} < 90%"

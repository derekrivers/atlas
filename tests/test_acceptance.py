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
from acceptance_metrics import (
    ANCHOR_COVERAGE_FLOOR,
    CONTENT_COVERAGE_THRESHOLD,
    UNIFIED_COVERAGE_FLOOR,
    anchor_coverage,
    at7_pair_verdict,
    citation_covered_keys,
    cited_keys,
    content_coverage,
    enumerate_roadmap_tickets,
    heading_index,
    is_adjacent_anchor,
    unified_coverage,
)
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
from atlas.core.anchors import AnchorIndex
from atlas.core.models import Epic, PlanRunStatus, Ticket
from atlas.planning.client import AnthropicPlannerClient
from atlas.planning.ingestion import collect_input_documents
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


# --- AT-7 content-coverage sibling metric (ATLAS-112) ------------------------


class _FakeProposedTicket:
    """Minimal stand-in for a Proposal ticket: title + objective only."""

    def __init__(self, title: str, objective: str = "") -> None:
        self.title = title
        self.objective = objective


def test_heading_index_orders_slugs_and_excludes_fenced_headings() -> None:
    text = "# Top\n\n## Alpha\n\n```\n# Not A Heading\n```\n\n## Beta\n### Beta Child\n"
    index = heading_index(text)
    # The fenced `# Not A Heading` is not a heading (§2.3); order is preserved.
    assert [h.slug for h in index] == ["top", "alpha", "beta", "beta-child"]
    assert [h.level for h in index] == [1, 2, 2, 3]


def test_content_coverage_covers_work_anchored_to_a_different_document() -> None:
    # The case ATLAS-112 exists for: the planner covers the work but anchors
    # it to a DESIGN DOC, not the roadmap epic heading. Exact-anchor scores it
    # a miss; content-coverage scores it covered.
    roadmap = (
        "# Phase 4\n\n## Epic: Delivery Coordination\n\n"
        "ATLAS-43 Ready state detection\n"
    )
    # The planner's ticket: same work, richer title, anchored elsewhere.
    proposed = [
        _FakeProposedTicket(
            "Ready State Detection and Promotion",
            "Detect when a ticket becomes ready and promote it.",
        )
    ]
    # Exact-anchor: the planner anchored to a design doc, not the roadmap epic,
    # so nothing matches the roadmap anchor -> a miss.
    assert (
        anchor_coverage(
            {"docs/atlas/pm-engine-and-linear-sync.md#sync-loop"},
            roadmap,
            path="r.md",
        )
        == 0.0
    )
    # Content-coverage: the work is present -> covered, document-independent.
    result = content_coverage(proposed, roadmap, path="r.md")
    assert result.fraction == 1.0
    assert result.covered[0].roadmap_key == "ATLAS-43"
    assert result.covered[0].best_score >= CONTENT_COVERAGE_THRESHOLD


def test_content_coverage_threshold_is_a_boundary() -> None:
    roadmap = "## Epic: E\n\nATLAS-1 Token budget compression ladder\n"
    # Above threshold: a faithful restatement is covered.
    near = [_FakeProposedTicket("Token Budget and Compression Ladder")]
    assert content_coverage(near, roadmap, path="r.md").fraction == 1.0
    # Below threshold: an unrelated ticket sharing no work is missed (negative).
    far = [_FakeProposedTicket("Linear API client and field ownership")]
    far_result = content_coverage(far, roadmap, path="r.md")
    assert far_result.fraction == 0.0
    assert far_result.missed[0].best_score < CONTENT_COVERAGE_THRESHOLD


def test_content_coverage_routes_through_reconciler_similarity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Reuse, not reimplementation: there is one similarity implementation, and
    # content_coverage must call it. A spy on the real reconciler module (the
    # same object content_coverage looks the function up on) proves the routing.
    from atlas.planning import reconciler

    original = reconciler.similarity
    calls: list[tuple[str, str, str, str]] = []

    def spy(title_a: str, objective_a: str, title_b: str, objective_b: str) -> float:
        calls.append((title_a, objective_a, title_b, objective_b))
        return original(title_a, objective_a, title_b, objective_b)

    monkeypatch.setattr(reconciler, "similarity", spy)
    roadmap = "## Epic: E\n\nATLAS-1 Ready state detection\n"
    content_coverage(
        [_FakeProposedTicket("Ready State Detection")], roadmap, path="r.md"
    )
    assert calls  # the metric routed through the reconciler primitive


# --- AT-7 containment-aware content coverage (ATLAS-124 D1) ------------------


def test_containment_recovers_the_asymmetric_false_negative() -> None:
    # The case ATLAS-124 exists for: the roadmap title is a literal subset of a
    # longer proposed title. Symmetric Sorensen-Dice scores it below threshold
    # (the proposal's extra tokens dilute the coefficient), but every roadmap
    # token is present, so containment = 1.0 recovers it deterministically.
    roadmap = "## Epic: E\n\nATLAS-1 Ticket synchronisation\n"
    proposed = [
        _FakeProposedTicket(
            "Ticket synchronisation sync_tick (pull status, push definitions)"
        )
    ]
    result = content_coverage(proposed, roadmap, path="r.md")
    match = result.matches[0]
    # Symmetric alone is a miss; containment carries it over the line.
    assert match.best_score < CONTENT_COVERAGE_THRESHOLD  # wrong answer: still a miss
    assert match.best_containment == 1.0
    assert match.covered
    assert result.fraction == 1.0


def test_containment_cannot_create_cross_ticket_false_positive() -> None:
    # Disjoint token sets: containment is 0.0, so an unrelated proposed ticket
    # never spuriously covers a roadmap ticket. The wrong answer marks it covered.
    roadmap = "## Epic: E\n\nATLAS-1 Token budget compression ladder\n"
    proposed = [_FakeProposedTicket("Linear API client and field ownership")]
    result = content_coverage(proposed, roadmap, path="r.md")
    match = result.matches[0]
    assert match.best_containment == 0.0
    assert not match.covered
    assert result.fraction == 0.0


def test_reconciler_similarity_semantics_unchanged_by_containment() -> None:
    # ATLAS-124 adds `containment` BESIDE `similarity`; the primitive's symmetric
    # semantics must be untouched. A known symmetric pair returns the symmetric
    # coefficient, and containment is a distinct, directional, asymmetric score.
    from atlas.planning import reconciler

    # {ready, state, detection} vs {ready, state, detection, and, promotion}:
    # symmetric Dice = 2*3/(3+5) = 0.75; containment (roadmap ⊂ proposed) = 1.0.
    sym = reconciler.similarity(
        "Ready state detection", "", "Ready state detection and promotion", ""
    )
    assert sym == 0.75  # the symmetric primitive is exactly as before
    assert (
        reconciler.containment(
            "Ready state detection", "Ready state detection and promotion"
        )
        == 1.0
    )
    # Asymmetry: swapping the arguments changes containment but not similarity.
    assert (
        reconciler.similarity(
            "Ready state detection and promotion", "", "Ready state detection", ""
        )
        == sym
    )
    assert (
        reconciler.containment(
            "Ready state detection and promotion", "Ready state detection"
        )
        == 0.6  # 3 of the 5 longer-title tokens appear in the shorter -> 3/5
    )


# --- AT-7 pair verdict: both floors gate, citation excluded (ATLAS-124) ------


def test_at7_pair_unified_floor_gates_with_citation_excluded() -> None:
    # The pair logic the live leg runs, proved deterministically in CI (the live
    # leg is skipped here). Both floors gate: anchor >= 0.50 AND
    # exact-or-content >= 0.80. Citation is excluded from the gated union by
    # construction (it is never a parameter to unified_coverage).
    total = 10
    exact = {f"ATLAS-{n}" for n in range(1, 6)}  # 5 keys

    # 1. Sub-floor anchor_coverage FAILS, regardless of a passing union. The
    #    wrong answer asserts `below.passed`.
    full_union = unified_coverage(exact, {f"ATLAS-{n}" for n in range(6, 11)}, total)
    below_anchor = at7_pair_verdict(0.40, 0.90, full_union)
    assert full_union == 1.0
    assert not below_anchor.passed
    assert "40.00%" in below_anchor.message and "FAIL" in below_anchor.message

    # 2. exact-or-content >= 0.80 with anchor clear -> PASS. Content keys here lift
    #    the union to 0.80 exactly (the floor is inclusive).
    content = {f"ATLAS-{n}" for n in range(6, 9)}  # 3 more -> union 8/10 = 0.80
    passing = unified_coverage(exact, content, total)
    assert passing == UNIFIED_COVERAGE_FLOOR
    verdict = at7_pair_verdict(0.634, 0.55, passing)
    assert verdict.passed
    assert "citation excluded from gate" in verdict.message

    # 3. Drop content below 0.80 and assert the verdict FAILS even though a
    #    citation set would cover the gap -> proves citation is NOT in the gated
    #    union. The wrong answer admits the citation keys and passes.
    thin_content = {"ATLAS-6"}  # union 6/10 = 0.60 < 0.80
    citation_would_cover = {f"ATLAS-{n}" for n in range(6, 11)}  # would lift to 1.0
    sub_floor = unified_coverage(exact, thin_content, total)
    assert sub_floor == 0.60
    # unified_coverage takes no citation argument; even handed the rescuing set
    # it cannot enter the gate. Confirm the (exact-or-content) value still fails.
    assert unified_coverage(exact, thin_content, total) == 0.60
    failed = at7_pair_verdict(0.634, 0.10, sub_floor)
    assert not failed.passed
    # The citation set, were it gated, would have rescued the run; it does not.
    assert citation_would_cover  # constructed, but never reaches the verdict


# --- AT-7 key-citation coverage lens (ATLAS-124 diagnostic, additive) --------


def test_cited_keys_extracts_cited_keys_and_ignores_non_citations() -> None:
    # The planner writes the roadmap key it implements into the title,
    # parenthetically. The lens reads it; a non-ATLAS parenthetical is not a key.
    assert cited_keys("Stale-block detection (sync_tick step 5, ATLAS-44)") == {
        "ATLAS-44"
    }
    # No key cited -> empty set (not the proposal's own null key).
    assert cited_keys("PM scheduler with create-on-crash") == set()
    # Multiple keys in one title -> all of them.
    assert cited_keys("Split work (ATLAS-119, ATLAS-120)") == {"ATLAS-119", "ATLAS-120"}
    # A non-ATLAS parenthetical is not a citation.
    assert cited_keys("Token budget compression ladder (Phase 5, step 2)") == set()
    # A key not flush against the paren is still found (the wrong answer misses it).
    assert cited_keys("Ready state detection — see ATLAS-43 for the heading") == {
        "ATLAS-43"
    }


def test_citation_covered_keys_credits_only_cited_roadmap_keys() -> None:
    roadmap_keys = {"ATLAS-1", "ATLAS-2"}
    proposed_titles = ["Foo (ATLAS-1)", "Bar"]
    # Only ATLAS-1 is cited; ATLAS-2 is uncited and is NOT credited.
    assert citation_covered_keys(proposed_titles, roadmap_keys) == {"ATLAS-1"}
    # A cited key outside the roadmap set is never credited (the wrong answer
    # returns it).
    assert citation_covered_keys(["Baz (ATLAS-999)"], roadmap_keys) == set()


def test_gated_metrics_constants_are_the_pinned_floors() -> None:
    # The gated bar is the pair of floors plus the recorded content threshold;
    # the citation lens is additive and diagnostic and must NOT move any of them.
    # This guard pins the spec'd constants (§7.1, §7.2) so a future edit cannot
    # silently move the bar to chase a score.
    assert ANCHOR_COVERAGE_FLOOR == 0.50
    assert UNIFIED_COVERAGE_FLOOR == 0.80
    assert CONTENT_COVERAGE_THRESHOLD == 0.5


# --- AT-7 adjacency classification fix (ATLAS-112 named gap) -----------------

_CLUSTER_ROADMAP = """\
# Phase 0

## Epic: Bootstrap Repository

ATLAS-1 Repository skeleton

# Phase 4

## Epic: Delivery Coordination

ATLAS-41 Linear integration
"""


def test_adjacency_rejects_far_same_doc_anchor_no_false_ceiling() -> None:
    # The roadmap-clustering case: a Phase 4 miss whose only same-doc planner
    # anchor is a FAR Phase 0 heading must NOT be classified adjacent. Under
    # the old "any anchor in the same document" test this was a false adjacent
    # (and a false 100% optimistic ceiling); the proximity rule rejects it.
    headings = heading_index(_CLUSTER_ROADMAP)
    wanted = "r.md#epic-delivery-coordination"  # Phase 4
    far = "r.md#epic-bootstrap-repository"  # Phase 0 — same doc, far away
    assert is_adjacent_anchor(wanted, far, headings) is False


def test_adjacency_accepts_neighbouring_and_rejects_other_document() -> None:
    headings = heading_index(_CLUSTER_ROADMAP)
    wanted = "r.md#epic-delivery-coordination"
    # An immediate neighbour heading IS adjacent (the genuine undercount case).
    assert is_adjacent_anchor(wanted, "r.md#phase-4", headings) is True
    # A different document is never adjacent — it is a different-doc anchoring
    # choice, the case ATLAS-112 distinguishes.
    assert (
        is_adjacent_anchor(
            wanted, "docs/atlas/pm-engine-and-linear-sync.md#sync-loop", headings
        )
        is False
    )


def test_enumeration_pins_real_roadmap_count() -> None:
    # The denominator is a prose parser; a roadmap reformat the parser
    # misses changes this hand-verified count and fires the test.
    tickets = enumerate_roadmap_tickets(ROADMAP.read_text(encoding="utf-8"))
    # 84 milestone tickets (ATLAS-33 retired into the Phase 3 Retired line) +
    # 7 post-milestone hardening tickets (ATLAS-101..107, ATLAS-102's roadmap
    # addition) + ATLAS-112 (the AT-7 work-coverage record, hand-written intent)
    # + 4 Phase 2.5 live-discovered fixes (ATLAS-108..111) + 3 Phase 3.5
    # layer-consolidation tickets (ATLAS-113..115) + ATLAS-117 (the Phase 9
    # code-quality debt-register seed, ADR-0011 D2) + ATLAS-118 (out-of-ownership
    # transition logging) + ATLAS-119/-120 (the dwell-breach and review-cycling
    # anomaly mechanisms split out of ATLAS-118) + ATLAS-121 (the
    # state-transition-history seed: the data-model prerequisite ATLAS-47's true
    # cycle-time metric needs, reported only as a current-dwell proxy until it
    # lands) + ATLAS-122 (the follow-up CONSUMER seed: plan reads the committed
    # inbox as a separate input source and apply moves processed stubs — the half
    # the ATLAS-45 producer does not build) + ATLAS-123 (the AT-7 pair-metric
    # encoding: exact-anchor floor live, content_coverage reported — realises
    # ATLAS-112's RESOLVED decision, the clause ATLAS-107 reused) + ATLAS-124 (the
    # content-coverage bar-pin follow-up seed, gated on a second staged capture)
    # + ATLAS-125 (the tick-failure record + recorded_since dedup predicate: the
    # create-on-crash record half, a prerequisite for the ATLAS-50 scheduler) +
    # ATLAS-126 (the historical-cycle-time CONSUMER seed: upgrade build_delivery_report
    # to compute true per-state cycle time from the ATLAS-121 transition log,
    # replacing the current-dwell proxy ATLAS-47 reports today) + ATLAS-127/-128
    # (the ticket tags/component fields: ATLAS-127 lands the free-form
    # tags/component on the stored Ticket — the storage half — and ATLAS-128
    # seeds the writer half where the planner emits them; both sequenced ahead of
    # the Phase 5 retrievers ATLAS-51/-53 that consume the field).
    # + ATLAS-129 (the anchor/slug primitive relocation to atlas.core: a pure
    # move of slugify/heading-parsing/SourceDocument/ResolvedAnchor/AnchorIndex
    # and the anchor error hierarchy out of atlas.planning.ingestion so the
    # atlas.context retrievers — below planning in the spine — can import it;
    # no new ticket behaviour, the existing suite is the oracle).
    # + ATLAS-130 (the live-discovered Phase 6 fix: the evidence CLI fails
    # cleanly on a cold/never-migrated database — a missing-schema
    # OperationalError maps to EXIT_PRECONDITION across pull/list/show — recorded
    # under the Phase 6 live-discovered-fixes block per the closure-coverage
    # sensor, merged #116).
    # Addition couples to the pin, the mirror of the retirement rule. The pin
    # fires on exactly this kind of change.
    # - ATLAS-143 retires ATLAS-83 (subsumed by the sole-writer promotion in
    #   atlas/pm/promotion.py, ATLAS-34/42) and ATLAS-87 (subsumed by the
    #   follow-up producer ATLAS-45 + consumer ATLAS-122) into the Phase-8
    #   Retired line, so both drop their standalone ticket lines: 114 -> 112.
    # - ATLAS-029M (Phase 9 closure) fences the delivered E11 list, the
    #   closure-findings list, and the deferred ATLAS-107 block: those keys
    #   (65/99/100/101-107) collide in the roadmap namespace with the Phase
    #   2.5 planner tickets of the same numbers, and the pin's uniqueness
    #   assert forbids double-defining them. Fencing keeps them coverage-
    #   visible without the enumerator counting them: 112 -> 102. The
    #   roadmap/store identity collision is a Phase 10 carry-forward.
    assert len(tickets) == 102
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
    # AT-7 is the pair of floors (ATLAS-112 RESOLVED, PINNED by ATLAS-124, §7.2):
    # the exact-anchor floor AND the unified `exact-or-content` floor both gate,
    # content measured containment-aware, citation excluded. The gating logic is
    # `at7_pair_verdict` over `unified_coverage`, the same helpers the synthetic
    # CI test proves both ways.
    proposal = Proposal.model_validate(live_plan_run.plan_run.proposal)
    roadmap_text = ROADMAP.read_text(encoding="utf-8")
    anchors = {ticket.source_anchor for ticket in proposal.tickets}
    roadmap_tickets = enumerate_roadmap_tickets(roadmap_text)
    anchor_cov = anchor_coverage(anchors, roadmap_text)
    content = content_coverage(proposal.tickets, roadmap_text)
    exact_covered_keys = {t.key for t in roadmap_tickets if t.anchor in anchors}
    content_covered_keys = {m.roadmap_key for m in content.matches if m.covered}
    unified_cov = unified_coverage(
        exact_covered_keys, content_covered_keys, len(roadmap_tickets)
    )
    verdict = at7_pair_verdict(anchor_cov, content.fraction, unified_cov)
    assert verdict.passed, verdict.message

"""ATLAS-153: stubs-only plan mode — mint committed inbox stubs without a
model call.

Fixture-driven, zero model calls (ATLAS_LIVE_TESTS=0 posture throughout):
every test drives ``run_stubs_only_plan`` (or ``main`` at the CLI seam)
against a committed git fixture repo and an on-disk SQLite database. The
named July-batch fixture reproduces the July 2026 mint shape: four
hand-authored stubs whose ``depends_on`` front-matter names existing
backlog keys ATLAS-20/22/23/26/45 (ATLAS-45 frozen at ``done``) plus one
sibling-stub filename — the batch that cost three staged £5 draws when
promotion could only ride inside generation.

Seeded failing-first per the house convention (every test here began as
``assert 1 == 2`` to prove collection), then implemented against the
rendered acceptance criteria.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from planner_fakes import FAKE_IDENTITY, MustNotBeCalledClient
from test_apply import (
    APPLY_NOW,
    _epic_model_kwargs,
    _seed_counter,
    _ticket_model_kwargs,
    confirmed,
)
from test_plan_pipeline import (
    NOW,
    PLAN_MD,
    PRODUCT_MD,
    fixture_repo,
    fresh_db,
    git,
    make_repo,
)

from atlas.cli import EXIT_OK, EXIT_PRECONDITION, main
from atlas.core.anchors import AnchorIndex
from atlas.core.enums import ActorType
from atlas.core.models import Epic, PlanRunStatus, Ticket, TicketDependency
from atlas.core.models.dependency import DependencyType
from atlas.planning.apply import run_apply
from atlas.planning.ingestion import (
    InboxCollisionError,
    collect_inbox_documents,
    collect_input_documents,
    collect_processed_documents,
)
from atlas.planning.pipeline import (
    DEFAULT_INBOX_DIR,
    EmptyInboxError,
    PlanResult,
    StubEpicRefError,
    run_stubs_only_plan,
)
from atlas.planning.promotion import StubPromotionError
from atlas.planning.reconciler import ADD, CONFLICT, MODIFY, PROPOSE_ARCHIVE
from atlas.storage import (
    Database,
    EpicRepo,
    PlanRunRepo,
    ProductRepo,
    TicketDependencyRepo,
    TicketRepo,
)

# --- the named July-batch fixture --------------------------------------------

JULY_EPIC_KEY = "ATLAS-E1"
JULY_BACKLOG_KEYS = ("ATLAS-20", "ATLAS-22", "ATLAS-23", "ATLAS-26", "ATLAS-45")
# done: frozen to planning (spec §4). Both frozen so the seeded frozen-source
# edge ATLAS-45 -> ATLAS-20 satisfies the terminal-dependency graph rule.
JULY_FROZEN_KEYS = ("ATLAS-20", "ATLAS-45")
JULY_FROZEN_KEY = "ATLAS-45"


def _stub(
    title: str,
    *,
    depends_on: Sequence[str] = (),
    epic_ref: str = JULY_EPIC_KEY,
) -> str:
    """One committed inbox stub carrying the ATLAS-146 front-matter contract
    plus the optional ATLAS-153 ``depends_on`` list."""
    lines = [
        "---",
        f'title: "{title}"',
        f'objective: "{title} objective."',
        'context: "July 2026 batch."',
        'ticket_type: "feature"',
        f'epic_ref: "{epic_ref}"',
        'acceptance_criteria:\n  - "Done when done."',
        'non_goals:\n  - "Nothing else."',
        'test_requirements:\n  - "A named test."',
        'definition_of_done:\n  - "Evidence recorded."',
    ]
    if depends_on:
        lines.append("depends_on:")
        lines.extend(f'  - "{entry}"' for entry in depends_on)
    lines.append("---")
    lines.append(f"\n# {title}\n")
    return "\n".join(lines)


# Four stubs; collect_inbox_documents sorts by path, and the backlog echoes
# five tickets first, so the stubs promote to new:5..new:8 in this order.
JULY_STUBS = {
    "docs/planning/inbox/inbox-stub-accepted-types-spelling.md": _stub(
        "Accepted-types spelling", depends_on=["ATLAS-45"]
    ),
    "docs/planning/inbox/inbox-stub-f4-promotion-dedup.md": _stub(
        "F-4 promotion dedup", depends_on=["ATLAS-20", "ATLAS-22"]
    ),
    "docs/planning/inbox/inbox-stub-retire-on-reject-scope.md": _stub(
        "Retire-on-reject scope", depends_on=["ATLAS-23"]
    ),
    "docs/planning/inbox/inbox-stub-stubs-only-plan-mode.md": _stub(
        "Stubs-only plan mode",
        depends_on=["ATLAS-26", "inbox-stub-f4-promotion-dedup.md"],
    ),
}

# Every edge the July batch declares, in resolved-identity form: five
# new->existing (one to the frozen ticket — a frozen TARGET is permitted,
# spec §4) and one new->new via the sibling filename.
JULY_EDGE_IDENTITIES = {
    "new:5 -> ATLAS-45",
    "new:6 -> ATLAS-20",
    "new:6 -> ATLAS-22",
    "new:7 -> ATLAS-23",
    "new:8 -> ATLAS-26",
    "new:8 -> new:6",
}


def july_repo(tmp_path: Path, stubs: dict[str, str] | None = None) -> Path:
    return make_repo(
        tmp_path,
        {
            "PRODUCT.md": PRODUCT_MD,
            "docs/atlas/plan.md": PLAN_MD,
            **(JULY_STUBS if stubs is None else stubs),
        },
    )


def july_db(tmp_path: Path) -> Database:
    """The July backlog: one epic, five keyed tickets (ATLAS-20 and ATLAS-45
    frozen at done), and two existing edges — one with a frozen ticket as
    SOURCE, so the verbatim echo is proven against the frozen-edge CONFLICT
    machinery too."""
    database = fresh_db(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key=JULY_EPIC_KEY))
    EpicRepo(database).add(epic)
    tickets: dict[str, Ticket] = {}
    for key in JULY_BACKLOG_KEYS:
        status = "done" if key in JULY_FROZEN_KEYS else "planned"
        ticket = Ticket(
            **_ticket_model_kwargs(product.id, epic.id, key=key)
            | {"title": f"Existing work {key}", "status": status}
        )
        TicketRepo(database).add(ticket)
        tickets[key] = ticket
    for source, target in (("ATLAS-22", "ATLAS-20"), ("ATLAS-45", "ATLAS-20")):
        TicketDependencyRepo(database).add(
            TicketDependency(
                id=uuid4(),
                source_ticket_id=tickets[source].id,
                target_entity_type="ticket",
                target_entity_id=tickets[target].id,
                dependency_type=DependencyType.DEPENDS_ON,
                reason=f"{source} builds on {target}.",
                created_by_type=ActorType.AGENT,
                created_by_id="planner",
                created_at=NOW,
            )
        )
    return database


def july_setup(
    tmp_path: Path, stubs: dict[str, str] | None = None
) -> tuple[Path, Database]:
    return july_repo(tmp_path, stubs), july_db(tmp_path)


def run_stubs(repo: Path, database: Database) -> PlanResult:
    return run_stubs_only_plan(repo_root=repo, database=database, now=NOW)


# --- AC-1: zero model calls; N stubs -> exactly N ticket ADDs -----------------


def test_stubs_only_never_calls_the_model(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The must-not-be-called fake proves the flag end to end at the CLI seam:
    # any generate() call fails the test attributably. Belt to that braces:
    # the pipeline entry has no client parameter at all.
    repo, database = july_setup(tmp_path)
    code = main(
        ["plan", "--repo", str(repo), "--stubs-only"],
        database=database,
        client=MustNotBeCalledClient(),
        identity=FAKE_IDENTITY,
    )
    assert code == EXIT_OK
    assert "client" not in inspect.signature(run_stubs_only_plan).parameters
    out = capsys.readouterr().out
    assert "Plan diff:" in out
    assert "persisted at status proposed" in out


def test_stubs_only_july_batch_yields_exactly_n_ticket_adds(tmp_path: Path) -> None:
    # Four committed stubs -> exactly four ticket ADDs. The keyed backlog echo
    # is a no-op (no epic entries, no MODIFY, no PROPOSE_ARCHIVE, no CONFLICT)
    # and the ATLAS-151 collapse pre-pass ran with nothing to collapse.
    repo, database = july_setup(tmp_path)
    result = run_stubs(repo, database)
    assert result.status is PlanRunStatus.PROPOSED
    assert result.diff is not None
    ticket_entries = [e for e in result.diff.entries if e.kind == "ticket"]
    assert [entry.entry_type for entry in ticket_entries] == [ADD] * 4
    assert sorted(entry.identity for entry in ticket_entries) == [
        "new:5",
        "new:6",
        "new:7",
        "new:8",
    ]
    assert [e for e in result.diff.entries if e.kind == "epic"] == []
    counts = result.diff.counts
    assert counts[MODIFY] == 0
    assert counts[PROPOSE_ARCHIVE] == 0
    assert counts[CONFLICT] == 0
    assert result.diff.collapses == ()


def test_stubs_only_echo_emits_no_conflict_for_frozen_tickets(tmp_path: Path) -> None:
    # ATLAS-45 is done (frozen) and is the SOURCE of an echoed edge — the two
    # shapes the reconciler turns into CONFLICT when a proposal touches them.
    # A verbatim echo touches neither: no entry names the frozen ticket.
    repo, database = july_setup(tmp_path)
    result = run_stubs(repo, database)
    assert result.status is PlanRunStatus.PROPOSED
    assert result.diff is not None
    assert all(entry.entry_type != CONFLICT for entry in result.diff.entries)
    assert not any(
        JULY_FROZEN_KEY in entry.identity and entry.kind != "dependency"
        for entry in result.diff.entries
    )


# --- the depends_on front-matter contract (all three cases) ------------------


def test_depends_on_existing_key_becomes_new_to_existing_edge(tmp_path: Path) -> None:
    # Entries naming existing ticket keys become new->existing dependency
    # ADDs — including one whose target is frozen (permitted, spec §4) — with
    # the single pinned mechanical reason (ADR-0005).
    repo, database = july_setup(tmp_path)
    result = run_stubs(repo, database)
    assert result.diff is not None
    dependency_entries = [
        entry for entry in result.diff.entries if entry.kind == "dependency"
    ]
    assert {entry.identity for entry in dependency_entries} == JULY_EDGE_IDENTITIES
    assert all(entry.entry_type == ADD for entry in dependency_entries)
    assert {entry.title for entry in dependency_entries} == {
        "declared by the stub's depends_on front-matter (ATLAS-153)"
    }


def test_depends_on_sibling_filename_becomes_new_to_new_edge(tmp_path: Path) -> None:
    # A sibling stub FILENAME in the same batch becomes a new->new edge:
    # inbox-stub-stubs-only-plan-mode.md (new:8) names
    # inbox-stub-f4-promotion-dedup.md (new:6).
    repo, database = july_setup(tmp_path)
    result = run_stubs(repo, database)
    assert result.diff is not None
    assert "new:8 -> new:6" in {
        entry.identity for entry in result.diff.entries if entry.kind == "dependency"
    }


def test_depends_on_unknown_key_fails_gate3_typed(tmp_path: Path) -> None:
    # A nonexistent ticket key fails the gates with a typed error: gate 3's
    # GATE3_UNRESOLVED_TARGET, recorded as a FAILED PlanRun (spec §6) exactly
    # as a generative run's gate failure would be.
    stubs = {
        "docs/planning/inbox/inbox-stub-bad-key.md": _stub(
            "Bad key", depends_on=["ATLAS-999"]
        )
    }
    repo, database = july_setup(tmp_path, stubs)
    result = run_stubs(repo, database)
    assert result.status is PlanRunStatus.FAILED
    assert result.failure_reason is not None
    assert "GATE3_UNRESOLVED_TARGET" in result.failure_reason
    assert "ATLAS-999" in result.failure_reason
    stored = PlanRunRepo(database).list()
    assert len(stored) == 1
    assert stored[0].status is PlanRunStatus.FAILED


def test_depends_on_unknown_sibling_filename_fails_closed(tmp_path: Path) -> None:
    # An .md entry naming no sibling in the batch is promotion's fail-closed
    # StubPromotionError (clean exit, no PlanRun), like any front-matter
    # defect — never a silently dropped edge.
    stubs = {
        "docs/planning/inbox/inbox-stub-bad-sibling.md": _stub(
            "Bad sibling", depends_on=["inbox-stub-missing.md"]
        )
    }
    repo, database = july_setup(tmp_path, stubs)
    with pytest.raises(StubPromotionError, match="not in this inbox batch"):
        run_stubs(repo, database)
    assert PlanRunRepo(database).list() == []


def test_stubs_only_new_epic_ref_fails_closed(tmp_path: Path) -> None:
    # A stubs-only run has no model to create epics and no parse stage to
    # bounds-check a placeholder ref, so epic_ref must name an existing epic
    # key — typed refusal, never a reconciler crash or a positional
    # mis-anchor.
    stubs = {
        "docs/planning/inbox/inbox-stub-new-epic.md": _stub(
            "New epic ref", epic_ref="new_epic:0"
        )
    }
    repo, database = july_setup(tmp_path, stubs)
    with pytest.raises(StubEpicRefError, match="existing epic key"):
        run_stubs(repo, database)
    assert PlanRunRepo(database).list() == []


# --- AC-2: empty inbox is a clean-exit precondition failure ------------------


def test_stubs_only_empty_inbox_is_a_clean_precondition_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The rendered AC: a clean-exit precondition failure whose message names
    # the empty inbox — never an empty-diff PlanRun. Exit 2 at the CLI.
    repo = fixture_repo(tmp_path)  # committed corpus, no inbox at all
    database = fresh_db(tmp_path)
    with pytest.raises(EmptyInboxError, match="docs/planning/inbox"):
        run_stubs_only_plan(repo_root=repo, database=database, now=NOW)
    assert PlanRunRepo(database).list() == []

    code = main(
        ["plan", "--repo", str(repo), "--stubs-only"],
        database=database,
        client=MustNotBeCalledClient(),
        identity=FAKE_IDENTITY,
    )
    assert code == EXIT_PRECONDITION
    assert "docs/planning/inbox" in capsys.readouterr().err
    assert PlanRunRepo(database).list() == []


# --- AC-3: provenance records the mode ----------------------------------------


def test_stubs_only_provenance_records_the_mode(tmp_path: Path) -> None:
    # generation_stages == [] is the mode marker (zero stages is unreachable
    # generatively: single-call stores one record, staged stores three); the
    # other provenance columns carry the pinned sentinels, and input_doc_shas
    # still pins corpus + inbox so the AT-5 staleness re-check holds.
    repo, database = july_setup(tmp_path)
    result = run_stubs(repo, database)
    assert result.status is PlanRunStatus.PROPOSED
    stored = PlanRunRepo(database).list()[0]
    assert stored.generation_stages == []
    assert stored.model_provider == "none"
    assert stored.model_name == "stubs-only"
    assert stored.model_parameters == {}
    assert stored.prompt_version == "stubs-only"
    assert stored.prompt_hash == hashlib.sha256(b"").hexdigest()
    assert (
        stored.raw_output_hash
        == hashlib.sha256(
            json.dumps(stored.proposal, sort_keys=True).encode("utf-8")
        ).hexdigest()
    )
    assert "PRODUCT.md" in stored.input_doc_shas
    assert all(path in stored.input_doc_shas for path in JULY_STUBS)


# --- AC-4: --stubs-only and --staged are mutually exclusive -------------------


def test_stubs_only_and_staged_are_mutually_exclusive(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["plan", "--stubs-only", "--staged"])
    assert excinfo.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


# --- no PlannerClient means no ANTHROPIC_API_KEY ------------------------------


def test_stubs_only_runs_without_anthropic_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # client=None is the production path: the generative branch would try to
    # construct AnthropicPlannerClient here and exit 2 on the missing key;
    # stubs-only never constructs one.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    repo, database = july_setup(tmp_path)
    code = main(["plan", "--repo", str(repo), "--stubs-only"], database=database)
    assert code == EXIT_OK


# --- AC-6: apply consumes a stubs-only PlanRun unchanged ----------------------


def test_apply_consumes_a_stubs_only_plan_run_unchanged(tmp_path: Path) -> None:
    # The unchanged apply path: confirm gate, monotonic keys from the counter,
    # dependency materialisation (including the new->new sibling edge), the
    # renders, and the stub retirement lifecycle.
    repo, database = july_setup(tmp_path)
    _seed_counter(database, tickets=45, epics=1)
    run_stubs(repo, database)

    result = run_apply(
        repo_root=repo,
        database=database,
        now=APPLY_NOW,
        confirm=confirmed,
        planning_dir=tmp_path / "planning",
    )

    assert result.outcome == "applied"
    tickets = {ticket.key: ticket for ticket in TicketRepo(database).list()}
    minted = sorted(set(tickets) - set(JULY_BACKLOG_KEYS))
    assert minted == ["ATLAS-46", "ATLAS-47", "ATLAS-48", "ATLAS-49"]
    # All six declared edges materialised on top of the two seeded ones.
    assert len(TicketDependencyRepo(database).list()) == 2 + 6
    # The renders were written and the consumed stubs retired.
    assert (tmp_path / "planning" / "tickets.yaml").exists()
    inbox = repo / "docs" / "planning" / "inbox"
    for path in JULY_STUBS:
        name = Path(path).name
        assert not (inbox / name).exists()
        assert (inbox / "processed" / name).exists()


# --- ATLAS-159: durable stub anchors -----------------------------------------
#
# The sixteen-ticket live shape (named fixture): every live ticket whose
# source_anchor was minted against an active-inbox path that its own apply
# retired to processed/ — ATLAS-109/110/147-158 from the rendered ticket plus
# ATLAS-159/160, retired by the apply that minted them (PR #171; ratified
# premise delta). Keys, statuses, stub basenames, and heading slugs mirror the
# live store; sixteen tickets share ten stub files, exactly as live.

LIVE_SHAPE = (
    (
        "ATLAS-109",
        "rejected",
        "smoke-b-fixture.md",
        "smoke-b-fixture-add-a-delivery-loop-smoke-marker-to-the-readme",
    ),
    (
        "ATLAS-110",
        "done",
        "smoke-b-fixture-v2.md",
        "smoke-b-fixture-v2-document-the-delivery-loop-under-docs",
    ),
    (
        "ATLAS-147",
        "needs_human_decision",
        "inbox-stub-op8-linear-client-hardening.md",
        "linear-client-hardening-op-8",
    ),
    (
        "ATLAS-148",
        "needs_human_decision",
        "inbox-stub-op9-sync-request-budget.md",
        "sync-request-budget-op-9",
    ),
    (
        "ATLAS-149",
        "rejected",
        "inbox-stub-op8-linear-client-hardening.md",
        "linear-client-hardening-op-8",
    ),
    (
        "ATLAS-150",
        "rejected",
        "inbox-stub-op9-sync-request-budget.md",
        "sync-request-budget-op-9",
    ),
    (
        "ATLAS-151",
        "needs_human_decision",
        "inbox-stub-f4-promotion-dedup.md",
        "promotion-dedup-f-4",
    ),
    (
        "ATLAS-152",
        "needs_human_decision",
        "inbox-stub-retire-on-reject-scope.md",
        "retire-on-reject-scope",
    ),
    (
        "ATLAS-153",
        "needs_human_decision",
        "inbox-stub-stubs-only-plan-mode.md",
        "stubs-only-plan-mode",
    ),
    (
        "ATLAS-154",
        "needs_human_decision",
        "inbox-stub-accepted-types-spelling.md",
        "accepted-types-spelling",
    ),
    (
        "ATLAS-155",
        "rejected",
        "inbox-stub-accepted-types-spelling.md",
        "accepted-types-spelling",
    ),
    (
        "ATLAS-156",
        "rejected",
        "inbox-stub-f4-promotion-dedup.md",
        "promotion-dedup-f-4",
    ),
    (
        "ATLAS-157",
        "rejected",
        "inbox-stub-retire-on-reject-scope.md",
        "retire-on-reject-scope",
    ),
    (
        "ATLAS-158",
        "rejected",
        "inbox-stub-stubs-only-plan-mode.md",
        "stubs-only-plan-mode",
    ),
    (
        "ATLAS-159",
        "planned",
        "inbox-stub-durable-stub-anchors.md",
        "durable-stub-anchors",
    ),
    (
        "ATLAS-160",
        "planned",
        "inbox-stub-meta-label-discipline.md",
        "meta-label-discipline",
    ),
)

LIVE_INBOX = "docs/planning/inbox"
LIVE_TRIGGER_STUB = f"{LIVE_INBOX}/inbox-stub-trigger.md"


def _retired_stub_content(slug: str) -> str:
    # The first heading slugifies back to the live slug (§2.3: hyphens from
    # spaces), so the fixture anchors byte-match the live store's.
    return f"# {slug.replace('-', ' ')}\n\nRetired stub body.\n"


def live_shape_repo(
    tmp_path: Path,
    *,
    with_trigger_stub: bool = True,
    omit_processed: str | None = None,
) -> Path:
    files = {
        "PRODUCT.md": PRODUCT_MD,
        "docs/atlas/plan.md": PLAN_MD,
    }
    for _key, _status, basename, slug in LIVE_SHAPE:
        if basename == omit_processed:
            continue
        files[f"{LIVE_INBOX}/processed/{basename}"] = _retired_stub_content(slug)
    if with_trigger_stub:
        files[LIVE_TRIGGER_STUB] = _stub("Trigger stub")
    return make_repo(tmp_path, files)


def live_shape_db(tmp_path: Path) -> Database:
    database = fresh_db(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key=JULY_EPIC_KEY))
    EpicRepo(database).add(epic)
    for key, status, basename, slug in LIVE_SHAPE:
        TicketRepo(database).add(
            Ticket(
                **_ticket_model_kwargs(product.id, epic.id, key=key)
                | {
                    "title": f"Existing work {key}",
                    "status": status,
                    "source_anchor": f"{LIVE_INBOX}/{basename}#{slug}",
                }
            )
        )
    return database


def live_shape_setup(tmp_path: Path, **repo_kwargs: Any) -> tuple[Path, Database]:
    return live_shape_repo(tmp_path, **repo_kwargs), live_shape_db(tmp_path)


def test_promotion_then_retirement_round_trip_anchor_resolves(tmp_path: Path) -> None:
    # AC-1: REAL promotion (run_stubs_only_plan), REAL retirement (run_apply's
    # own _retire_inbox_stubs on the applied outcome), no mocks of either.
    # After the operator commits the retirement move, a fresh HEAD ingestion —
    # the exact index a later run's gate 4 resolves against — still resolves
    # every minted ticket's anchor, at its durable processed/ path.
    repo, database = july_setup(tmp_path)
    _seed_counter(database, tickets=45, epics=1)
    run_stubs(repo, database)
    result = run_apply(
        repo_root=repo,
        database=database,
        now=APPLY_NOW,
        confirm=confirmed,
        planning_dir=tmp_path / "planning",
    )
    assert result.outcome == "applied"
    # The retirement really moved the stubs (the round trip's second half).
    for path in JULY_STUBS:
        assert not (repo / path).exists()
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "land the retirement move")

    fresh_index = AnchorIndex.build(
        collect_input_documents(repo)
        + collect_inbox_documents(repo, DEFAULT_INBOX_DIR)
        + collect_processed_documents(repo, DEFAULT_INBOX_DIR)
    )
    minted = [
        ticket
        for ticket in TicketRepo(database).list()
        if ticket.key not in JULY_BACKLOG_KEYS
    ]
    assert len(minted) == 4
    for ticket in minted:
        resolved = fresh_index.resolve(ticket.source_anchor)
        assert resolved.path.startswith("docs/planning/inbox/processed/")


def test_retired_stub_echo_passes_gate4_after_fix(tmp_path: Path) -> None:
    # AC-3, post-fix half: after the round trip above, a SECOND stubs-only run
    # echoes the minted tickets verbatim — their durable anchors resolve at
    # gate 4 and the run is PROPOSED with exactly the new stub's ADD. (Pre-fix
    # this exact scenario failed gate 4 on the retired inbox path: the pre-fix
    # half is pinned by test_live_shape_dangling_echo_fails_gate4_pre_repair.)
    repo, database = july_setup(tmp_path)
    _seed_counter(database, tickets=45, epics=1)
    run_stubs(repo, database)
    run_apply(
        repo_root=repo,
        database=database,
        now=APPLY_NOW,
        confirm=confirmed,
        planning_dir=tmp_path / "planning",
    )
    (repo / LIVE_TRIGGER_STUB).write_text(_stub("Second batch"), encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "land the retirement move and a new stub")

    result = run_stubs(repo, database)

    assert result.status is PlanRunStatus.PROPOSED
    assert result.diff is not None
    ticket_entries = [e for e in result.diff.entries if e.kind == "ticket"]
    assert [entry.entry_type for entry in ticket_entries] == [ADD]
    assert result.failure_reason is None


def test_live_shape_dangling_echo_fails_gate4_pre_repair(tmp_path: Path) -> None:
    # AC-3, pre-fix half (the seeded regression's red state, pinned): the
    # sixteen-ticket live shape with its pre-repair active-inbox anchors fails
    # gate 4 as a typed recorded failure — one GATE4_UNRESOLVED_ANCHOR per
    # dangling ticket, every old anchor named. This is exactly the live
    # failure that blocks the ATLAS-153 door until the repair runs.
    repo, database = live_shape_setup(tmp_path)
    result = run_stubs(repo, database)

    assert result.status is PlanRunStatus.FAILED
    assert result.failure_reason is not None
    payload = json.loads(result.failure_reason)
    gate4 = [f for f in payload["failures"] if f["code"] == "GATE4_UNRESOLVED_ANCHOR"]
    assert len(gate4) == len(LIVE_SHAPE)
    reasons = " ".join(f["reason"] for f in gate4)
    for _key, _status, basename, slug in LIVE_SHAPE:
        assert f"{LIVE_INBOX}/{basename}#{slug}" in reasons
    stored = PlanRunRepo(database).list()
    assert len(stored) == 1
    assert stored[0].status is PlanRunStatus.FAILED


def test_live_shape_passes_gate4_post_repair(tmp_path: Path) -> None:
    # AC-2: the ruled branch (a) repair, applied to the live shape, rewrites
    # all sixteen named anchors to their durable processed/ spelling; the same
    # echo then passes gate 4 and the run is PROPOSED — the ATLAS-153 door
    # opens. The repair runs the REAL script functions (plan + apply), each
    # ticket named in its rewrite.
    import importlib

    repair = importlib.import_module("scripts.repair_stub_anchors")

    repo, database = live_shape_setup(tmp_path)
    rewrites, already = repair.plan_repair(database, repo)
    assert already == []
    assert [rewrite.key for rewrite in rewrites] == list(repair.REPAIR_KEYS)
    assert len(rewrites) == len(LIVE_SHAPE)
    repair.apply_repair(database, rewrites, now=APPLY_NOW)

    result = run_stubs(repo, database)

    assert result.status is PlanRunStatus.PROPOSED
    assert result.diff is not None
    ticket_entries = [e for e in result.diff.entries if e.kind == "ticket"]
    assert [entry.entry_type for entry in ticket_entries] == [ADD]  # the trigger
    counts = result.diff.counts
    assert counts[MODIFY] == 0
    assert counts[PROPOSE_ARCHIVE] == 0
    assert counts[CONFLICT] == 0


def test_empty_inbox_with_processed_stubs_still_clean_precondition(
    tmp_path: Path,
) -> None:
    # The DoD live-proof shape: the post-merge live probe is `atlas plan
    # --stubs-only` against an EMPTY active inbox with a populated processed/
    # subdir — exactly this fixture. It must fail ONLY on the empty-inbox
    # precondition (typed, clean exit, no PlanRun), never on gate 4.
    repo = live_shape_repo(tmp_path, with_trigger_stub=False)
    database = live_shape_db(tmp_path)
    with pytest.raises(EmptyInboxError, match="docs/planning/inbox"):
        run_stubs(repo, database)
    assert PlanRunRepo(database).list() == []


def test_inbox_processed_basename_collision_fails_closed(tmp_path: Path) -> None:
    # Ratified assumption 2: the same basename present in BOTH the active
    # inbox and processed/ is a typed fail-closed error at plan time — the
    # durable alias would collide with the real retired document, and
    # retirement's target-exists skip would re-read the stub forever.
    colliding = dict(JULY_STUBS)
    colliding["docs/planning/inbox/processed/inbox-stub-f4-promotion-dedup.md"] = (
        _retired_stub_content("promotion-dedup-f-4")
    )
    repo, database = july_setup(tmp_path, colliding)
    with pytest.raises(InboxCollisionError, match="inbox-stub-f4-promotion-dedup"):
        run_stubs(repo, database)
    assert PlanRunRepo(database).list() == []

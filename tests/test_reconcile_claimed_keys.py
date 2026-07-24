"""ATLAS-029M: bounded reconciliation of claimed ATLAS-187..192 keys."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from test_apply import _ticket_model_kwargs
from test_plan_pipeline import fresh_db, git, make_repo

from atlas.core.models import Ticket
from atlas.core.yaml_io import RenderHeader, render_document
from atlas.planning.mermaid import render_roadmap
from atlas.planning.pipeline import _next_key_hint
from atlas.storage import Database, KeyCounterRepo, PlanRunRepo, ProductRepo, TicketRepo
from atlas.tools.doc_linter import check_planning_renders
from scripts.reconcile_claimed_keys import (
    AUTHORIZED_STALE_PLAN_RUN_ID,
    CLAIMED_KEYS,
    MINT_PLAN_RUN_ID,
    MINT_PROMPT_VERSION,
    ExistingRecordMismatchError,
    ReconciliationError,
    RenderDriftError,
    _add_plan_run,
    _dispose_authorized_stale_run,
    _mint_proposal,
    _run_or_resume_apply,
    assert_no_unrelated_render_drift,
    reconcile_claimed_keys,
)

NOW = datetime(2026, 7, 24, 12, tzinfo=UTC)


def fixture_repo(tmp_path: Path) -> Path:
    header = RenderHeader(
        plan_run_id=UUID("2cdad1a4-383b-4abb-9a5d-4a128c54c4a5"),
        prompt_version="stubs-only",
        ticket_key_high_water=178,
        epic_key_high_water=11,
    )
    return make_repo(
        tmp_path,
        {
            "PRODUCT.md": "# Atlas\n",
            "README.md": "# Atlas\n",
            "ROADMAP.md": "# ROADMAP.md\n",
            "docs/atlas/plan.md": "# Planning\n\n## Backlog\n",
            "docs/planning/epics.yaml": render_document("epics", [], header),
            "docs/planning/tickets.yaml": render_document("tickets", [], header),
            "docs/planning/dependencies.yaml": render_document(
                "dependencies", [], header
            ),
            "docs/planning/roadmap.mmd": render_roadmap([], [], [], header),
        },
    )


def fixture_database(tmp_path: Path) -> Database:
    database = fresh_db(tmp_path)
    KeyCounterRepo(database).advance_to("ATLAS", 178)
    KeyCounterRepo(database).advance_to("ATLAS-E", 11)
    return database


def snapshot(repo: Path, database: Database) -> tuple[object, ...]:
    renders = tuple(
        (repo / "docs" / "planning" / name).read_bytes()
        for name in ("epics.yaml", "tickets.yaml", "dependencies.yaml", "roadmap.mmd")
    )
    return (
        KeyCounterRepo(database).high_water_marks(),
        [ticket.model_dump(mode="json") for ticket in TicketRepo(database).list()],
        [run.model_dump(mode="json") for run in PlanRunRepo(database).list()],
        renders,
    )


def test_seeded_defect_probe_bites_then_reconciliation_sets_next_key(
    tmp_path: Path,
) -> None:
    repo = fixture_repo(tmp_path)
    database = fixture_database(tmp_path)

    with pytest.raises(AssertionError):
        assert _next_key_hint(database) == "ATLAS-193"

    assert reconcile_claimed_keys(repo, database, now=NOW) == CLAIMED_KEYS
    assert _next_key_hint(database) == "ATLAS-193"


def test_six_done_records_have_exact_pr_mapping_and_no_linear_join(
    tmp_path: Path,
) -> None:
    repo = fixture_repo(tmp_path)
    database = fixture_database(tmp_path)
    reconcile_claimed_keys(repo, database, now=NOW)

    tickets = sorted(TicketRepo(database).list(), key=lambda ticket: ticket.key)
    assert [ticket.key for ticket in tickets] == list(CLAIMED_KEYS)
    assert [ticket.external_github_issue_id for ticket in tickets] == [
        "223",
        "224",
        "225",
        "226",
        "227",
        "228",
    ]
    assert all(ticket.status.value == "done" for ticket in tickets)
    assert all(ticket.external_linear_id is None for ticket in tickets)
    assert all(ticket.linear_synced_at is None for ticket in tickets)
    assert check_planning_renders(repo) == []


def test_running_twice_leaves_identical_store_and_renders(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fixture_database(tmp_path)
    reconcile_claimed_keys(repo, database, now=NOW)
    first = snapshot(repo, database)

    assert reconcile_claimed_keys(repo, database, now=NOW) == ()
    assert snapshot(repo, database) == first


def test_partial_ticket_run_converges_on_rerun(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fixture_database(tmp_path)
    KeyCounterRepo(database).advance_to("ATLAS", 186)
    _run_or_resume_apply(
        repo,
        database,
        NOW,
        run_id=MINT_PLAN_RUN_ID,
        prompt_version=MINT_PROMPT_VERSION,
        proposal=_mint_proposal(),
        add_only=True,
    )

    assert reconcile_claimed_keys(repo, database, now=NOW) == ()
    assert {ticket.key for ticket in TicketRepo(database).list()} == set(CLAIMED_KEYS)
    assert all(ticket.status.value == "done" for ticket in TicketRepo(database).list())


def test_mismatched_claimed_record_fails_before_counter_advance(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fixture_database(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    values = _ticket_model_kwargs(product.id, None, key="ATLAS-187")
    values.update(title="wrong", ticket_type="tech_debt")
    wanted = Ticket(**values)
    TicketRepo(database).add(wanted)

    with pytest.raises(ExistingRecordMismatchError, match="ATLAS-187"):
        reconcile_claimed_keys(repo, database, now=NOW)
    assert KeyCounterRepo(database).high_water_marks()["ATLAS"] == 178


def test_unrelated_store_render_drift_fails_closed(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fixture_database(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    values = _ticket_model_kwargs(product.id, None, key="ATLAS-50")
    values.update(ticket_type="tech_debt")
    unrelated = Ticket(**values)
    TicketRepo(database).add(unrelated)

    with pytest.raises(RenderDriftError, match=r"outside ATLAS-187\.\.192"):
        assert_no_unrelated_render_drift(repo, database)
    assert KeyCounterRepo(database).high_water_marks()["ATLAS"] == 178


def test_a6_disposes_only_the_authorized_proposed_run(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fixture_database(tmp_path)
    _add_plan_run(
        repo,
        database,
        NOW,
        run_id=AUTHORIZED_STALE_PLAN_RUN_ID,
        prompt_version="stale-test",
        proposal=_mint_proposal(),
    )

    assert _dispose_authorized_stale_run(database) is True
    stored = PlanRunRepo(database).get(AUTHORIZED_STALE_PLAN_RUN_ID)
    assert stored is not None
    assert stored.status.value == "rejected"
    with pytest.raises(ReconciliationError, match="not 'proposed'"):
        _dispose_authorized_stale_run(database)


def test_fixture_starts_clean_and_committed(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    assert git(repo, "status", "--short") == ""

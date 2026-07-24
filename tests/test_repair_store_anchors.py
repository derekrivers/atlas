"""ATLAS-031M: bounded repair of four dangling store source anchors."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
import sqlalchemy as sa
from test_apply import _epic_model_kwargs, _ticket_model_kwargs
from test_plan_pipeline import fresh_db, make_repo

from atlas.core.models import Epic, Ticket
from atlas.core.yaml_io import RenderHeader, render_document
from atlas.planning.mermaid import render_roadmap
from atlas.storage import (
    Database,
    EpicRepo,
    KeyCounterRepo,
    ProductRepo,
    TicketRepo,
)
from scripts import repair_store_anchors as repair

NOW = datetime(2026, 7, 24, 14, tzinfo=UTC)
E11_ANCHOR = "docs/atlas/implementation-roadmap.md#epic-organisational-learning-e11"
STUB_PATH = "docs/planning/inbox/atlas-031m-seeded-probe.md"
STUB_BODY = """\
---
title: "ATLAS-031M seeded probe"
objective: "Prove stubs-only clears gate 4 after the store repair."
context: "Fixture-only committed stub."
ticket_type: "feature"
epic_ref: "ATLAS-E11"
acceptance_criteria:
  - "The fixture proposal clears every gate."
non_goals:
  - "No live planning run."
test_requirements:
  - "Run the blocked command."
definition_of_done:
  - "The post-repair command exits zero."
---

# ATLAS-031M seeded probe
"""


def _seed_database(tmp_path: Path) -> Database:
    database = fresh_db(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic_values = _epic_model_kwargs(product.id, key="ATLAS-E11") | {
        "title": "Organisational Learning",
        "source_anchor": repair.OLD_E11_ANCHOR,
    }
    epic = Epic(**epic_values)
    EpicRepo(database).add(epic)
    tickets = (
        (
            "ATLAS-105",
            "Organisational memory search (`atlas lessons search`)",
            repair.OLD_E11_ANCHOR,
        ),
        (
            "ATLAS-106",
            "Continuous learning scheduler (`atlas lessons schedule`)",
            repair.OLD_E11_ANCHOR,
        ),
        (
            repair.ATLAS_192_KEY,
            repair.ATLAS_192_TITLE,
            repair.OLD_ATLAS_192_ANCHOR,
        ),
    )
    for key, title, anchor in tickets:
        values = _ticket_model_kwargs(product.id, epic.id, key=key) | {
            "title": title,
            "source_anchor": anchor,
        }
        TicketRepo(database).add(Ticket(**values))
    KeyCounterRepo(database).advance_to("ATLAS", 192)
    KeyCounterRepo(database).advance_to("ATLAS-E", 11)
    return database


def _fixture(tmp_path: Path) -> tuple[Path, Database]:
    database = _seed_database(tmp_path)
    header = RenderHeader(
        plan_run_id=UUID("ea58c897-3c4b-43c7-87fa-278f3c19b6d0"),
        prompt_version="fixture",
        ticket_key_high_water=192,
        epic_key_high_water=11,
    )
    epics = EpicRepo(database).list()
    tickets = TicketRepo(database).list()
    repo = make_repo(
        tmp_path,
        {
            "PRODUCT.md": "# Atlas\n",
            "ARCHITECTURE.md": "# Architecture\n",
            "ROADMAP.md": "# ROADMAP.md\n\nFixture roadmap.\n",
            "WORKFLOW.md": "# Workflow\n",
            "docs/atlas/implementation-roadmap.md": (
                "# Implementation Roadmap\n\n## Epic: Organisational Learning (E11)\n"
            ),
            STUB_PATH: STUB_BODY,
            "docs/planning/epics.yaml": render_document("epics", epics, header),
            "docs/planning/tickets.yaml": render_document("tickets", tickets, header),
            "docs/planning/dependencies.yaml": render_document(
                "dependencies", [], header
            ),
            "docs/planning/roadmap.mmd": render_roadmap(epics, tickets, [], header),
        },
    )
    return repo, database


def _run_blocked_command(
    repo: Path, database: Database
) -> subprocess.CompletedProcess[str]:
    command = [
        "uv",
        "run",
        "atlas",
        "plan",
        "--repo",
        str(repo),
        "--db",
        str(database.engine.url),
        "--stubs-only",
    ]
    return subprocess.run(
        command,
        cwd=Path(__file__).parents[1],
        env=os.environ | {"ATLAS_LIVE_TESTS": "0"},
        capture_output=True,
        text=True,
    )


def test_seeded_defect_blocked_command_fails_four_then_passes(
    tmp_path: Path,
) -> None:
    repo, database = _fixture(tmp_path)

    before = _run_blocked_command(repo, database)
    assert before.returncode != 0
    assert before.stderr.count("GATE4_UNRESOLVED_ANCHOR") == 4
    assert "Organisational memory search" in before.stderr
    assert "Continuous learning scheduler" in before.stderr
    assert "README.md" in before.stderr

    result = repair.repair_store_anchors(
        repo, database, now=NOW, backup_dir=tmp_path / "backups"
    )
    assert result.updated == ("ATLAS-E11", "ATLAS-105", "ATLAS-106", "ATLAS-192")
    assert result.backup_path is not None and result.backup_path.is_file()
    assert (repo / STUB_PATH).is_file()

    second = repair.repair_store_anchors(
        repo, database, now=NOW, backup_dir=tmp_path / "backups"
    )
    assert second.updated == ()
    assert second.backup_path is None

    after = _run_blocked_command(repo, database)
    assert after.returncode == 0, after.stderr
    assert "GATE4_UNRESOLVED_ANCHOR" not in after.stderr


def test_targets_derive_from_unique_committed_headings(tmp_path: Path) -> None:
    repo, _database = _fixture(tmp_path)
    targets = repair.derive_targets(repo)
    assert targets.e11 == E11_ANCHOR
    assert targets.atlas_192 == "ROADMAP.md#roadmapmd"


def test_target_derivation_refuses_ambiguous_heading(tmp_path: Path) -> None:
    repo, _database = _fixture(tmp_path)
    path = repo / repair.ROADMAP_PATH
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\n## Another Organisational Learning heading\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", repair.ROADMAP_PATH], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "ambiguous heading"], cwd=repo, check=True)
    with pytest.raises(repair.RepairRefusedError, match="2 headings"):
        repair.derive_targets(repo)


def test_preflight_refuses_unexpected_anchor_before_backup(tmp_path: Path) -> None:
    repo, database = _fixture(tmp_path)
    with database.session() as session, session.begin():
        ticket = session.execute(
            sa.text("SELECT id FROM tickets WHERE key = 'ATLAS-105'")
        ).one()
        session.execute(
            sa.text(
                "UPDATE tickets SET source_anchor = 'unexpected#anchor' WHERE id = :id"
            ),
            {"id": ticket.id},
        )
    backup_dir = tmp_path / "backups"
    with pytest.raises(repair.RepairRefusedError, match="expected exactly"):
        repair.repair_store_anchors(repo, database, now=NOW, backup_dir=backup_dir)
    assert not backup_dir.exists()


def test_repair_changes_no_entity_field_except_source_anchor(tmp_path: Path) -> None:
    repo, database = _fixture(tmp_path)
    before_epics = {epic.key: epic.model_dump() for epic in EpicRepo(database).list()}
    before_tickets = {
        ticket.key: ticket.model_dump() for ticket in TicketRepo(database).list()
    }

    repair.repair_store_anchors(
        repo, database, now=NOW, backup_dir=tmp_path / "backups"
    )

    after_epics = {epic.key: epic.model_dump() for epic in EpicRepo(database).list()}
    after_tickets = {
        ticket.key: ticket.model_dump() for ticket in TicketRepo(database).list()
    }
    for key in before_epics:
        old = before_epics[key]
        new = after_epics[key]
        assert old | {"source_anchor": new["source_anchor"]} == new
    for key in before_tickets:
        old = before_tickets[key]
        new = after_tickets[key]
        assert old | {"source_anchor": new["source_anchor"]} == new

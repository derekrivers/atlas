"""ATLAS-28: AT-5 / AT-6 provenance shadows at the persistence layer.

The audit (PR description / completion report) shows every provenance
value AT-5 and AT-6 need is already recorded by ATLAS-25/26/27. These
tests prove it is also RETRIEVABLE — the applied PlanRun read back through
`latest_applied()` (the row-9 addition) and the render header's
plan_run_id — and that the AT-5/AT-6 properties hold on the persisted
record.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from test_apply import apply, plan_then, planning_dir
from test_plan_pipeline import git

from atlas.core.models import PlanRunStatus
from atlas.storage import (
    EpicRepo,
    KeyCounterRepo,
    PlanRunRepo,
    TicketRepo,
)

# --- AT-5: applied provenance recorded AND retrievable ----------------------


def test_at5_applied_planrun_input_shas_match_git_tree(tmp_path: Path) -> None:
    repo, database = plan_then(tmp_path)
    apply(repo, database, planning_dir(tmp_path))

    applied = PlanRunRepo(database).latest_applied()
    assert applied is not None
    assert applied.status is PlanRunStatus.APPLIED
    head_shas = {
        "PRODUCT.md": git(repo, "rev-parse", "HEAD:PRODUCT.md").strip(),
        "docs/atlas/plan.md": git(repo, "rev-parse", "HEAD:docs/atlas/plan.md").strip(),
    }
    # The retrieved record's input_doc_shas match the git tree (AT-5).
    assert applied.input_doc_shas == head_shas


def test_at5_applied_run_retrievable_via_render_header(tmp_path: Path) -> None:
    repo, database = plan_then(tmp_path)
    pdir = planning_dir(tmp_path)
    apply(repo, database, pdir)

    # The render header links the applied backlog back to its PlanRun.
    header = (pdir / "tickets.yaml").read_text(encoding="utf-8").splitlines()
    id_line = next(line for line in header if line.startswith("# plan_run_id:"))
    plan_run_id = UUID(id_line.split(": ", 1)[1])

    fetched = PlanRunRepo(database).get(plan_run_id)
    assert fetched is not None
    assert fetched.status is PlanRunStatus.APPLIED
    assert fetched.input_doc_shas  # the provenance chain is intact
    # latest_applied() resolves to the same record.
    assert PlanRunRepo(database).latest_applied() == fetched


# --- AT-6: applied keys trace to the counter, not the model -----------------


def test_at6_applied_keys_trace_to_counter_not_proposal(tmp_path: Path) -> None:
    repo, database = plan_then(tmp_path)
    apply(repo, database, planning_dir(tmp_path))

    # The assigned keys are the counter's.
    assert {ticket.key for ticket in TicketRepo(database).list()} == {"ATLAS-1"}
    assert {epic.key for epic in EpicRepo(database).list()} == {"ATLAS-E1"}
    assert KeyCounterRepo(database).high_water_marks() == {"ATLAS": 1, "ATLAS-E": 1}

    # The model assigned none: the retrieved proposal carries only null keys.
    applied = PlanRunRepo(database).latest_applied()
    assert applied is not None
    assert all(ticket["key"] is None for ticket in applied.proposal["tickets"])
    assert all(epic["key"] is None for epic in applied.proposal["epics"])


def test_at6_counter_high_water_is_retrievable_after_apply(tmp_path: Path) -> None:
    repo, database = plan_then(tmp_path)
    apply(repo, database, planning_dir(tmp_path))
    # The counter is authoritative and readable; it never reissues (ATLAS-25).
    marks = KeyCounterRepo(database).high_water_marks()
    assert marks["ATLAS"] == 1
    assert marks["ATLAS-E"] == 1

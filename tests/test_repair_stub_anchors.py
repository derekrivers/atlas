"""ATLAS-159 branch (a): the one-time stub-anchor repair script.

The operator-ruled repair (plan gate, PR #172): `scripts/repair_stub_anchors.py`
rewrites the sixteen named tickets' dangling active-inbox anchors to their
durable `processed/` spelling — fail-closed per ticket BEFORE any write,
idempotent, one transaction, `source_anchor` + `updated_at` only, and never a
`docs/planning/` write (ADR-0007; the renders self-correct at the next apply).
Fixture-driven, ATLAS_LIVE_TESTS=0; seeded `assert 1 == 2` first (B011).
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from test_apply import APPLY_NOW
from test_plan_pipeline import git
from test_stubs_only import LIVE_INBOX, LIVE_SHAPE, live_shape_setup

from atlas.storage import TicketRepo

repair = importlib.import_module("scripts.repair_stub_anchors")


def test_repair_rewrites_exactly_the_named_set(tmp_path: Path) -> None:
    # Every rewrite is named (key, old, new); the set is exactly REPAIR_KEYS —
    # nothing else in the store is touched, and the rewritten rows carry the
    # durable spelling with updated_at bumped (the ratified audit trail) and
    # every other field byte-identical.
    repo, database = live_shape_setup(tmp_path)
    before = {t.key: t for t in TicketRepo(database).list()}

    rewrites, already = repair.plan_repair(database, repo)
    assert already == []
    assert [r.key for r in rewrites] == list(repair.REPAIR_KEYS)
    assert {r.key for r in rewrites} == {key for key, _s, _b, _sl in LIVE_SHAPE}
    for rewrite, (key, _status, basename, slug) in zip(
        rewrites, LIVE_SHAPE, strict=True
    ):
        assert rewrite.key == key
        assert rewrite.old_anchor == f"{LIVE_INBOX}/{basename}#{slug}"
        assert rewrite.new_anchor == f"{LIVE_INBOX}/processed/{basename}#{slug}"

    repair.apply_repair(database, rewrites, now=APPLY_NOW)
    after = {t.key: t for t in TicketRepo(database).list()}
    for key, _status, basename, slug in LIVE_SHAPE:
        assert after[key].source_anchor == f"{LIVE_INBOX}/processed/{basename}#{slug}"
        assert after[key].updated_at == APPLY_NOW
        assert after[key].title == before[key].title
        assert after[key].status == before[key].status
        assert after[key].acceptance_criteria == before[key].acceptance_criteria


def test_repair_refuses_missing_processed_file(tmp_path: Path) -> None:
    # Fail-closed: one named ticket's retired stub is absent from processed/,
    # so its rewritten anchor cannot resolve — the WHOLE run refuses before
    # any write, naming the offending anchor.
    repo, database = live_shape_setup(
        tmp_path, omit_processed="inbox-stub-f4-promotion-dedup.md"
    )
    with pytest.raises(repair.RepairRefusedError, match="does not resolve"):
        repair.plan_repair(database, repo)
    # Nothing was written: every anchor still carries the old spelling.
    for ticket in TicketRepo(database).list():
        assert "/processed/" not in ticket.source_anchor


def test_repair_refuses_unretired_stub(tmp_path: Path) -> None:
    # Fail-closed: a named ticket whose anchor cites a stub STILL in the
    # active inbox is not dangling — repairing it would forge history, so the
    # run refuses and tells the operator to investigate.
    repo, database = live_shape_setup(tmp_path)
    unretired = repo / LIVE_INBOX / "inbox-stub-durable-stub-anchors.md"
    unretired.write_text("# durable stub anchors\n\nStill active.\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "the stub was never retired")
    with pytest.raises(repair.RepairRefusedError, match="still in the active"):
        repair.plan_repair(database, repo)


def test_repair_is_idempotent(tmp_path: Path) -> None:
    # A second run plans zero rewrites and reports every key as already
    # repaired — never a double rewrite, never a refusal on repaired state.
    repo, database = live_shape_setup(tmp_path)
    rewrites, _ = repair.plan_repair(database, repo)
    repair.apply_repair(database, rewrites, now=APPLY_NOW)

    again, already = repair.plan_repair(database, repo)
    assert again == []
    assert already == list(repair.REPAIR_KEYS)

    # The CLI seam agrees: exit 0, nothing to repair.
    code = repair.main(
        ["--db", f"sqlite:///{tmp_path}/atlas.db", "--repo", str(repo), "--yes"]
    )
    assert code == repair.EXIT_OK


def test_repair_touches_no_planning_files(tmp_path: Path) -> None:
    # ADR-0007: the repair is a DB-only write. The repository tree — renders,
    # inbox, processed — is byte-identical afterwards; renders self-correct at
    # the next apply.
    repo, database = live_shape_setup(tmp_path)
    rewrites, _ = repair.plan_repair(database, repo)
    repair.apply_repair(database, rewrites, now=APPLY_NOW)
    assert git(repo, "status", "--porcelain") == ""

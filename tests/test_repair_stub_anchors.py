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
from typing import Any

import pytest
from test_apply import APPLY_NOW, _epic_model_kwargs, _ticket_model_kwargs
from test_plan_pipeline import PLAN_MD, PRODUCT_MD, fresh_db, git, make_repo
from test_stubs_only import JULY_EPIC_KEY, LIVE_INBOX, LIVE_SHAPE, live_shape_setup

from atlas.context import build_context_pack, select_doc_sections
from atlas.core.anchors import SourceDocument
from atlas.core.models import Epic, Ticket
from atlas.dependencies import project_graph
from atlas.planning.ingestion import collect_processed_documents, processed_path_for
from atlas.planning.pipeline import DEFAULT_INBOX_DIR
from atlas.storage import Database, EpicRepo, ProductRepo, TicketRepo

repair = importlib.import_module("scripts.repair_stub_anchors")


RELEVANT_DOC_STATUSES = {
    "ATLAS-98": "needs_human_decision",
    "ATLAS-109": "rejected",
    "ATLAS-110": "done",
    "ATLAS-147": "needs_human_decision",
    "ATLAS-148": "needs_human_decision",
    "ATLAS-149": "rejected",
    "ATLAS-150": "rejected",
    "ATLAS-151": "needs_human_decision",
    "ATLAS-152": "needs_human_decision",
    "ATLAS-153": "needs_human_decision",
    "ATLAS-154": "needs_human_decision",
    "ATLAS-155": "rejected",
    "ATLAS-156": "rejected",
    "ATLAS-157": "rejected",
    "ATLAS-158": "rejected",
    "ATLAS-159": "planned",
    "ATLAS-160": "planned",
    "ATLAS-161": "planned",
    "ATLAS-162": "planned",
    "ATLAS-163": "planned",
    "ATLAS-164": "planned",
    "ATLAS-165": "planned",
}
ANCHOR_FIXTURE_PATH = f"{LIVE_INBOX}/processed/inbox-stub-anchor-fixture.md"
ANCHOR_FIXTURE = "# Anchor Fixture\n\nThe primary ticket anchor.\n"
ABSENT_RELEVANT_DOC = f"{LIVE_INBOX}/missing-stub.md"


def _processed_stub_body(path: str) -> str:
    title = Path(path).stem.replace("-", " ")
    return f"# {title}\n\nRetired relevant_docs fixture.\n"


def relevant_docs_repo(
    tmp_path: Path,
    *,
    omit_processed: str | None = None,
    active_path: str | None = None,
) -> Path:
    files = {
        "PRODUCT.md": PRODUCT_MD,
        "docs/atlas/plan.md": PLAN_MD,
        ANCHOR_FIXTURE_PATH: ANCHOR_FIXTURE,
    }
    for _key, old_path in repair.RELEVANT_DOC_REPAIR_PATHS:
        new_path = processed_path_for(old_path)
        if Path(new_path).name == omit_processed:
            continue
        files[new_path] = _processed_stub_body(new_path)
    if active_path is not None:
        files[active_path] = "# Still Active\n\nNot retired.\n"
    return make_repo(tmp_path, files)


def relevant_docs_db(
    tmp_path: Path,
    *,
    already: bool = False,
    include_absent: bool = False,
    outside_defect: bool = False,
) -> Database:
    database = fresh_db(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key=JULY_EPIC_KEY))
    EpicRepo(database).add(epic)
    for key, old_path in repair.RELEVANT_DOC_REPAIR_PATHS:
        ref = processed_path_for(old_path) if already else old_path
        TicketRepo(database).add(
            Ticket(
                **_ticket_model_kwargs(product.id, epic.id, key=key)
                | {
                    "title": f"Relevant docs repair fixture {key}",
                    "status": RELEVANT_DOC_STATUSES[key],
                    "source_anchor": f"{ANCHOR_FIXTURE_PATH}#anchor-fixture",
                    "relevant_docs": [ref],
                }
            )
        )
    if include_absent:
        TicketRepo(database).add(
            Ticket(
                **_ticket_model_kwargs(product.id, epic.id, key="ATLAS-999")
                | {
                    "title": "Absent relevant doc stays absent",
                    "source_anchor": f"{ANCHOR_FIXTURE_PATH}#anchor-fixture",
                    "relevant_docs": [ABSENT_RELEVANT_DOC],
                }
            )
        )
    if outside_defect:
        _key, old_path = repair.RELEVANT_DOC_REPAIR_PATHS[0]
        TicketRepo(database).add(
            Ticket(
                **_ticket_model_kwargs(product.id, epic.id, key="ATLAS-998")
                | {
                    "title": "Outside named set",
                    "source_anchor": f"{ANCHOR_FIXTURE_PATH}#anchor-fixture",
                    "relevant_docs": [old_path],
                }
            )
        )
    return database


def relevant_docs_setup(tmp_path: Path, **db_kwargs: Any) -> tuple[Path, Database]:
    return relevant_docs_repo(tmp_path), relevant_docs_db(tmp_path, **db_kwargs)


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


# --- ATLAS-165: relevant_docs active-inbox spelling repair -------------------


def test_relevant_docs_repair_scan_finds_no_retired_active_paths_after_repair(
    tmp_path: Path,
) -> None:
    # Named live-shaped scan: every stored relevant_docs path that exists only
    # under processed/ is found before the repair and zero remain afterwards.
    # The absent-both row is not a spelling repair candidate.
    repo, database = relevant_docs_setup(tmp_path, include_absent=True)
    before = {ticket.key: ticket for ticket in TicketRepo(database).list()}

    defects = repair.scan_retired_relevant_docs(database, repo)

    assert {(defect.key, defect.old_path, defect.new_path) for defect in defects} == {
        (key, old_path, processed_path_for(old_path))
        for key, old_path in repair.RELEVANT_DOC_REPAIR_PATHS
    }

    rewrites, already = repair.plan_relevant_docs_repair(database, repo)
    assert already == []
    assert [rewrite.key for rewrite in rewrites] == list(
        repair.RELEVANT_DOC_REPAIR_KEYS
    )
    for rewrite in rewrites:
        ((old_path, new_path),) = rewrite.rewrites
        assert rewrite.old_relevant_docs == (old_path,)
        assert rewrite.new_relevant_docs == (new_path,)

    repair.apply_relevant_docs_repair(database, rewrites, now=APPLY_NOW)

    assert repair.scan_retired_relevant_docs(database, repo) == []
    after = {ticket.key: ticket for ticket in TicketRepo(database).list()}
    for key, old_path in repair.RELEVANT_DOC_REPAIR_PATHS:
        assert after[key].relevant_docs == [processed_path_for(old_path)]
        assert after[key].updated_at == APPLY_NOW
        assert after[key].model_dump(exclude={"relevant_docs", "updated_at"}) == before[
            key
        ].model_dump(exclude={"relevant_docs", "updated_at"})

    # Negative guard: a genuinely absent document is neither rewritten nor
    # timestamp-bumped by this spelling repair.
    assert after["ATLAS-999"].relevant_docs == [ABSENT_RELEVANT_DOC]
    assert after["ATLAS-999"].updated_at == before["ATLAS-999"].updated_at


def test_relevant_docs_repair_refuses_row_outside_named_set(tmp_path: Path) -> None:
    # Fail-closed: any additional stored row with the same retired-active shape
    # is drift from the enumerated set, so the whole run refuses before writes.
    repo, database = relevant_docs_setup(tmp_path, outside_defect=True)
    before = {ticket.key: ticket for ticket in TicketRepo(database).list()}

    with pytest.raises(repair.RepairRefusedError, match="outside the named"):
        repair.plan_relevant_docs_repair(database, repo)

    after = {ticket.key: ticket for ticket in TicketRepo(database).list()}
    assert after == before


def test_relevant_docs_repair_refuses_missing_processed_file(tmp_path: Path) -> None:
    # Fail-closed: the active spelling is retired but the durable processed/
    # file is missing for a named row, so no relevant_docs row is rewritten.
    repo = relevant_docs_repo(
        tmp_path, omit_processed="inbox-stub-f4-promotion-dedup.md"
    )
    database = relevant_docs_db(tmp_path)

    with pytest.raises(repair.RepairRefusedError, match="not present"):
        repair.plan_relevant_docs_repair(database, repo)

    for ticket in TicketRepo(database).list():
        assert "/processed/" not in "".join(ticket.relevant_docs)


def test_relevant_docs_repair_refuses_unretired_active_file(tmp_path: Path) -> None:
    # Fail-closed: a named relevant_docs entry whose active stub still exists
    # is not retired, so rewriting it would forge the stored state.
    active_path = "docs/planning/inbox/inbox-stub-durable-stub-anchors.md"
    repo = relevant_docs_repo(tmp_path, active_path=active_path)
    database = relevant_docs_db(tmp_path)

    with pytest.raises(repair.RepairRefusedError, match="still exists"):
        repair.plan_relevant_docs_repair(database, repo)


def test_relevant_docs_repair_is_idempotent_and_cli_prints_each_rewrite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, database = relevant_docs_setup(tmp_path)

    code = repair.main(
        [
            "--db",
            f"sqlite:///{tmp_path}/atlas.db",
            "--repo",
            str(repo),
            "--repair",
            "relevant-docs",
            "--yes",
        ]
    )

    assert code == repair.EXIT_OK
    out = capsys.readouterr().out
    assert (
        "ATLAS-98: relevant_docs docs/planning/inbox/smoke-b-fixture.md -> "
        "docs/planning/inbox/processed/smoke-b-fixture.md"
    ) in out
    assert (
        "ATLAS-165: relevant_docs "
        "docs/planning/inbox/inbox-stub-relevant-docs-repair.md -> "
        "docs/planning/inbox/processed/inbox-stub-relevant-docs-repair.md"
    ) in out
    assert "repaired 22 ticket relevant_docs row(s)" in out

    again, already = repair.plan_relevant_docs_repair(database, repo)
    assert again == []
    assert already == list(repair.RELEVANT_DOC_REPAIR_KEYS)

    code = repair.main(
        [
            "--db",
            f"sqlite:///{tmp_path}/atlas.db",
            "--repo",
            str(repo),
            "--repair",
            "relevant-docs",
            "--yes",
        ]
    )
    assert code == repair.EXIT_OK
    assert "nothing to repair" in capsys.readouterr().out


def test_absent_relevant_doc_still_soft_skips_after_repair(tmp_path: Path) -> None:
    # Negative: this repair fixes old->processed spellings only. A document that
    # exists at neither spelling keeps the renderer's existing soft-skip posture.
    repo, database = relevant_docs_setup(tmp_path, include_absent=True)
    rewrites, _ = repair.plan_relevant_docs_repair(database, repo)
    repair.apply_relevant_docs_repair(database, rewrites, now=APPLY_NOW)
    ticket = TicketRepo(database).get_by_key("ATLAS-999")
    assert ticket is not None

    ctx = select_doc_sections(
        [
            SourceDocument(
                path=ANCHOR_FIXTURE_PATH, sha="sha-anchor", content=ANCHOR_FIXTURE
            )
        ],
        ticket,
    )

    assert ctx.references == ()


def test_pack_rendering_includes_formerly_skipped_relevant_doc_after_repair(
    tmp_path: Path,
) -> None:
    # Seeded regression: before the stored-data rewrite, the active-inbox
    # spelling exact-misses the processed-only corpus and is skipped. After the
    # rewrite, the same fixture pack records and renders the formerly skipped
    # document path.
    repo, database = relevant_docs_setup(tmp_path)
    old_path = "docs/planning/inbox/smoke-b-fixture.md"
    new_path = processed_path_for(old_path)

    def render_fixture_pack() -> Any:
        ticket = TicketRepo(database).get_by_key("ATLAS-98")
        assert ticket is not None
        return build_context_pack(
            ticket,
            graph=project_graph([ticket], [], [], []),
            documents=collect_processed_documents(repo, DEFAULT_INBOX_DIR),
            accepted_adrs=[],
            lessons=[],
        )

    pre = render_fixture_pack()
    assert new_path not in pre.relevant_docs  # wrong answer: pre-fix path resolves
    assert f"- {new_path}" not in pre.rendered_markdown

    rewrites, _ = repair.plan_relevant_docs_repair(database, repo)
    repair.apply_relevant_docs_repair(database, rewrites, now=APPLY_NOW)

    post = render_fixture_pack()
    assert new_path in post.relevant_docs
    assert post.input_doc_shas[new_path]
    assert f"- {new_path}" in post.rendered_markdown

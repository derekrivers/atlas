"""Repair the four ATLAS-031M store anchors and regenerate planning renders.

This is a bounded, one-shot operational repair.  Canonical headings are read
from committed HEAD and slugged by the shared anchor implementation; every
named record and every committed render is verified before the first write.
The store rewrite changes ``source_anchor`` only, then a zero-model PlanRun is
applied through ``run_apply`` so that no caller writes ``docs/planning/``.

Usage:
    uv run python scripts/repair_store_anchors.py [--repo PATH] [--db URL]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import sqlalchemy as sa

from atlas.core.anchors import parse_headings, slugify
from atlas.core.models import Epic, PlanRun, PlanRunStatus, Ticket
from atlas.core.yaml_io import RenderHeader, render_document
from atlas.planning.apply import ApplyDecision, run_apply
from atlas.planning.ingestion import (
    collect_inbox_documents,
    collect_input_documents,
    collect_processed_documents,
)
from atlas.planning.mermaid import render_roadmap
from atlas.planning.pipeline import _echo_backlog_proposal
from atlas.planning.reconciler import DEFAULT_SIMILARITY_THRESHOLD, Backlog, reconcile
from atlas.storage import (
    Database,
    EpicRepo,
    PlanRunRepo,
    ProductRepo,
    TicketDependencyRepo,
    TicketRepo,
)
from atlas.storage.tables import EpicRow, TicketRow

EXIT_OK = 0
EXIT_REFUSED = 2
ROADMAP_PATH = "docs/atlas/implementation-roadmap.md"
ROOT_ROADMAP_PATH = "ROADMAP.md"
OLD_E11_ANCHOR = f"{ROADMAP_PATH}#epic-organisational-learning"
OLD_ATLAS_192_ANCHOR = "README.md#atlas"
E11_EPIC_TITLE = "Organisational Learning"
E11_TICKET_TITLES = (
    "Organisational memory search",
    "Continuous learning scheduler",
)
ATLAS_192_KEY = "ATLAS-192"
ATLAS_192_TITLE = "reconcile root documentation pointers"
RENDER_PLAN_RUN_ID = uuid5(NAMESPACE_URL, "atlas:store-anchor-repair:ATLAS-031M")
RENDER_PROMPT_VERSION = "store-anchor-repair-render-v1"
RENDER_NAMES = ("epics.yaml", "tickets.yaml", "dependencies.yaml", "roadmap.mmd")
# This render-only PlanRun must not consume the operator's active follow-up
# inbox.  run_apply accepts an explicit inbox domain, and an absent dedicated
# domain gives this one-shot run the same AT-5 check without retiring real
# stubs that a later `atlas plan --stubs-only` must mint.
REPAIR_INBOX_DIR = Path("docs/planning/atlas-031m-render-inbox")


class RepairRefusedError(RuntimeError):
    """The bounded repair cannot safely proceed."""


@dataclass(frozen=True)
class AnchorTargets:
    e11: str
    atlas_192: str


@dataclass(frozen=True)
class AnchorRewrite:
    kind: str
    identity: str
    old_anchor: str
    new_anchor: str


@dataclass(frozen=True)
class RepairResult:
    updated: tuple[str, ...]
    already_repaired: tuple[str, ...]
    backup_path: Path | None
    plan_run_id: UUID


def _head_text(repo_root: Path, path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"HEAD:{path}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RepairRefusedError(
            f"cannot read {path!r} at committed HEAD: {result.stderr.strip()}"
        )
    return result.stdout


def _unique_heading(
    content: str,
    *,
    path: str,
    contains: str | None = None,
    level: int | None = None,
) -> str:
    candidates = [
        heading
        for heading in parse_headings(content)
        if (contains is None or contains in heading.text)
        and (level is None or heading.level == level)
    ]
    if len(candidates) != 1:
        raise RepairRefusedError(
            f"{path!r} at HEAD has {len(candidates)} headings matching "
            f"contains={contains!r}, level={level!r}; expected exactly one"
        )
    return candidates[0].text


def derive_targets(repo_root: Path) -> AnchorTargets:
    """Derive both destinations from unique headings at committed HEAD."""
    e11_heading = _unique_heading(
        _head_text(repo_root, ROADMAP_PATH),
        path=ROADMAP_PATH,
        contains="Organisational Learning",
    )
    root_heading = _unique_heading(
        _head_text(repo_root, ROOT_ROADMAP_PATH),
        path=ROOT_ROADMAP_PATH,
        level=1,
    )
    return AnchorTargets(
        e11=f"{ROADMAP_PATH}#{slugify(e11_heading)}",
        atlas_192=f"{ROOT_ROADMAP_PATH}#{slugify(root_heading)}",
    )


def _matching_epic(database: Database) -> Epic:
    matches = [
        epic for epic in EpicRepo(database).list() if E11_EPIC_TITLE in epic.title
    ]
    if len(matches) != 1:
        raise RepairRefusedError(
            f"store has {len(matches)} epics containing {E11_EPIC_TITLE!r}; "
            "expected exactly one"
        )
    return matches[0]


def _matching_ticket(database: Database, title: str) -> Ticket:
    matches = [
        ticket for ticket in TicketRepo(database).list() if title in ticket.title
    ]
    if len(matches) != 1:
        raise RepairRefusedError(
            f"store has {len(matches)} tickets containing {title!r}; "
            "expected exactly one"
        )
    return matches[0]


def plan_repair(
    database: Database, targets: AnchorTargets
) -> tuple[list[AnchorRewrite], list[str]]:
    """Verify the exact four-record defect before any write."""
    epic = _matching_epic(database)
    memory = _matching_ticket(database, E11_TICKET_TITLES[0])
    scheduler = _matching_ticket(database, E11_TICKET_TITLES[1])
    atlas_192 = TicketRepo(database).get_by_key(ATLAS_192_KEY)
    if atlas_192 is None or atlas_192.title != ATLAS_192_TITLE:
        actual = None if atlas_192 is None else atlas_192.title
        raise RepairRefusedError(
            f"{ATLAS_192_KEY} has title {actual!r}; expected {ATLAS_192_TITLE!r}"
        )

    expected = (
        ("epic", epic.key, epic.source_anchor, OLD_E11_ANCHOR, targets.e11),
        ("ticket", memory.key, memory.source_anchor, OLD_E11_ANCHOR, targets.e11),
        (
            "ticket",
            scheduler.key,
            scheduler.source_anchor,
            OLD_E11_ANCHOR,
            targets.e11,
        ),
        (
            "ticket",
            atlas_192.key,
            atlas_192.source_anchor,
            OLD_ATLAS_192_ANCHOR,
            targets.atlas_192,
        ),
    )
    states = []
    for kind, identity, actual, old, new in expected:
        if actual == old:
            states.append("old")
        elif actual == new:
            states.append("new")
        else:
            raise RepairRefusedError(
                f"{kind} {identity} has source_anchor {actual!r}; "
                f"expected exactly {old!r} or already-repaired {new!r}"
            )
    if len(set(states)) != 1:
        raise RepairRefusedError(
            "the four source anchors are partially repaired; refusing mixed state"
        )
    if states[0] == "new":
        return [], [identity for _kind, identity, *_rest in expected]
    return (
        [
            AnchorRewrite(kind, identity, old, new)
            for kind, identity, _actual, old, new in expected
        ],
        [],
    )


def _render_header(text: str) -> RenderHeader:
    values: dict[str, str] = {}
    for line in text.splitlines()[:5]:
        marker = "%% " if line.startswith("%% ") else "# "
        if line.startswith(marker) and ": " in line:
            name, value = line[len(marker) :].split(": ", 1)
            values[name] = value
    try:
        return RenderHeader(
            plan_run_id=values["plan_run_id"],
            prompt_version=values["prompt_version"],
            ticket_key_high_water=int(values["ticket_key_high_water"]),
            epic_key_high_water=int(values["epic_key_high_water"]),
        )
    except (KeyError, ValueError) as error:
        raise RepairRefusedError("committed render header is malformed") from error


def _render_set(database: Database, header: RenderHeader) -> dict[str, str]:
    epics = EpicRepo(database).list()
    tickets = TicketRepo(database).list()
    dependencies = TicketDependencyRepo(database).list()
    return {
        "epics.yaml": render_document("epics", epics, header),
        "tickets.yaml": render_document("tickets", tickets, header),
        "dependencies.yaml": render_document("dependencies", dependencies, header),
        "roadmap.mmd": render_roadmap(epics, tickets, dependencies, header),
    }


def assert_store_matches_renders(repo_root: Path, database: Database) -> None:
    """Refuse operational drift before the source-anchor transaction."""
    planning = repo_root / "docs" / "planning"
    committed = {
        name: (planning / name).read_text(encoding="utf-8") for name in RENDER_NAMES
    }
    header = _render_header(committed["tickets.yaml"])
    expected = _render_set(database, header)
    mismatches = [name for name in RENDER_NAMES if committed[name] != expected[name]]
    if mismatches:
        raise RepairRefusedError(
            "live store differs from committed planning renders before repair: "
            + ", ".join(mismatches)
        )


def _sqlite_path(database: Database) -> Path:
    url = database.engine.url
    if (
        url.get_backend_name() != "sqlite"
        or not url.database
        or url.database == ":memory:"
    ):
        raise RepairRefusedError(
            "ATLAS-031M backup requires a file-backed SQLite database"
        )
    return Path(url.database).resolve()


def create_backup(
    database: Database, *, now: datetime, backup_dir: Path | None = None
) -> Path:
    source = _sqlite_path(database)
    destination_dir = (
        backup_dir if backup_dir is not None else source.parent / "backups"
    )
    destination_dir.mkdir(parents=True, exist_ok=True)
    timestamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = destination_dir / f"{source.stem}-before-atlas-031m-{timestamp}.db"
    if destination.exists():
        raise RepairRefusedError(f"backup path already exists: {destination}")
    with (
        closing(sqlite3.connect(source)) as source_db,
        closing(sqlite3.connect(destination)) as backup_db,
    ):
        source_db.backup(backup_db)
    return destination


def apply_source_anchor_rewrites(
    database: Database, rewrites: list[AnchorRewrite]
) -> None:
    """Change only the four preflighted ``source_anchor`` columns."""
    with database.session() as session, session.begin():
        for rewrite in rewrites:
            row_type = EpicRow if rewrite.kind == "epic" else TicketRow
            row = session.scalars(
                sa.select(row_type).where(row_type.key == rewrite.identity)
            ).one()
            if row.source_anchor != rewrite.old_anchor:
                raise RepairRefusedError(
                    f"{rewrite.kind} {rewrite.identity} changed after preflight"
                )
            row.source_anchor = rewrite.new_anchor


def _backlog(database: Database) -> Backlog:
    return Backlog(
        epics=EpicRepo(database).list(),
        tickets=TicketRepo(database).list(),
        dependencies=TicketDependencyRepo(database).list(),
    )


def _document_shas(repo_root: Path) -> dict[str, str]:
    documents = (
        collect_input_documents(repo_root)
        + collect_inbox_documents(repo_root, REPAIR_INBOX_DIR)
        + collect_processed_documents(repo_root, REPAIR_INBOX_DIR)
    )
    return {document.path: document.sha for document in documents}


def _add_render_plan_run(
    repo_root: Path, database: Database, *, now: datetime
) -> PlanRun:
    proposed = PlanRunRepo(database).latest_proposed()
    if proposed is not None:
        if proposed.id == RENDER_PLAN_RUN_ID:
            return proposed
        raise RepairRefusedError(
            f"unrelated proposed PlanRun {proposed.id} exists; dispose it first"
        )
    backlog = _backlog(database)
    proposal = _echo_backlog_proposal(backlog)
    diff = reconcile(
        proposal, backlog, similarity_threshold=DEFAULT_SIMILARITY_THRESHOLD
    )
    raw = json.dumps(proposal.model_dump(mode="json"), sort_keys=True)
    product = ProductRepo(database).get_by_key("ATLAS")
    if product is None:
        raise RepairRefusedError("no ATLAS product exists in the target store")
    run = PlanRun(
        id=RENDER_PLAN_RUN_ID,
        product_id=product.id,
        status=PlanRunStatus.PROPOSED,
        input_doc_shas=_document_shas(repo_root),
        model_provider="none",
        model_name="store-anchor-repair",
        prompt_version=RENDER_PROMPT_VERSION,
        prompt_hash=hashlib.sha256(b"").hexdigest(),
        model_parameters={},
        similarity_threshold=DEFAULT_SIMILARITY_THRESHOLD,
        raw_output_hash=hashlib.sha256(raw.encode()).hexdigest(),
        proposal=proposal.model_dump(mode="json"),
        generation_stages=[],
        diff_summary=diff.as_summary(),
        failure_reason=None,
        approved_by=None,
        created_at=now,
        applied_at=None,
    )
    PlanRunRepo(database).add(run)
    return run


def _apply_render_plan_run(
    repo_root: Path, database: Database, *, now: datetime
) -> PlanRun:
    run = PlanRunRepo(database).get(RENDER_PLAN_RUN_ID)
    if run is None:
        run = _add_render_plan_run(repo_root, database, now=now)
    if run.prompt_version != RENDER_PROMPT_VERSION:
        raise RepairRefusedError(
            f"PlanRun {RENDER_PLAN_RUN_ID} has unexpected provenance"
        )
    if run.status is PlanRunStatus.APPLIED:
        return run
    if run.status is not PlanRunStatus.PROPOSED:
        raise RepairRefusedError(
            f"PlanRun {RENDER_PLAN_RUN_ID} is {run.status.value!r}, not resumable"
        )
    result = run_apply(
        repo_root=repo_root,
        database=database,
        now=now,
        confirm=lambda _: ApplyDecision.CONFIRMED,
        inbox_dir=REPAIR_INBOX_DIR,
    )
    if result.outcome != "applied" or result.plan_run.id != RENDER_PLAN_RUN_ID:
        raise RepairRefusedError("zero-model render PlanRun was not applied")
    return result.plan_run


def assert_bounded_render_delta(
    before: dict[str, str], after: dict[str, str], targets: AnchorTargets
) -> None:
    expected_anchor_deltas = {
        "epics.yaml": [(OLD_E11_ANCHOR, targets.e11)],
        "tickets.yaml": [
            (OLD_E11_ANCHOR, targets.e11),
            (OLD_E11_ANCHOR, targets.e11),
            (OLD_ATLAS_192_ANCHOR, targets.atlas_192),
        ],
        "dependencies.yaml": [],
        "roadmap.mmd": [],
    }
    for name in RENDER_NAMES:
        before_lines = before[name].splitlines()
        after_lines = after[name].splitlines()
        if len(before_lines) != len(after_lines):
            raise RepairRefusedError(f"{name} changed line count during repair")
        observed = []
        for index, (old_line, new_line) in enumerate(
            zip(before_lines, after_lines, strict=True)
        ):
            if old_line == new_line or index < 5:
                continue
            observed.append(
                (
                    old_line.removeprefix("  source_anchor: "),
                    new_line.removeprefix("  source_anchor: "),
                )
            )
        if observed != expected_anchor_deltas[name]:
            raise RepairRefusedError(
                f"{name} changed outside its bounded anchor delta: {observed!r}"
            )


def repair_store_anchors(
    repo_root: Path,
    database: Database,
    *,
    now: datetime,
    backup_dir: Path | None = None,
) -> RepairResult:
    targets = derive_targets(repo_root)
    rewrites, already = plan_repair(database, targets)
    planning = repo_root / "docs" / "planning"
    before = {
        name: (planning / name).read_text(encoding="utf-8") for name in RENDER_NAMES
    }
    if not rewrites:
        run = PlanRunRepo(database).get(RENDER_PLAN_RUN_ID)
        if run is None or run.status is not PlanRunStatus.APPLIED:
            raise RepairRefusedError(
                "anchors are repaired but the sanctioned render PlanRun is not applied"
            )
        after = {
            name: (planning / name).read_text(encoding="utf-8") for name in RENDER_NAMES
        }
        if before != after:
            raise RepairRefusedError("idempotency check observed render drift")
        return RepairResult((), tuple(already), None, run.id)

    assert_store_matches_renders(repo_root, database)
    backup_path = create_backup(database, now=now, backup_dir=backup_dir)
    apply_source_anchor_rewrites(database, rewrites)
    run = _apply_render_plan_run(repo_root, database, now=now)
    after = {
        name: (planning / name).read_text(encoding="utf-8") for name in RENDER_NAMES
    }
    assert_bounded_render_delta(before, after, targets)
    return RepairResult(
        tuple(rewrite.identity for rewrite in rewrites), (), backup_path, run.id
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--db", default=None)
    args = parser.parse_args()
    try:
        result = repair_store_anchors(
            args.repo.resolve(), Database(args.db), now=datetime.now(UTC)
        )
    except RepairRefusedError as error:
        print(f"REFUSED: {error}")
        return EXIT_REFUSED
    if result.updated:
        print(f"Backup: {result.backup_path}")
        print(f"Updated source_anchor: {', '.join(result.updated)}")
        print(f"Applied zero-model PlanRun: {result.plan_run_id}")
    else:
        print("ATLAS-031M already applied; no changes.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

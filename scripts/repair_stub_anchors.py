"""One-time repair of dangling stub fields (ATLAS-159 / ATLAS-165, branch (a)).

Stub-minted tickets were anchored to their stub's ACTIVE-inbox path, and
`atlas apply` retires that stub to `inbox/processed/` — so every such anchor
dangled the moment its own apply completed, and gate 4 refused every later
stubs-only echo. Ten-plus of the affected tickets are frozen (spec §4), so
planning cannot repair them and no other sanctioned anchor writer exists.
The operator ruled branch (a) at the ATLAS-159 plan gate (PR #172): a scoped,
one-time repair OUTSIDE planning, documented in the debt register per the
ATLAS-007M bootstrap-exception precedent. This script repairs exactly the
named set below and refuses anything else — it is NOT general
anchor-migration tooling.

Fail-closed: every named ticket is verified BEFORE any write — the stored
anchor must be an active-inbox anchor (or already repaired, which is a
skip), the old path must be retired (absent from the active inbox at HEAD),
and the rewritten anchor must resolve against the committed processed/ set.
Any verification failure refuses the whole run with nothing written. The
rewrite is one transaction: `source_anchor` and `updated_at` only (the
ratified audit trail), and it never touches `docs/planning/` (ADR-0007 —
the renders self-correct at the next apply).

ATLAS-165 adds the same scoped exception for `relevant_docs`: the ATLAS-159
repair rewrote `source_anchor` only, leaving stored references at retired
ACTIVE-inbox spellings whose files now live under `processed/`. The
`relevant-docs` mode is likewise named-set-scoped, fail-closed, idempotent,
prints each row rewrite, and updates only `relevant_docs` + `updated_at`.

Usage:
    uv run python scripts/repair_stub_anchors.py --db <url> [--repo PATH] [--yes]
    uv run python scripts/repair_stub_anchors.py --db <url>
        --repair relevant-docs [--repo PATH] [--yes]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import sqlalchemy as sa

from atlas.core.anchors import AnchorIndex, IngestionError
from atlas.planning.ingestion import (
    collect_inbox_documents,
    collect_processed_documents,
    processed_path_for,
)
from atlas.planning.pipeline import DEFAULT_INBOX_DIR
from atlas.storage import Database, TicketRepo
from atlas.storage.tables import TicketRow

EXIT_OK = 0
EXIT_REFUSED = 2

# The one named defect set (ATLAS-159): every live ticket whose source_anchor
# was minted against an active-inbox path later retired by its own apply —
# ATLAS-109/110/147-158 from the rendered ticket, plus ATLAS-159/160, whose
# stubs were retired by the apply that minted them (PR #171; premise delta
# ratified at the gate). The script refuses any key outside this tuple.
REPAIR_KEYS = (
    "ATLAS-109",
    "ATLAS-110",
    "ATLAS-147",
    "ATLAS-148",
    "ATLAS-149",
    "ATLAS-150",
    "ATLAS-151",
    "ATLAS-152",
    "ATLAS-153",
    "ATLAS-154",
    "ATLAS-155",
    "ATLAS-156",
    "ATLAS-157",
    "ATLAS-158",
    "ATLAS-159",
    "ATLAS-160",
)

# The one named relevant_docs defect set (ATLAS-165), enumerated live from the
# rendered store at the plan gate: every stored relevant_docs entry whose active
# inbox path is retired at HEAD and whose durable processed/ path exists.
# The script refuses any additional row/path with that shape.
RELEVANT_DOC_REPAIR_PATHS = (
    ("ATLAS-98", "docs/planning/inbox/smoke-b-fixture.md"),
    ("ATLAS-109", "docs/planning/inbox/smoke-b-fixture.md"),
    ("ATLAS-110", "docs/planning/inbox/smoke-b-fixture-v2.md"),
    ("ATLAS-147", "docs/planning/inbox/inbox-stub-op8-linear-client-hardening.md"),
    ("ATLAS-148", "docs/planning/inbox/inbox-stub-op9-sync-request-budget.md"),
    ("ATLAS-149", "docs/planning/inbox/inbox-stub-op8-linear-client-hardening.md"),
    ("ATLAS-150", "docs/planning/inbox/inbox-stub-op9-sync-request-budget.md"),
    ("ATLAS-151", "docs/planning/inbox/inbox-stub-f4-promotion-dedup.md"),
    ("ATLAS-152", "docs/planning/inbox/inbox-stub-retire-on-reject-scope.md"),
    ("ATLAS-153", "docs/planning/inbox/inbox-stub-stubs-only-plan-mode.md"),
    ("ATLAS-154", "docs/planning/inbox/inbox-stub-accepted-types-spelling.md"),
    ("ATLAS-155", "docs/planning/inbox/inbox-stub-accepted-types-spelling.md"),
    ("ATLAS-156", "docs/planning/inbox/inbox-stub-f4-promotion-dedup.md"),
    ("ATLAS-157", "docs/planning/inbox/inbox-stub-retire-on-reject-scope.md"),
    ("ATLAS-158", "docs/planning/inbox/inbox-stub-stubs-only-plan-mode.md"),
    ("ATLAS-159", "docs/planning/inbox/inbox-stub-durable-stub-anchors.md"),
    ("ATLAS-160", "docs/planning/inbox/inbox-stub-meta-label-discipline.md"),
    ("ATLAS-161", "docs/planning/inbox/inbox-stub-collapse-anchor-normalization.md"),
    ("ATLAS-162", "docs/planning/inbox/inbox-stub-pack-processed-anchors.md"),
    ("ATLAS-163", "docs/planning/inbox/inbox-stub-retirement-collision.md"),
    ("ATLAS-164", "docs/planning/inbox/inbox-stub-pack-embedding.md"),
    ("ATLAS-165", "docs/planning/inbox/inbox-stub-relevant-docs-repair.md"),
)

RELEVANT_DOC_REPAIR_KEYS = tuple(key for key, _path in RELEVANT_DOC_REPAIR_PATHS)


class RepairRefusedError(RuntimeError):
    """A named ticket failed verification; the whole run refuses, unwritten."""


@dataclass(frozen=True)
class PlannedRewrite:
    """One verified anchor rewrite: old active-inbox spelling -> durable."""

    key: str
    old_anchor: str
    new_anchor: str


@dataclass(frozen=True)
class RetiredRelevantDoc:
    """One stored relevant_docs entry whose active path is retired at HEAD."""

    key: str
    old_path: str
    new_path: str


@dataclass(frozen=True)
class PlannedRelevantDocsRewrite:
    """One verified relevant_docs row rewrite."""

    key: str
    old_relevant_docs: tuple[str, ...]
    new_relevant_docs: tuple[str, ...]
    rewrites: tuple[tuple[str, str], ...]


def _relevant_doc_expectations() -> dict[str, tuple[str, ...]]:
    expected: dict[str, list[str]] = {}
    for key, old_path in RELEVANT_DOC_REPAIR_PATHS:
        expected.setdefault(key, []).append(old_path)
    return {key: tuple(paths) for key, paths in expected.items()}


def plan_repair(
    database: Database,
    repo_root: Path,
    inbox_dir: Path = DEFAULT_INBOX_DIR,
) -> tuple[list[PlannedRewrite], list[str]]:
    """Verify every named ticket; return (rewrites, already_repaired_keys).

    Fail-closed: raises :class:`RepairRefusedError` on the FIRST ticket that
    cannot be verified, before anything is written. Idempotent by
    construction: a ticket whose anchor already cites its resolvable durable
    path is reported as already repaired, never rewritten twice.
    """
    index = AnchorIndex.build(collect_processed_documents(repo_root, inbox_dir))
    active_paths = {
        document.path for document in collect_inbox_documents(repo_root, inbox_dir)
    }
    tickets = {ticket.key: ticket for ticket in TicketRepo(database).list()}
    inbox = PurePosixPath(inbox_dir.as_posix())

    rewrites: list[PlannedRewrite] = []
    already: list[str] = []
    for key in REPAIR_KEYS:
        ticket = tickets.get(key)
        if ticket is None:
            raise RepairRefusedError(
                f"{key} is not in the store; the repair set no longer matches "
                "the backlog — refusing the whole run"
            )
        anchor = ticket.source_anchor
        path, separator, _slug = anchor.partition("#")
        if separator != "#":
            raise RepairRefusedError(
                f"{key} anchor {anchor!r} is not of the form <path>#<slug>"
            )
        parent = PurePosixPath(path).parent
        if parent == inbox / "processed":
            try:
                index.resolve(anchor)
            except IngestionError as error:
                raise RepairRefusedError(
                    f"{key} anchor {anchor!r} cites processed/ but does not "
                    f"resolve at HEAD: {error}"
                ) from error
            already.append(key)
            continue
        if parent != inbox:
            raise RepairRefusedError(
                f"{key} anchor {anchor!r} is not an active-inbox anchor; this "
                "script repairs only the named inbox-path defect"
            )
        if path in active_paths:
            raise RepairRefusedError(
                f"{key} anchor {anchor!r} cites a stub still in the active "
                "inbox — it is not dangling; investigate before repairing"
            )
        new_anchor = anchor.replace(path, processed_path_for(path), 1)
        try:
            index.resolve(new_anchor)
        except IngestionError as error:
            raise RepairRefusedError(
                f"{key}: rewritten anchor {new_anchor!r} does not resolve "
                f"against the committed processed/ set: {error}"
            ) from error
        rewrites.append(
            PlannedRewrite(key=key, old_anchor=anchor, new_anchor=new_anchor)
        )
    return rewrites, already


def apply_repair(
    database: Database, rewrites: list[PlannedRewrite], *, now: datetime
) -> None:
    """Apply the verified rewrites in one transaction: ``source_anchor`` and
    ``updated_at`` only, no other field, no file writes."""
    with database.session() as session, session.begin():
        for rewrite in rewrites:
            row = session.scalars(
                sa.select(TicketRow).where(TicketRow.key == rewrite.key)
            ).one()
            row.source_anchor = rewrite.new_anchor
            row.updated_at = now


def scan_retired_relevant_docs(
    database: Database,
    repo_root: Path,
    inbox_dir: Path = DEFAULT_INBOX_DIR,
) -> list[RetiredRelevantDoc]:
    """Find stored relevant_docs entries at retired active-inbox spellings.

    A defect is specifically: ``docs/planning/inbox/<name>.md`` is absent from
    the active inbox at HEAD, while its ``processed/<name>.md`` counterpart is
    present. A genuinely absent document (neither spelling present) is not this
    repair's job; the renderer keeps its existing soft-skip posture.
    """
    inbox = PurePosixPath(inbox_dir.as_posix())
    active_paths = {
        document.path for document in collect_inbox_documents(repo_root, inbox_dir)
    }
    processed_paths = {
        document.path for document in collect_processed_documents(repo_root, inbox_dir)
    }
    defects: list[RetiredRelevantDoc] = []
    for ticket in TicketRepo(database).list():
        for path in ticket.relevant_docs:
            candidate = PurePosixPath(path)
            if candidate.parent != inbox:
                continue
            new_path = processed_path_for(path)
            if path not in active_paths and new_path in processed_paths:
                defects.append(
                    RetiredRelevantDoc(key=ticket.key, old_path=path, new_path=new_path)
                )
    return defects


def plan_relevant_docs_repair(
    database: Database,
    repo_root: Path,
    inbox_dir: Path = DEFAULT_INBOX_DIR,
) -> tuple[list[PlannedRelevantDocsRewrite], list[str]]:
    """Verify and plan the ATLAS-165 relevant_docs repair.

    Fail-closed in two directions: every live retired-active spelling must be
    in the named set, and every named row must still contain either the old
    spelling or the already-repaired processed/ spelling. No writes happen here.
    """
    expected_by_key = _relevant_doc_expectations()
    expected_pairs = {
        (key, old_path) for key, paths in expected_by_key.items() for old_path in paths
    }
    for defect in scan_retired_relevant_docs(database, repo_root, inbox_dir):
        if (defect.key, defect.old_path) not in expected_pairs:
            raise RepairRefusedError(
                f"{defect.key} relevant_docs entry {defect.old_path!r} also "
                "points at a retired inbox path, but is outside the named "
                "ATLAS-165 repair set — refusing the whole run"
            )

    active_paths = {
        document.path for document in collect_inbox_documents(repo_root, inbox_dir)
    }
    processed_paths = {
        document.path for document in collect_processed_documents(repo_root, inbox_dir)
    }
    tickets = {ticket.key: ticket for ticket in TicketRepo(database).list()}

    rewrites: list[PlannedRelevantDocsRewrite] = []
    already: list[str] = []
    for key, old_paths in expected_by_key.items():
        ticket = tickets.get(key)
        if ticket is None:
            raise RepairRefusedError(
                f"{key} is not in the store; the relevant_docs repair set no "
                "longer matches the backlog — refusing the whole run"
            )
        refs = list(ticket.relevant_docs)
        old_refs = tuple(refs)
        row_rewrites: list[tuple[str, str]] = []
        for old_path in old_paths:
            new_path = processed_path_for(old_path)
            if old_path in active_paths:
                raise RepairRefusedError(
                    f"{key} relevant_docs entry {old_path!r} still exists in "
                    "the active inbox — it is not retired; investigate before "
                    "repairing"
                )
            if new_path not in processed_paths:
                raise RepairRefusedError(
                    f"{key}: rewritten relevant_docs path {new_path!r} is not "
                    "present in the committed processed/ set"
                )
            has_old = old_path in refs
            has_new = new_path in refs
            if has_old and has_new:
                raise RepairRefusedError(
                    f"{key} relevant_docs contains both {old_path!r} and "
                    f"{new_path!r}; refusing an ambiguous partial repair"
                )
            if has_old:
                refs = [new_path if path == old_path else path for path in refs]
                row_rewrites.append((old_path, new_path))
                continue
            if has_new:
                continue
            raise RepairRefusedError(
                f"{key} relevant_docs contains neither {old_path!r} nor "
                f"{new_path!r}; the named repair set no longer matches the row"
            )
        if row_rewrites:
            rewrites.append(
                PlannedRelevantDocsRewrite(
                    key=key,
                    old_relevant_docs=old_refs,
                    new_relevant_docs=tuple(refs),
                    rewrites=tuple(row_rewrites),
                )
            )
        else:
            already.append(key)
    return rewrites, already


def apply_relevant_docs_repair(
    database: Database,
    rewrites: list[PlannedRelevantDocsRewrite],
    *,
    now: datetime,
) -> None:
    """Apply verified relevant_docs rewrites in one transaction."""
    with database.session() as session, session.begin():
        for rewrite in rewrites:
            row = session.scalars(
                sa.select(TicketRow).where(TicketRow.key == rewrite.key)
            ).one()
            row.relevant_docs = list(rewrite.new_relevant_docs)
            row.updated_at = now


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="database URL")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="repository root")
    parser.add_argument(
        "--repair",
        choices=("source-anchor", "relevant-docs"),
        default="source-anchor",
        help="which named one-time repair to run",
    )
    parser.add_argument("--yes", action="store_true", help="apply without prompting")
    args = parser.parse_args(argv)

    database = Database(args.db)
    if args.repair == "source-anchor":
        try:
            rewrites, already = plan_repair(database, args.repo)
        except (RepairRefusedError, IngestionError) as error:
            print(f"refused: {error}", file=sys.stderr)
            return EXIT_REFUSED

        for key in already:
            print(f"{key}: already repaired (durable anchor resolves) — skip")
        for rewrite in rewrites:
            print(f"{rewrite.key}: {rewrite.old_anchor} -> {rewrite.new_anchor}")
        if not rewrites:
            print("nothing to repair")
            return EXIT_OK

        if not args.yes:
            answer = input(f"Rewrite {len(rewrites)} anchor(s)? [y/N] ")
            if answer.strip().lower() != "y":
                print("refused: not confirmed", file=sys.stderr)
                return EXIT_REFUSED

        apply_repair(database, rewrites, now=datetime.now(UTC))
        print(f"repaired {len(rewrites)} anchor(s)")
        return EXIT_OK

    try:
        doc_rewrites, already = plan_relevant_docs_repair(database, args.repo)
    except (RepairRefusedError, IngestionError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return EXIT_REFUSED

    for key in already:
        print(f"{key}: already repaired (durable relevant_docs path present) — skip")
    for rewrite in doc_rewrites:
        changes = ", ".join(f"{old} -> {new}" for old, new in rewrite.rewrites)
        print(f"{rewrite.key}: relevant_docs {changes}")
    if not doc_rewrites:
        print("nothing to repair")
        return EXIT_OK

    if not args.yes:
        answer = input(
            f"Rewrite {len(doc_rewrites)} ticket relevant_docs row(s)? [y/N] "
        )
        if answer.strip().lower() != "y":
            print("refused: not confirmed", file=sys.stderr)
            return EXIT_REFUSED

    apply_relevant_docs_repair(database, doc_rewrites, now=datetime.now(UTC))
    print(f"repaired {len(doc_rewrites)} ticket relevant_docs row(s)")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

"""One-time repair of the sixteen dangling stub anchors (ATLAS-159, branch (a)).

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

Usage:
    uv run python scripts/repair_stub_anchors.py --db <url> [--repo PATH] [--yes]
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


class RepairRefusedError(RuntimeError):
    """A named ticket failed verification; the whole run refuses, unwritten."""


@dataclass(frozen=True)
class PlannedRewrite:
    """One verified anchor rewrite: old active-inbox spelling -> durable."""

    key: str
    old_anchor: str
    new_anchor: str


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="database URL")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="repository root")
    parser.add_argument("--yes", action="store_true", help="apply without prompting")
    args = parser.parse_args(argv)

    database = Database(args.db)
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


if __name__ == "__main__":
    sys.exit(main())

"""Seed a throwaway SQLite DB with a small dependency graph so the `atlas deps`
CLI can be exercised by hand — ZERO API, no `atlas plan` needed.

Usage:
    uv run python scratch_seed.py                 # clean, valid graph
    uv run python scratch_seed.py --break dangling # inject a dangling target
    uv run python scratch_seed.py --break cycle    # inject a 2-cycle

Then drive the real binary against it:
    uv run atlas deps validate      --db sqlite:///./scratch.db
    uv run atlas deps ready         --db sqlite:///./scratch.db
    uv run atlas deps blocked       --db sqlite:///./scratch.db
    uv run atlas deps blocked --high-risk --db sqlite:///./scratch.db
    uv run atlas deps critical-path --db sqlite:///./scratch.db
    uv run atlas deps unlocks ATLAS-2 --db sqlite:///./scratch.db
    uv run atlas deps effort ATLAS-4 10 --db sqlite:///./scratch.db  # then re-run cp
    uv run atlas deps graph         --db sqlite:///./scratch.db      # paste to viewer

The clean graph (all under epic ATLAS-E1):
    ATLAS-1 done                          (a completed dependency)
    ATLAS-2 planned eff=3 depends_on 1    -> READY (dep done)
    ATLAS-3 planned eff=5 depends_on 2    -> BLOCKED by 2; critical chain [2,3] total 8
    ATLAS-4 in_progress risk=high         (a high-risk blocker)
    ATLAS-5 planned eff=2 depends_on 4    -> BLOCKED by 4
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from atlas.core.models import Epic, Ticket, TicketDependency
from atlas.storage import Database, EpicRepo, TicketDependencyRepo, TicketRepo

NOW = datetime.now(UTC)
PRODUCT_ID = uuid4()


def epic(key: str, title: str) -> Epic:
    return Epic(
        id=uuid4(),
        product_id=PRODUCT_ID,
        key=key,
        title=title,
        description="Scratch epic for the deps CLI smoke.",
        objective="Exercise readiness/blockers/critical-path/graph.",
        status="planned",
        priority=1,
        risk_level="medium",
        source_anchor="docs/atlas/dependency-engine.md#graph-projection-build",
        created_by_type="human",
        created_by_id="operator",
        created_at=NOW,
        updated_at=NOW,
    )


def ticket(
    key: str,
    *,
    epic_id: UUID,
    status: str = "planned",
    risk_level: str = "low",
    effort: int | None = None,
    criteria: int = 1,
) -> Ticket:
    return Ticket(
        id=uuid4(),
        product_id=PRODUCT_ID,
        epic_id=epic_id,
        key=key,
        title=f"Scratch {key}",
        objective=f"Objective for {key}.",
        context="Scratch context.",
        status=status,
        ticket_type="feature",
        risk_level=risk_level,
        priority=10,
        estimated_effort=effort,
        acceptance_criteria=[f"criterion {n}" for n in range(criteria)],
        source_anchor="docs/atlas/dependency-engine.md#readiness-predicate",
        created_by_type="agent",
        created_by_id="claude",
        created_at=NOW,
        updated_at=NOW,
    )


def depends_on(source: Ticket, target_id: UUID) -> TicketDependency:
    return TicketDependency(
        id=uuid4(),
        source_ticket_id=source.id,
        target_entity_type="ticket",
        target_entity_id=target_id,
        dependency_type="depends_on",
        reason=f"{source.key} depends on its target.",
        created_by_type="human",
        created_by_id="operator",
        created_at=NOW,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed a scratch deps graph.")
    parser.add_argument(
        "--break", dest="brk", choices=["dangling", "cycle"], default=None
    )
    parser.add_argument("--db", default="sqlite:///./scratch.db")
    args = parser.parse_args()

    # Fresh file each run.
    path = args.db.removeprefix("sqlite:///")
    Path(path).unlink(missing_ok=True)

    db = Database(args.db)
    db.create_all()

    eng = epic("ATLAS-E1", "Dependency Engine")
    t1 = ticket("ATLAS-1", epic_id=eng.id, status="done")
    t2 = ticket("ATLAS-2", epic_id=eng.id, effort=3)
    t3 = ticket("ATLAS-3", epic_id=eng.id, effort=5)
    t4 = ticket("ATLAS-4", epic_id=eng.id, status="in_progress", risk_level="high")
    t5 = ticket("ATLAS-5", epic_id=eng.id, effort=2)

    deps = [
        depends_on(t2, t1.id),  # ATLAS-2 -> ATLAS-1 (done)  => ATLAS-2 ready
        depends_on(t3, t2.id),  # ATLAS-3 -> ATLAS-2         => ATLAS-3 blocked
        depends_on(t5, t4.id),  # ATLAS-5 -> ATLAS-4 (high)  => ATLAS-5 blocked
    ]
    if args.brk == "dangling":
        deps.append(depends_on(t3, uuid4()))  # target points at no stored ticket
    elif args.brk == "cycle":
        deps.append(depends_on(t2, t3.id))  # ATLAS-2 -> ATLAS-3 closes a 2-cycle

    EpicRepo(db).add(eng)
    for t in (t1, t2, t3, t4, t5):
        TicketRepo(db).add(t)
    for d in deps:
        TicketDependencyRepo(db).add(d)

    label = args.brk or "clean"
    print(f"Seeded {path} ({label}): 1 epic, 5 tickets, {len(deps)} dependencies.")


if __name__ == "__main__":
    main()

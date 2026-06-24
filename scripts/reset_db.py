"""Wipe the local Atlas DB and leave it plan-ready in one guarded command.

`atlas plan` (especially `--staged`, which is first-run-only and refuses a
non-empty backlog) needs a clean DB with the ATLAS product seeded. This empties
every model table (schema and migration head preserved — it does NOT migrate or
drop tables) and re-seeds the canonical product, subsuming the manual bootstrap
snippet from the runbook. ZERO API, no `atlas plan` needed.

Usage:
    uv run python scripts/reset_db.py                 # prompt, then wipe + seed
    uv run python scripts/reset_db.py --yes           # non-interactive
    uv run python scripts/reset_db.py --no-seed-product  # wipe only, no reseed
    uv run python scripts/reset_db.py --db sqlite:///./scratch.db --yes

The DB must already be provisioned (`uv run alembic upgrade head`); a reset
targets an already-migrated DB and refuses an unprovisioned one rather than
migrate it.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa

from atlas.core.models import Product
from atlas.storage import Database, ProductRepo, clear_all_data
from atlas.storage.tables import Base

EXIT_OK = 0
EXIT_REFUSED = 2

# The canonical product the planner attributes a PlanRun to. ATLAS-specific
# identity lives here (dev tooling), not in atlas.storage — it mirrors the
# manual bootstrap snippet from docs/runbooks/running-atlas-plan.md.
PRODUCT_KEY = "ATLAS"


def _seed_product(db: Database) -> None:
    now = datetime.now(UTC)
    ProductRepo(db).add(
        Product(
            id=uuid4(),
            key=PRODUCT_KEY,
            name="Atlas",
            description="Stateful organisational operating system.",
            vision="Repeatable software delivery through a self-improving harness.",
            status="active",
            created_by_type="human",
            created_by_id="operator",
            created_at=now,
            updated_at=now,
        )
    )


def _is_provisioned(db: Database) -> bool:
    """True when the model tables exist. A reset targets a migrated DB; it never
    provisions schema (that is `alembic upgrade head`)."""
    present = set(sa.inspect(db.engine).get_table_names())
    expected = {table.name for table in Base.metadata.sorted_tables}
    return expected.issubset(present)


def _confirmed(url: str, *, assume_yes: bool) -> bool:
    """Consent policy, mirroring `atlas apply`: --yes pre-confirms; otherwise an
    interactive y/N prompt; with neither a TTY nor --yes, refuse rather than
    assume consent."""
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print(
            "reset needs confirmation: re-run with --yes (no TTY available).",
            file=sys.stderr,
        )
        return False
    return input(f"Wipe {url}? [y/N] ").strip().lower() == "y"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Wipe the local Atlas DB and leave it plan-ready."
    )
    parser.add_argument(
        "--db",
        default=None,
        help="database URL (overrides ATLAS_DATABASE_URL; default .atlas/atlas.db)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="pre-confirm the wipe (non-interactive)",
    )
    parser.add_argument(
        "--no-seed-product",
        action="store_true",
        help="empty the DB without re-seeding the ATLAS product",
    )
    args = parser.parse_args()

    db = Database(args.db)
    url = str(db.engine.url)
    print(f"Target: {url}")

    if not _is_provisioned(db):
        print(
            "DB not provisioned — run `uv run alembic upgrade head` first.",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    if not _confirmed(url, assume_yes=args.yes):
        print("Aborted; nothing was changed.", file=sys.stderr)
        return EXIT_REFUSED

    counts = clear_all_data(db)
    total = sum(counts.values())
    print(f"Cleared {total} row(s) across {len(counts)} table(s):")
    for table, rows in counts.items():
        print(f"  {table}: {rows}")

    if args.no_seed_product:
        print("Product reseed skipped (--no-seed-product).")
    else:
        _seed_product(db)
        print(f"Seeded product {PRODUCT_KEY!r}.")

    print("Reset complete — ready for `atlas plan`.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

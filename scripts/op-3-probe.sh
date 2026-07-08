#!/usr/bin/env bash
# OP-3 probe — READ-ONLY. Establishes the store facts before any correction.
# Run from the atlas repo root on the operator machine. Writes nothing.
# Origin: OP-3 (2026-07-07); reusable read-only store scan; the 111..146 range check is OP-3-specific and documents the burn invariant.
set -euo pipefail

uv sync --locked --quiet
ATLAS_LIVE_TESTS=0 uv run python - <<'PY'
import re
from atlas.storage.db import Database, resolve_url
from atlas.storage.repositories import KeyCounterRepo, TicketRepo

db = Database()
print(f"db url            : {resolve_url(None)}")

marks = KeyCounterRepo(db).high_water_marks()
print(f"counter marks     : {marks}")

tickets = TicketRepo(db).list()
nums = sorted(int(t.key.split("-")[-1]) for t in tickets if re.fullmatch(r"ATLAS-\d+", t.key))
print(f"ticket rows       : {len(tickets)}")
print(f"max store key     : ATLAS-{max(nums)}" if nums else "max store key     : (none)")

burned_range = [n for n in nums if 111 <= n <= 146]
print(f"store keys in 111..146 (expected EMPTY): {burned_range or 'none'}")

from collections import Counter
dist = Counter(t.status.value for t in tickets)
print("status distribution:")
for status, count in sorted(dist.items(), key=lambda kv: -kv[1]):
    print(f"  {status:>22}: {count}")
PY

echo
echo "Manual glance (no API): open the Linear team and note the highest"
echo "Linear-NATIVE issue number (the ATL-<n> Linear itself assigned)."
echo "It is informational only — the embedded-title key (ATLAS-143) is"
echo "authoritative — but the OP-3a ruling should be made knowing it."

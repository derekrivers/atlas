#!/usr/bin/env bash
# Smoke B — diff inspector (read-only; NO planner call, NO writes).
#
# Reconciles the latest PROPOSED PlanRun against the current backlog and
# reports the diff's shape — the same reconcile() apply runs, so what you
# see here is exactly what apply refused. Purpose: decide whether the 109
# MODIFYs are SPURIOUS (regenerated-but-equivalent prose — an ATLAS-145
# seed-fidelity ticket) or REAL (the planner changed bodies — a second
# finding: instability under restatement).
#
# Usage:
#   ./smoke-b-diff-inspect.sh                 # counts + per-field MODIFY summary
#   ./smoke-b-diff-inspect.sh --show KEY      # full old/new for one ticket key
#   ./smoke-b-diff-inspect.sh --sample N      # full old/new for N sample MODIFYs
#   ./smoke-b-diff-inspect.sh --db URL

set -u
SHOW=""; SAMPLE=0; DB_URL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --show) shift; SHOW="${1:?--show needs a ticket key}" ;;
    --sample) shift; SAMPLE="${1:?--sample needs a count}" ;;
    --db) shift; DB_URL="${1:?--db needs a URL}" ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac; shift
done
die() { echo "ABORT: $*" >&2; exit 2; }
[ -f pyproject.toml ] && grep -q '^name = "atlas"' pyproject.toml || die "run from the atlas repo root"
command -v uv >/dev/null 2>&1 || die "uv not on PATH"
export SMOKE_DB_URL="$DB_URL" SMOKE_SHOW="$SHOW" SMOKE_SAMPLE="$SAMPLE"

uv run python - << 'EOF'
import os, sys, textwrap
from atlas.storage.db import Database
from atlas.storage.repositories import (
    PlanRunRepo, EpicRepo, TicketRepo, TicketDependencyRepo,
)
from atlas.planning.proposal import Proposal
from atlas.planning.reconciler import reconcile, Backlog

db = Database(os.environ.get("SMOKE_DB_URL") or None)
plan_run = PlanRunRepo(db).latest_proposed()
if plan_run is None:
    print("No PROPOSED PlanRun in the store. Run `atlas plan --staged` first "
          "(or ./scripts/phase_1.sh) — this inspector reads that run, it does "
          "not create one.")
    sys.exit(1)

backlog = Backlog(
    epics=EpicRepo(db).list(),
    tickets=TicketRepo(db).list(),
    dependencies=TicketDependencyRepo(db).list(),
)
proposal = Proposal.model_validate(plan_run.proposal)
diff = reconcile(proposal, backlog, similarity_threshold=plan_run.similarity_threshold)

# --- counts by type -----------------------------------------------------------
from collections import Counter
by_type = Counter(e.entry_type for e in diff.entries)
print(f"PlanRun {plan_run.id} (proposed {plan_run.created_at.isoformat(timespec='seconds')})")
print(f"Backlog: {len(backlog.tickets)} tickets, {len(backlog.epics)} epics")
print("Diff entry counts:")
for t in ("ADD", "MODIFY", "PROPOSE_ARCHIVE", "CONFLICT"):
    print(f"  {t:16s} {by_type.get(t, 0)}")
print()

archives = [e for e in diff.entries if e.entry_type == "PROPOSE_ARCHIVE"]
if archives:
    print("!! PROPOSE_ARCHIVE entries present — restated items would be LOST.")
    print("!! This is the ATLAS-144 guard's failure shape; do NOT apply. Keys:")
    for e in archives:
        print(f"     {e.kind} {e.identity}: {e.title}")
    print()

adds = [e for e in diff.entries if e.entry_type == "ADD"]
print(f"ADD entries ({len(adds)}) — expect your fixture among these:")
for e in adds:
    print(f"  {e.kind} {e.identity}: {e.title}")
print()

mods = [e for e in diff.entries if e.entry_type == "MODIFY"]

# --- --show KEY: full old/new for one ticket ----------------------------------
show = os.environ.get("SMOKE_SHOW") or ""
if show:
    hit = next((e for e in mods if e.identity == show), None)
    if hit is None:
        print(f"No MODIFY entry for {show!r}. MODIFY identities: "
              + ", ".join(sorted(e.identity for e in mods)[:20]) + " ...")
        sys.exit(0)
    print(f"=== MODIFY {hit.identity}: {hit.title} ===")
    for field_name, (old, new) in sorted(hit.changes.items()):
        print(f"\n--- {field_name} ---")
        print("OLD:", textwrap.shorten(repr(old), 2000))
        print("NEW:", textwrap.shorten(repr(new), 2000))
    sys.exit(0)

# --- MODIFY field-frequency summary (the spurious-vs-real signal) -------------
if not mods:
    print("No MODIFY entries — the diff is clean. If the only ADD is your "
          "fixture, apply would proceed.")
    sys.exit(0)

field_freq = Counter()
whitespace_only = 0
for e in mods:
    for field_name, (old, new) in e.changes.items():
        field_freq[field_name] += 1
    # a cheap spurious signal: every changed field differs only by
    # whitespace/newline normalisation
    if e.changes and all(
        str(old).split() == str(new).split() for old, new in e.changes.values()
    ):
        whitespace_only += 1

print(f"MODIFY entries: {len(mods)}")
print(f"  of which whitespace/normalisation-only: {whitespace_only} "
      f"({100*whitespace_only//max(len(mods),1)}%)")
print("Changed-field frequency (which fields drift, across all MODIFYs):")
for field_name, n in field_freq.most_common():
    print(f"  {field_name:22s} {n}")
print()
print("Read: drift concentrated in prose fields (objective/context/"
      "acceptance_criteria/implementation_notes) with high whitespace-only "
      "share => SPURIOUS (seed-fidelity: ATLAS-145). Drift in categorical "
      "fields (priority/risk_level/ticket_type), or substantive prose "
      "rewrites => REAL (planner instability under restatement: a second "
      "finding).")

# --- --sample N: full old/new for a few -------------------------------------
n = int(os.environ.get("SMOKE_SAMPLE") or 0)
if n > 0:
    print(f"\n=== {min(n, len(mods))} sample MODIFY entries (full) ===")
    for e in mods[:n]:
        print(f"\n### {e.identity}: {e.title}")
        for field_name, (old, new) in sorted(e.changes.items()):
            print(f"  --- {field_name} ---")
            print("  OLD:", textwrap.shorten(repr(old), 800))
            print("  NEW:", textwrap.shorten(repr(new), 800))
EOF
rc=$?
[ $rc -eq 1 ] && exit 1
[ $rc -ne 0 ] && die "inspector errored — see traceback above"
echo
echo "Next: --show KEY for one ticket's full old/new, or --sample 3 for a few."
echo "Then rule ATLAS-145 (seed fidelity) — I'll draft the runbook once you"
echo "report the shape."
#!/usr/bin/env bash
# Smoke B — diff inspector (read-only; NO planner call, NO writes).
#
# Reconciles the latest PROPOSED PlanRun against the current backlog and
# writes a full report to a file (printed too), so nothing is ever swallowed.
# This is the same reconcile() apply runs — what you see is what apply refused.
#
# Usage:
#   ./smoke-b-diff-inspect.sh [--db URL] [--sample N] [--show KEY] [--out FILE]

set -u
DB_URL=""; SAMPLE=0; SHOW=""; OUT="smoke-b-diff-report.txt"
while [ $# -gt 0 ]; do
  case "$1" in
    --db) shift; DB_URL="${1:?--db needs a URL}" ;;
    --sample) shift; SAMPLE="${1:?--sample needs a count}" ;;
    --show) shift; SHOW="${1:?--show needs a ticket key}" ;;
    --out) shift; OUT="${1:?--out needs a path}" ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac; shift
done
[ -f pyproject.toml ] && grep -q '^name = "atlas"' pyproject.toml \
  || { echo "ABORT: run from the atlas repo root" >&2; exit 2; }
command -v uv >/dev/null 2>&1 || { echo "ABORT: uv not on PATH" >&2; exit 2; }
export SMOKE_DB_URL="$DB_URL" SMOKE_SAMPLE="$SAMPLE" SMOKE_SHOW="$SHOW" SMOKE_OUT="$OUT"

uv run python - << 'EOF'
import os
from collections import Counter
from atlas.storage.db import Database
from atlas.storage.repositories import (
    PlanRunRepo, EpicRepo, TicketRepo, TicketDependencyRepo,
)
from atlas.planning.proposal import Proposal
from atlas.planning.reconciler import reconcile, Backlog

out_lines = []
def emit(s=""):
    print(s)
    out_lines.append(s)

db = Database(os.environ.get("SMOKE_DB_URL") or None)
plan_run = PlanRunRepo(db).latest_proposed()
if plan_run is None:
    emit("No PROPOSED PlanRun. Run the planrun-doctor to see what exists.")
else:
    backlog = Backlog(
        epics=EpicRepo(db).list(),
        tickets=TicketRepo(db).list(),
        dependencies=TicketDependencyRepo(db).list(),
    )
    proposal = Proposal.model_validate(plan_run.proposal)
    diff = reconcile(proposal, backlog,
                     similarity_threshold=plan_run.similarity_threshold)
    entries = list(diff.entries)

    by_type = Counter(e.entry_type for e in entries)
    emit(f"PlanRun {plan_run.id} (proposed "
         f"{plan_run.created_at.isoformat(timespec='seconds')})")
    emit(f"Backlog: {len(backlog.tickets)} tickets, {len(backlog.epics)} epics")
    emit(f"Total diff entries: {len(entries)}")
    emit("Counts by type:")
    for t in ("ADD", "MODIFY", "PROPOSE_ARCHIVE", "CONFLICT"):
        emit(f"  {t:16s} {by_type.get(t, 0)}")
    emit("")

    archives = [e for e in entries if e.entry_type == "PROPOSE_ARCHIVE"]
    if archives:
        emit("!! PROPOSE_ARCHIVE present — restated items would be LOST. "
             "Do NOT apply. Keys:")
        for e in archives:
            emit(f"     {e.kind} {e.identity}: {e.title}")
        emit("")

    adds = [e for e in entries if e.entry_type == "ADD"]
    emit(f"ADD ({len(adds)}) — your fixture should be here:")
    for e in adds:
        emit(f"  {e.kind} {e.identity}: {e.title}")
    emit("")

    conflicts = [e for e in entries if e.entry_type == "CONFLICT"]
    if conflicts:
        emit(f"CONFLICT ({len(conflicts)}):")
        for e in conflicts:
            emit(f"  {e.kind} {e.identity}: {e.reason}")
        emit("")

    mods = [e for e in entries if e.entry_type == "MODIFY"]
    if not mods:
        emit("No MODIFY entries — diff is clean; apply would proceed if the "
             "only ADD is your fixture.")
    else:
        field_freq = Counter()
        ws_only = 0
        for e in mods:
            for fname in e.changes:
                field_freq[fname] += 1
            if e.changes and all(
                str(o).split() == str(n).split()
                for o, n in e.changes.values()
            ):
                ws_only += 1
        emit(f"MODIFY ({len(mods)}):")
        emit(f"  whitespace/normalisation-only: {ws_only} "
             f"({100*ws_only//max(len(mods),1)}%)")
        emit("  changed-field frequency:")
        for fname, n in field_freq.most_common():
            emit(f"    {fname:24s} {n}")
        emit("")
        emit("READ: prose-field drift (objective/context/acceptance_criteria/"
             "implementation_notes) with high whitespace-only share => SPURIOUS "
             "(seed fidelity, ATLAS-145). Categorical drift (priority/risk_level/"
             "ticket_type) or substantive rewrites => REAL (planner instability).")
        emit("")

        show = os.environ.get("SMOKE_SHOW") or ""
        if show:
            hit = next((e for e in mods if e.identity == show), None)
            if hit is None:
                emit(f"--show: no MODIFY for {show!r}.")
            else:
                emit(f"=== MODIFY {hit.identity}: {hit.title} ===")
                for fname, (o, n) in sorted(hit.changes.items()):
                    emit(f"--- {fname} ---")
                    emit(f"OLD: {str(o)[:1500]}")
                    emit(f"NEW: {str(n)[:1500]}")
                emit("")

        n_sample = int(os.environ.get("SMOKE_SAMPLE") or 0)
        if n_sample > 0:
            emit(f"=== {min(n_sample, len(mods))} sample MODIFYs (truncated) ===")
            for e in mods[:n_sample]:
                emit(f"### {e.identity}: {e.title}")
                for fname, (o, nw) in sorted(e.changes.items()):
                    emit(f"  --- {fname} ---")
                    emit(f"  OLD: {str(o)[:600]}")
                    emit(f"  NEW: {str(nw)[:600]}")

open(os.environ["SMOKE_OUT"], "w").write("\n".join(out_lines) + "\n")
print(f"\n[report written to {os.environ['SMOKE_OUT']}]")
EOF
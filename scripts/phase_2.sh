#!/usr/bin/env bash
# Smoke B — Phase 2: sync outward, then machine-check the seam checkpoints.
#
# Runs `atlas pm sync --once`, then verifies through the REAL code paths (the
# same constructions the CLI uses — no re-implemented regexes, no GraphQL by
# hand):
#   2.2  the live Linear issue title equals render_definition_title(ticket)
#        byte-exactly, and round-trips through reports.parse_close_set to
#        exactly (FIXTURE,)   — the ATLAS-143 seam, observed live
#   2.3  the issue's Linear state maps (via the env LinearStatusMap) to
#        ready_for_agent — the PM Engine promotion (sole writer)
#
# Promotion may land one tick after creation/push, so up to MAX_TICKS ticks
# are run before 2.3 is declared failed. A 2.2 failure aborts immediately —
# nothing downstream is trustworthy if the embedded title didn't engage.
#
# Usage:  ./smoke-b-phase2.sh ATLAS-<n> [--db URL] [--max-ticks N]

set -u

FIXTURE="${1:?usage: smoke-b-phase2.sh ATLAS-<n> [--db URL] [--max-ticks N]}"
shift
DB_URL=""
MAX_TICKS=2
while [ $# -gt 0 ]; do
  case "$1" in
    --db) shift; DB_URL="${1:?--db needs a URL}" ;;
    --max-ticks) shift; MAX_TICKS="${1:?--max-ticks needs a number}" ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done

die() { echo "ABORT: $*" >&2; exit 1; }

# --- preconditions ---------------------------------------------------------
[ -f pyproject.toml ] && grep -q '^name = "atlas"' pyproject.toml \
  || die "run from the atlas repo root"
command -v uv >/dev/null 2>&1 || die "uv not on PATH (run Phase 0 first)"
case "$FIXTURE" in ATLAS-[0-9]*) ;; *) die "'$FIXTURE' is not an ATLAS-<n> key" ;; esac
for var in LINEAR_API_KEY LINEAR_TEAM_ID LINEAR_PROJECT_ID; do
  [ -n "$(eval "echo \${$var:-}")" ] || die "$var unset (Phase 0, check 0.4)"
done

DB_FLAG=()
[ -n "$DB_URL" ] && DB_FLAG=(--db "$DB_URL")
export SMOKE_FIXTURE_KEY="$FIXTURE"
export SMOKE_DB_URL="$DB_URL"

# The checkpoint, through the real code paths. Prints PASS/FAIL lines and a
# STATE line; exits 0 all-pass / 3 title-seam failure / 4 not-ready-yet.
checkpoint() {
  uv run python - << 'EOF'
import os, sys
from atlas.storage.db import Database
from atlas.storage.repositories import TicketRepo
from atlas.linear.client import LinearGraphQLClient
from atlas.linear.ownership import (
    LinearStatusMap,
    render_definition_title,
    status_from_issue,
)
from atlas.verification.reports import parse_close_set

key = os.environ["SMOKE_FIXTURE_KEY"]
db = Database(os.environ.get("SMOKE_DB_URL") or None)

ticket = TicketRepo(db).get_by_key(key)
if ticket is None:
    print(f"FAIL  2.2  {key} not in the Atlas store — wrong key or wrong --db")
    sys.exit(3)
if ticket.external_linear_id is None:
    print(f"FAIL  2.2  {key} has no external_linear_id — the sync tick did not create/join the Linear issue")
    sys.exit(3)

client = LinearGraphQLClient()          # creds from env, exactly as the CLI
issue = client.fetch_issue(ticket.external_linear_id)
if issue is None:
    print(f"FAIL  2.2  Linear issue {ticket.external_linear_id} not fetchable")
    sys.exit(3)

expected = render_definition_title(ticket)   # the REAL composer (ATLAS-143)
if issue.title != expected:
    print(f"FAIL  2.2  live title != render_definition_title output")
    print(f"      expected: {expected!r}")
    print(f"      actual:   {issue.title!r}")
    sys.exit(3)
print(f"PASS  2.2a live Linear title is byte-exact: {issue.title!r}")

close_set = parse_close_set(issue.title, None)   # the REAL parser
if close_set != (key,):
    print(f"FAIL  2.2  round-trip resolved {close_set!r}, expected ({key!r},)")
    sys.exit(3)
print(f"PASS  2.2b title round-trips through parse_close_set to exactly ({key},)")

status = status_from_issue(issue, LinearStatusMap.from_env())
print(f"STATE       Linear state {issue.state_name!r} -> Atlas status {getattr(status, 'value', None)!r}")
if status is None or status.value != "ready_for_agent":
    sys.exit(4)   # title seam fine; promotion not (yet) observed
print(f"PASS  2.3  promoted to Ready for Agent (PM Engine, sole writer)")
sys.exit(0)
EOF
}

# --- tick, then check; allow promotion one extra tick ------------------------
tick=1
while :; do
  echo "== atlas pm sync --once  (tick $tick/$MAX_TICKS) =="
  uv run atlas pm sync --once "${DB_FLAG[@]}" || die "sync tick failed — see output above"
  echo
  checkpoint; rc=$?
  case $rc in
    0) break ;;
    3) die "SEAM CHECKPOINT 2.2 FAILED — the ATLAS-143 embedded-title path did not engage live. Nothing downstream is trustworthy: capture the sync output and the Linear issue as the finding." ;;
    4) if [ "$tick" -ge "$MAX_TICKS" ]; then
         die "2.3 not reached after $MAX_TICKS tick(s) — title seam holds, but the fixture was not promoted to Ready for Agent. Check deps (uv run atlas deps ready), then investigate promotion before dispatch."
       fi
       echo "-- promotion not yet observed; running one more tick --"; echo
       tick=$((tick+1)) ;;
    *) die "checkpoint errored (exit $rc) — see traceback above" ;;
  esac
done

echo
echo "== Phase 2 complete =="
echo "Both seam checkpoints observed live; $FIXTURE is Ready for Agent."
echo "Linear's own identifier is now ignorable by construction."
echo "Proceed to Phase 3: point the Symphony poll at the board and OBSERVE —"
echo "next checkpoint is 3.2 (the agent's PR title carries $FIXTURE)."
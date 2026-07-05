#!/usr/bin/env bash
# Smoke B — Phase 3: dispatch watcher (Symphony's half; observe, don't steer).
#
# Polls the fixture's Linear state through the real client + env status map,
# reporting each transition, until it reaches `review_required` (the handoff)
# or times out. Then runs seam checkpoint 3.2: the agent's PR title must carry
# FIXTURE — verified through the REAL parser (parse_close_set) AND the real
# gate script (scripts/check_pr_title.py).
#
# The PR number is discovered from you: pass --pr if you already know it, or
# the script prompts once the ticket reaches PR Open / Review Required.
#
# Exit codes: 0 handoff reached + 3.2 PASS · 3 seam failure (FINDING) ·
# 4 routed to needs_human_decision (contract-respecting stop; investigate) ·
# 5 timeout · 2 precondition.
#
# Usage: ./smoke-b-phase3.sh ATLAS-<n> [--pr N] [--repo OWNER/REPO]
#                            [--interval S] [--timeout S] [--db URL]

set -u
FIXTURE="${1:?usage: smoke-b-phase3.sh ATLAS-<n> [--pr N] [--repo OWNER/REPO] [--interval S] [--timeout S] [--db URL]}"
shift
PR=""; REPO="derekrivers/atlas"; INTERVAL=30; TIMEOUT=1800; DB_URL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --pr) shift; PR="${1:?}" ;;
    --repo) shift; REPO="${1:?}" ;;
    --interval) shift; INTERVAL="${1:?}" ;;
    --timeout) shift; TIMEOUT="${1:?}" ;;
    --db) shift; DB_URL="${1:?}" ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac; shift
done

die() { echo "ABORT: $*" >&2; exit "${2:-2}"; }
[ -f pyproject.toml ] && grep -q '^name = "atlas"' pyproject.toml || die "run from the atlas repo root"
command -v uv >/dev/null 2>&1 || die "uv not on PATH (run Phase 0 first)"
case "$FIXTURE" in ATLAS-[0-9]*) ;; *) die "'$FIXTURE' is not an ATLAS-<n> key" ;; esac
for var in LINEAR_API_KEY GITHUB_TOKEN; do
  [ -n "$(eval "echo \${$var:-}")" ] || die "$var unset (Phase 0, check 0.4)"
done
export SMOKE_FIXTURE_KEY="$FIXTURE" SMOKE_DB_URL="$DB_URL"

# One state read through the real code paths; prints the mapped Atlas status.
read_state() {
  uv run python - << 'EOF'
import os, sys
from atlas.storage.db import Database
from atlas.storage.repositories import TicketRepo
from atlas.linear.client import LinearGraphQLClient
from atlas.linear.ownership import LinearStatusMap, status_from_issue
key = os.environ["SMOKE_FIXTURE_KEY"]
db = Database(os.environ.get("SMOKE_DB_URL") or None)
t = TicketRepo(db).get_by_key(key)
if t is None or t.external_linear_id is None:
    print("ERROR: fixture missing or not joined to Linear (run Phase 2 first)"); sys.exit(2)
issue = LinearGraphQLClient().fetch_issue(t.external_linear_id)
if issue is None:
    print("ERROR: Linear issue not fetchable"); sys.exit(2)
s = status_from_issue(issue, LinearStatusMap.from_env())
print(getattr(s, "value", f"UNMAPPED({issue.state_name})"))
EOF
}

echo "== Phase 3: watching $FIXTURE (every ${INTERVAL}s, timeout ${TIMEOUT}s) =="
echo "Expected path per WORKFLOW.md: ready_for_agent -> in_progress -> pr_open -> review_required, then STOP."
LAST=""; ELAPSED=0
while :; do
  STATE="$(read_state)" || die "$STATE"
  if [ "$STATE" != "$LAST" ]; then
    echo "$(date -u +%H:%M:%SZ)  state: ${LAST:-—} -> $STATE"
    LAST="$STATE"
  fi
  case "$STATE" in
    review_required) echo "Handoff reached — Symphony's half is done (ADR-0008 boundary holds)."; break ;;
    needs_human_decision) die "routed to Needs Human — a contract-respecting stop, not a crash. Investigate the dispatch transcript before anything else." 4 ;;
    done|rejected) die "terminal state '$STATE' without passing the acceptance gate — that is a FINDING (the operator gate was bypassed?)" 3 ;;
  esac
  [ "$ELAPSED" -lt "$TIMEOUT" ] || die "timeout after ${TIMEOUT}s in state '$STATE'. If dispatch produced silent empty turns, capture RAW EVENT TYPES first (the T3 lesson)." 5
  sleep "$INTERVAL"; ELAPSED=$((ELAPSED + INTERVAL))
done

# --- 3.2: the PR-title seam, live -------------------------------------------
if [ -z "$PR" ]; then
  printf 'PR number opened by the agent for %s: ' "$FIXTURE"
  read -r PR
fi
case "$PR" in [0-9]*) ;; *) die "'$PR' is not a PR number" ;; esac
export SMOKE_PR="$PR" SMOKE_REPO="$REPO"

TITLE="$(uv run python - << 'EOF'
import os, sys
from atlas.github.client import GitHubRESTClient
from atlas.verification.reports import parse_close_set
owner, repo = os.environ["SMOKE_REPO"].split("/", 1)
pr = GitHubRESTClient().fetch_pull_request(owner, repo, int(os.environ["SMOKE_PR"]))
title = pr.get("title") or ""
key = os.environ["SMOKE_FIXTURE_KEY"]
close = parse_close_set(title, None)
print(title)
sys.exit(0 if close == (key,) else 3)
EOF
)"; RC=$?
echo "PR #$PR title: $TITLE"
if [ $RC -ne 0 ]; then
  die "SEAM CHECKPOINT 3.2 FAILED — the PR title does not resolve to exactly ($FIXTURE,). The agent copied the WRONG key despite the contract. Capture the dispatch transcript: this is a finding, not a tweak-and-retry." 3
fi
uv run python scripts/check_pr_title.py "$TITLE" \
  || die "check_pr_title.py rejected the title the parser accepted — gate/parser divergence, itself a finding" 3
echo "PASS  3.2  PR title carries $FIXTURE (parse_close_set + lint-pr-title gate agree)"
echo
echo "== Phase 3 complete — proceed to Phase 4: ./smoke-b-phase4.sh $FIXTURE --pr $PR --repo $REPO =="
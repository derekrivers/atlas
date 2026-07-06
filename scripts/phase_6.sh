#!/usr/bin/env bash
# Smoke B — Phase 6: the merge and the Done gate.
#
# The MERGE ITSELF IS YOURS (human gate #3) — this script never merges. It:
#   6.0  checks the PR is merged (via the real GitHub client) and, best-effort,
#        that branch protection requires lint-pr-title (WARN, not FAIL — the
#        API read needs admin scope on some tokens)
#   6.1  runs `atlas verify` — per ATLAS-134 it is VERIFY that observes the
#        out-of-band merge and records the system-tier PR_MERGED evidence at
#        the verified head commit C (append-only, idempotent)
#   6.2  checks the PR_MERGED row exists at commit C through the store; a
#        merge recorded at a DIFFERENT commit must HOLD the gate — reported
#        as the completion guard working (exit 7), not a smoke failure
#   6.3  sync tick — complete_verified moves review_required -> Done (Linear)
#   6.4  one more tick — the pull reconciles Atlas-side status to done
#        (pull is the single writer of Atlas status; one tick of latency
#        is by design)
#
# Exit codes: 0 Done observed both sides · 7 completion guard held (commit
# mismatch or verdict not PASSED — investigate, don't force) · 2 precondition.
#
# Usage: ./smoke-b-phase6.sh ATLAS-<n> --pr N [--repo OWNER/REPO] [--db URL]

set -u
FIXTURE="${1:?usage: smoke-b-phase6.sh ATLAS-<n> --pr N [--repo OWNER/REPO] [--db URL]}"
shift
PR=""; REPO="derekrivers/atlas"; DB_URL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --pr) shift; PR="${1:?}" ;;
    --repo) shift; REPO="${1:?}" ;;
    --db) shift; DB_URL="${1:?}" ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac; shift
done
die() { echo "ABORT: $*" >&2; exit "${2:-2}"; }
[ -f pyproject.toml ] && grep -q '^name = "atlas"' pyproject.toml || die "run from the atlas repo root"
command -v uv >/dev/null 2>&1 || die "uv not on PATH"
[ -n "$PR" ] || die "--pr is required"
for var in GITHUB_TOKEN LINEAR_API_KEY LINEAR_TEAM_ID LINEAR_PROJECT_ID; do
  [ -n "$(eval "echo \${$var:-}")" ] || die "$var unset (Phase 0, check 0.4)"
done
case "$FIXTURE" in ATLAS-[0-9]*) ;; *) die "'$FIXTURE' is not an ATLAS-<n> key" ;; esac
DB_FLAG=(); [ -n "$DB_URL" ] && DB_FLAG=(--db "$DB_URL")
export SMOKE_FIXTURE_KEY="$FIXTURE" SMOKE_DB_URL="$DB_URL" SMOKE_PR="$PR" SMOKE_REPO="$REPO"

# --- 6.0 merged? + branch-protection posture ---------------------------------
HEAD_COMMIT="$(uv run python - << 'EOF'
import os, sys
from atlas.github.client import GitHubRESTClient
owner, repo = os.environ["SMOKE_REPO"].split("/", 1)
pr = GitHubRESTClient().fetch_pull_request(owner, repo, int(os.environ["SMOKE_PR"]))
if not pr.get("merged"):
    print("NOT_MERGED"); sys.exit(0)
print(pr["head"]["sha"])
EOF
)" || die "could not fetch the PR"
[ "$HEAD_COMMIT" != "NOT_MERGED" ] \
  || die "PR #$PR is not merged yet — merge it in GitHub (human gate #3), then re-run this script"
echo "PASS  6.0  PR #$PR merged; head commit C = $HEAD_COMMIT"

PROT="$(curl -sf -H "Authorization: Bearer $GITHUB_TOKEN" \
  "https://api.github.com/repos/$REPO/branches/main/protection/required_status_checks" 2>/dev/null || true)"
if [ -n "$PROT" ] && printf '%s' "$PROT" | grep -q "lint-pr-title"; then
  echo "PASS  6.0  branch protection requires lint-pr-title"
else
  echo "WARN  6.0  could not confirm lint-pr-title in required status checks (token scope, or not set — the post-#142 operator step); verify in GitHub settings"
fi

# --- 6.1 verify observes the merge (ATLAS-134) --------------------------------
echo; echo "== 6.1 atlas verify (records PR_MERGED at C; verify never merges) =="
uv run atlas verify --pr "$PR" --repo "$REPO" "${DB_FLAG[@]}" || die "verify errored"

# --- 6.2 the merge evidence, commit-pinned, through the store ------------------
echo; echo "== 6.2 PR_MERGED evidence at C (store-side) =="
export SMOKE_HEAD="$HEAD_COMMIT"
uv run python - << 'EOF'
import os, sys
from atlas.storage.db import Database
from atlas.storage.repositories import TicketRepo, EvidenceRepo
key, head = os.environ["SMOKE_FIXTURE_KEY"], os.environ["SMOKE_HEAD"]
db = Database(os.environ.get("SMOKE_DB_URL") or None)
ticket = TicketRepo(db).get_by_key(key)
merged = [e for e in EvidenceRepo(db).list()
          if e.ticket_id == ticket.id and e.evidence_type.value == "pr_merged"]
if not merged:
    print("FAIL  6.2  no PR_MERGED evidence recorded — did 6.1's verify see the merge?")
    sys.exit(2)
at_c = [e for e in merged if e.commit_sha == head]
if not at_c:
    seen = sorted({e.commit_sha for e in merged})
    print(f"HOLD  6.2  PR_MERGED recorded but at {seen}, not at C={head} — the "
          "completion guard MUST hold the gate. This ends the run; it is the "
          "guard working, not a smoke failure. Investigate the commit drift.")
    sys.exit(7)
print(f"PASS  6.2  PR_MERGED at C={head} (system-tier, commit-pinned)")
EOF
RC=$?; [ $RC -eq 0 ] || exit $RC

# --- 6.3 / 6.4 the Done gate, two ticks -----------------------------------------
linear_and_atlas_status() {
  uv run python - << 'EOF'
import os
from atlas.storage.db import Database
from atlas.storage.repositories import TicketRepo
from atlas.linear.client import LinearGraphQLClient
from atlas.linear.ownership import LinearStatusMap, status_from_issue
key = os.environ["SMOKE_FIXTURE_KEY"]
db = Database(os.environ.get("SMOKE_DB_URL") or None)
t = TicketRepo(db).get_by_key(key)
issue = LinearGraphQLClient().fetch_issue(t.external_linear_id)
linear = status_from_issue(issue, LinearStatusMap.from_env())
print(f"{getattr(linear, 'value', 'UNMAPPED')} {t.status.value}")
EOF
}

echo; echo "== 6.3 sync tick (complete_verified: review_required -> Done, Linear-only write) =="
uv run atlas pm sync --once "${DB_FLAG[@]}" || die "sync tick failed"
read -r LINEAR_S ATLAS_S <<< "$(linear_and_atlas_status)"
echo "after tick 1:  Linear -> $LINEAR_S · Atlas store -> $ATLAS_S"
[ "$LINEAR_S" = "done" ] \
  || die "Linear is not Done after the tick — the gate held (verdict not PASSED at C, or eligibility failed). Run 'uv run atlas verify --pr $PR --repo $REPO' and read the report; do not force." 7

echo; echo "== 6.4 one more tick (the pull reconciles Atlas-side status; latency by design) =="
uv run atlas pm sync --once "${DB_FLAG[@]}" || die "sync tick failed"
read -r LINEAR_S ATLAS_S <<< "$(linear_and_atlas_status)"
echo "after tick 2:  Linear -> $LINEAR_S · Atlas store -> $ATLAS_S"
[ "$ATLAS_S" = "done" ] \
  || die "Atlas-side status did not reconcile to done on the pull — investigate the sync log for a dropped/unmapped state" 7

echo
echo "== Phase 6 complete: $FIXTURE is Done on BOTH sides. The loop closed. =="
echo "Proceed to Phase 7 (closeout capture):"
echo "   ./smoke-b-phase7.sh $FIXTURE --pr $PR --repo $REPO"
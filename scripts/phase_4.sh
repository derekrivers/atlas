#!/usr/bin/env bash
# Smoke B — Phase 4: evidence and the FIRST verdict (system tier first).
#
#   4.1  atlas evidence pull  — CI evidence ingested; the script then checks
#        the full pin triple (commit_sha + external_run_id + payload_hash)
#        on the ingested system-tier rows, through the store.
#   4.2  atlas verify (--json) — verdict for FIXTURE must be PENDING.
#        PENDING here is CORRECT: acceptance/scope/human checks await the
#        operator. A PASSED before `atlas confirm` means the human gate was
#        BYPASSED — that is a finding that outranks the smoke (exit 3).
#
# Exit codes: 0 evidence pinned + verdict pending · 3 gate-bypass finding ·
# 6 verdict FAILED (real verification failure — triage, not a smoke defect) ·
# 2 precondition.
#
# Usage: ./smoke-b-phase4.sh ATLAS-<n> --pr N [--repo OWNER/REPO] [--db URL]

set -u
FIXTURE="${1:?usage: smoke-b-phase4.sh ATLAS-<n> --pr N [--repo OWNER/REPO] [--db URL]}"
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
[ -n "${GITHUB_TOKEN:-}" ] || die "GITHUB_TOKEN unset (Phase 0, check 0.4)"
case "$FIXTURE" in ATLAS-[0-9]*) ;; *) die "'$FIXTURE' is not an ATLAS-<n> key" ;; esac

DB_FLAG=(); [ -n "$DB_URL" ] && DB_FLAG=(--db "$DB_URL")
export SMOKE_FIXTURE_KEY="$FIXTURE" SMOKE_DB_URL="$DB_URL"

echo "== 4.1 atlas evidence pull --pr $PR --repo $REPO =="
uv run atlas evidence pull --pr "$PR" --repo "$REPO" "${DB_FLAG[@]}" \
  || die "evidence pull failed — see output above"

echo
echo "== 4.1 pin-triple check (store-side, real repos) =="
uv run python - << 'EOF' || exit $?
import os, sys
from atlas.storage.db import Database
from atlas.storage.repositories import TicketRepo, EvidenceRepo

key = os.environ["SMOKE_FIXTURE_KEY"]
db = Database(os.environ.get("SMOKE_DB_URL") or None)
ticket = TicketRepo(db).get_by_key(key)
if ticket is None:
    print(f"ABORT: {key} not in the store"); sys.exit(2)
rows = [e for e in EvidenceRepo(db).list() if e.ticket_id == ticket.id]
if not rows:
    print("FAIL  4.1  no evidence rows for the fixture after the pull"); sys.exit(2)
bad = [e for e in rows
       if e.created_by_type.value == "system"
       and not (e.commit_sha and e.external_run_id and e.payload_hash)]
if bad:
    print(f"FAIL  4.1  {len(bad)} system-tier row(s) missing the pin triple "
          "(commit_sha + external_run_id + payload_hash) — EvidenceRepo.add "
          "should have enforced this; that is a finding")
    sys.exit(3)
system = [e for e in rows if e.created_by_type.value == "system"]
print(f"PASS  4.1  {len(rows)} evidence row(s) for {key}; "
      f"{len(system)} system-tier, all carrying the full pin triple")
EOF
RC=$?; [ $RC -eq 0 ] || exit $RC

echo
echo "== 4.2 atlas verify --pr $PR (first verdict) =="
VERIFY_JSON="$(mktemp)"
uv run atlas verify --pr "$PR" --repo "$REPO" --json "${DB_FLAG[@]}" > "$VERIFY_JSON" \
  || die "verify errored (verify is EXIT_OK on ANY verdict, so this is a precondition failure)"
export SMOKE_VERIFY_JSON="$VERIFY_JSON"

uv run python - << 'EOF'
import json, os, sys
from atlas.storage.db import Database
from atlas.storage.repositories import TicketRepo

key = os.environ["SMOKE_FIXTURE_KEY"]
db = Database(os.environ.get("SMOKE_DB_URL") or None)
ticket = TicketRepo(db).get_by_key(key)
payload = json.load(open(os.environ["SMOKE_VERIFY_JSON"]))
mine = [t for t in payload["tickets"] if t["ticket_id"] == str(ticket.id)]
if not mine:
    print(f"FAIL  4.2  {key} absent from the verify close-set — the PR title "
          "did not resolve to it (re-check Phase 3's 3.2)"); sys.exit(3)
verdict = mine[0]["status"]
pending = [c["check_type"] for c in mine[0]["checks"] if c["status"] == "pending"]
print(f"verdict for {key}: {verdict}")
print(f"pending checks: {', '.join(pending) or '(none)'}")
if verdict == "pending":
    print("PASS  4.2  PENDING before operator confirmation — the human gate is intact")
    sys.exit(0)
if verdict == "passed":
    print("FAIL  4.2  PASSED before `atlas confirm` — the OPERATOR GATE WAS "
          "BYPASSED. This finding outranks the smoke: stop and investigate "
          "which evaluator passed without human-tier evidence.")
    sys.exit(3)
print(f"FAIL  4.2  verdict {verdict!r} — a real verification failure; triage "
      "the failed checks above (this ends the run but is the machinery "
      "working, not a smoke defect)")
sys.exit(6)
EOF
RC=$?
rm -f "$VERIFY_JSON"
[ $RC -eq 0 ] || exit $RC

echo
echo "== Phase 4 complete — proceed to Phase 5 (human gate #2): =="
echo "   ./smoke-b-phase5.sh $FIXTURE --pr $PR --repo $REPO"
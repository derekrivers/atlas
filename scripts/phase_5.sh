#!/usr/bin/env bash
# Smoke B — Phase 5: operator confirmation (human gate #2), then recompute.
#
#   5.1  atlas confirm — INTERACTIVE by contract (OP-3.2/D-4: no blanket
#        confirm-all exists; you decide each item). Records only — no
#        verdict, no transition (D-5).
#   5.2  atlas verify — the verdict recomputed from the new human-tier
#        Evidence must now be PASSED for FIXTURE.
#
# The thinnest possible wrapper by design: the gate is YOURS; the script
# only frames it and checks the recomputation afterwards.
#
# Exit codes: 0 confirmed + PASSED · 6 still not PASSED after confirm
# (skipped items or a real failure — triage) · 2 precondition.
#
# Usage: ./smoke-b-phase5.sh ATLAS-<n> --pr N [--repo OWNER/REPO] [--db URL]

set -u
FIXTURE="${1:?usage: smoke-b-phase5.sh ATLAS-<n> --pr N [--repo OWNER/REPO] [--db URL]}"
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
[ -t 0 ] || die "no TTY — confirm is interactive by contract (it refuses headless; so does this script)"
[ -n "${ATLAS_OPERATOR_ID:-}" ] || die "ATLAS_OPERATOR_ID unset — no anonymous human-tier writes"
[ -n "${GITHUB_TOKEN:-}" ] || die "GITHUB_TOKEN unset"
case "$FIXTURE" in ATLAS-[0-9]*) ;; *) die "'$FIXTURE' is not an ATLAS-<n> key" ;; esac

DB_FLAG=(); [ -n "$DB_URL" ] && DB_FLAG=(--db "$DB_URL")

echo "== 5.1 atlas confirm — human gate #2: you decide EACH item; there is no confirm-all =="
uv run atlas confirm --pr "$PR" --repo "$REPO" --operator "$ATLAS_OPERATOR_ID" "${DB_FLAG[@]}" \
  || die "confirm exited on a precondition — see the one-line reason above"

echo
echo "== 5.2 atlas verify — recomputing from the new human-tier evidence =="
VERIFY_JSON="$(mktemp)"
uv run atlas verify --pr "$PR" --repo "$REPO" --json "${DB_FLAG[@]}" > "$VERIFY_JSON" \
  || die "verify errored"
export SMOKE_FIXTURE_KEY="$FIXTURE" SMOKE_DB_URL="$DB_URL" SMOKE_VERIFY_JSON="$VERIFY_JSON"

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
    print(f"ABORT: {key} absent from the verify close-set"); sys.exit(2)
verdict = mine[0]["status"]
not_passed = [(c["check_type"], c["status"], c["reason"])
              for c in mine[0]["checks"] if c["status"] != "passed" and c["required"]]
print(f"verdict for {key}: {verdict}")
for ct, st, reason in not_passed:
    print(f"  {st:8s} {ct}: {reason}")
if verdict == "passed":
    print("PASS  5.2  PASSED from persisted checks — the PM tick can now act on it")
    sys.exit(0)
print("FAIL  5.2  not PASSED after confirmation — items you skipped stay "
      "pending (re-run Phase 5 to confirm them) or a required check failed "
      "(triage above). The gate holding is the machinery working.")
sys.exit(6)
EOF
RC=$?
rm -f "$VERIFY_JSON"
[ $RC -eq 0 ] || exit $RC

echo
echo "== Phase 5 complete — proceed to Phase 6 (human gate #3, the merge): =="
echo "   merge PR #$PR in GitHub, then run:"
echo "   ./smoke-b-phase6.sh $FIXTURE --pr $PR --repo $REPO"
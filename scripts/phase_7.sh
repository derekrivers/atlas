#!/usr/bin/env bash
# Smoke B — Phase 7: closeout capture.
#
# Gathers the run's evidence into one markdown bundle, in the shape the
# Phase 8 closure report needs: fixture key, PR, head commit C, the seam
# checkpoints, every evidence row (with pin triples), the final verify
# report, both status sides, and the pm delivery report. Read-only.
#
# Usage: ./smoke-b-phase7.sh ATLAS-<n> --pr N [--repo OWNER/REPO] [--db URL]
#                            [--out FILE]   (default: smoke-b-closeout.md)

set -u
FIXTURE="${1:?usage: smoke-b-phase7.sh ATLAS-<n> --pr N [--repo OWNER/REPO] [--db URL] [--out FILE]}"
shift
PR=""; REPO="derekrivers/atlas"; DB_URL=""; OUT="smoke-b-closeout.md"
while [ $# -gt 0 ]; do
  case "$1" in
    --pr) shift; PR="${1:?}" ;;
    --repo) shift; REPO="${1:?}" ;;
    --db) shift; DB_URL="${1:?}" ;;
    --out) shift; OUT="${1:?}" ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac; shift
done
die() { echo "ABORT: $*" >&2; exit 2; }
[ -f pyproject.toml ] && grep -q '^name = "atlas"' pyproject.toml || die "run from the atlas repo root"
command -v uv >/dev/null 2>&1 || die "uv not on PATH"
[ -n "$PR" ] || die "--pr is required"
case "$FIXTURE" in ATLAS-[0-9]*) ;; *) die "'$FIXTURE' is not an ATLAS-<n> key" ;; esac
DB_FLAG=(); [ -n "$DB_URL" ] && DB_FLAG=(--db "$DB_URL")
export SMOKE_FIXTURE_KEY="$FIXTURE" SMOKE_DB_URL="$DB_URL" SMOKE_PR="$PR" SMOKE_REPO="$REPO" SMOKE_OUT="$OUT"

VERIFY_TXT="$(mktemp)"; REPORT_MD="$(mktemp)"
uv run atlas verify --pr "$PR" --repo "$REPO" "${DB_FLAG[@]}" > "$VERIFY_TXT" 2>&1 \
  || die "verify errored — the bundle needs a final report"
uv run atlas pm report "${DB_FLAG[@]}" > "$REPORT_MD" 2>&1 || echo "(pm report unavailable)" > "$REPORT_MD"
export SMOKE_VERIFY_TXT="$VERIFY_TXT" SMOKE_REPORT_MD="$REPORT_MD"

uv run python - << 'EOF' || exit 2
import os
from datetime import UTC, datetime
from atlas.storage.db import Database
from atlas.storage.repositories import TicketRepo, EvidenceRepo, VerificationCheckRepo
from atlas.linear.client import LinearGraphQLClient
from atlas.linear.ownership import (
    LinearStatusMap, render_definition_title, status_from_issue,
)
from atlas.github.client import GitHubRESTClient
from atlas.verification.reports import parse_close_set

key = os.environ["SMOKE_FIXTURE_KEY"]
db = Database(os.environ.get("SMOKE_DB_URL") or None)
ticket = TicketRepo(db).get_by_key(key)
if ticket is None:
    raise SystemExit(f"ABORT: {key} not in the store")

owner, repo = os.environ["SMOKE_REPO"].split("/", 1)
pr_num = int(os.environ["SMOKE_PR"])
pr = GitHubRESTClient().fetch_pull_request(owner, repo, pr_num)
head = pr.get("head", {}).get("sha", "?")
pr_title = pr.get("title") or ""

issue = None
linear_status = None
if ticket.external_linear_id and os.environ.get("LINEAR_API_KEY"):
    issue = LinearGraphQLClient().fetch_issue(ticket.external_linear_id)
    if issue is not None:
        linear_status = status_from_issue(issue, LinearStatusMap.from_env())

rows = [e for e in EvidenceRepo(db).list() if e.ticket_id == ticket.id]
checks = VerificationCheckRepo(db).list_for_ticket(ticket.id)

seam_22 = issue is not None and issue.title == render_definition_title(ticket)
seam_32 = parse_close_set(pr_title, None) == (key,)

lines = []
w = lines.append
w(f"# Smoke B closeout — {key}")
w("")
w(f"Captured: {datetime.now(UTC).isoformat(timespec='seconds')}")
w(f"Repo: {owner}/{repo} · PR: #{pr_num} · head commit C: `{head}` · merged: {bool(pr.get('merged'))}")
w(f"Fixture: {key} — \"{ticket.title}\" · Linear issue: {ticket.external_linear_id}")
w("")
w("## Seam checkpoints (ATLAS-143, observed live)")
w(f"- 2.2 Linear title embeds the Atlas key: {'PASS' if seam_22 else 'NOT OBSERVED/FAIL'}"
  + (f" — `{issue.title}`" if issue else " (issue not fetched)"))
w(f"- 3.2 PR title resolves to exactly ({key},): {'PASS' if seam_32 else 'FAIL'} — `{pr_title}`")
w("")
w("## Final states")
w(f"- Linear: {getattr(linear_status, 'value', 'unfetched')}"
  + (f" (state {issue.state_name!r})" if issue else ""))
w(f"- Atlas store: {ticket.status.value}")
w("")
w(f"## Evidence rows ({len(rows)})")
for e in sorted(rows, key=lambda e: (e.evidence_type.value, str(e.id))):
    pin = f"commit={e.commit_sha} run={e.external_run_id} hash={e.payload_hash}" \
        if e.created_by_type.value == "system" else f"actor={e.created_by_type.value}"
    w(f"- `{e.id}` {e.evidence_type.value} [{e.status.value}] {pin}")
w("")
w(f"## Verification checks ({len(checks)} append-only rows)")
for c in checks:
    w(f"- {c.check_type.value} [{c.status.value}] at {c.created_at.isoformat(timespec='seconds')}")
w("")
w("## Final verify report")
w("```")
w(open(os.environ["SMOKE_VERIFY_TXT"]).read().rstrip())
w("```")
w("")
w("## PM delivery report")
w(open(os.environ["SMOKE_REPORT_MD"]).read().rstrip())
w("")
w("## Follow-ups")
w("(File observed follow-ups as `atlas:proposed-follow-up` comments — the")
w("producer/consumer path is live; eat the dogfood. List them here for the record.)")
w("")
w("---")
w("Milestone base case: a ready, context-rich fixture ticket flowed")
w("pack → Symphony → PR → evidence → verification → Done, with human steps")
w("only at the defined gates (apply, confirm, merge). ATLAS-90 formalises")
w("exactly this sequence.")

open(os.environ["SMOKE_OUT"], "w").write("\n".join(lines) + "\n")
print(f"Bundle written: {os.environ['SMOKE_OUT']}")
print(f"Seam 2.2: {'PASS' if seam_22 else 'CHECK'} · Seam 3.2: {'PASS' if seam_32 else 'CHECK'} · "
      f"Linear: {getattr(linear_status, 'value', '?')} · Atlas: {ticket.status.value}")
EOF
RC=$?
rm -f "$VERIFY_TXT" "$REPORT_MD"
[ $RC -eq 0 ] || exit $RC

echo
echo "== Phase 7 complete. The bundle is closure-report-ready ($OUT). =="
echo "Smoke B is the ATLAS-90 base case: base-case before generaliser holds."
#!/usr/bin/env bash
# Smoke B — Phase 1: mint the fixture ticket (Atlas store is the mint).
#
# Flow: hand-authored inbox stub → operator commits it (committed-inbox gate)
# → `atlas plan` (planner proposes) → `atlas apply` (INTERACTIVE — human gate
# #1, never --yes here) → minted key extracted from the tickets.yaml render
# diff → context-render sanity → closing instructions.
#
# RUNS IN YOUR WORKING CHECKOUT (it writes and commits a stub) — not a temp
# clone. Requires a TTY for the apply gate.
#
# Usage:
#   ./smoke-b-phase1.sh                      # default docs-touch fixture stub
#   ./smoke-b-phase1.sh --stub-file F.md     # your own stub content instead
#   ./smoke-b-phase1.sh --db URL             # override the database URL

set -u

DB_FLAG=()
DB_URL=""
STUB_SRC=""
STUB_PATH="docs/planning/inbox/smoke-b-fixture.md"

while [ $# -gt 0 ]; do
  case "$1" in
    --db) shift; DB_URL="${1:?--db needs a URL}"; DB_FLAG=(--db "$DB_URL") ;;
    --stub-file) shift; STUB_SRC="${1:?--stub-file needs a path}" ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done

die() { echo "ABORT: $*" >&2; exit 1; }

# --- preconditions -----------------------------------------------------------
[ -f pyproject.toml ] && grep -q '^name = "atlas"' pyproject.toml \
  || die "run from the atlas repo root"
[ -t 0 ] || die "no TTY — the apply gate is interactive by design (no --yes)"
command -v uv >/dev/null 2>&1 || die "uv not on PATH (run Phase 0 first)"
[ -n "${ANTHROPIC_API_KEY:-}" ] || die "ANTHROPIC_API_KEY unset — atlas plan needs the planner client"
[ -z "$(git status --porcelain)" ] || die "working tree dirty — plan reads HEAD and apply refuses a dirty tree; commit or stash first"
# Resume support: a stub that exists AND is committed clean is a prior run's
# gate already passed — skip authoring and proceed to plan. An uncommitted /
# dirty stub is still a hard stop (plan would raise DirtyInputError anyway).
RESUME=0
if [ -e "$STUB_PATH" ]; then
  if git ls-files --error-unmatch "$STUB_PATH" > /dev/null 2>&1 \
     && [ -z "$(git status --porcelain -- "$STUB_PATH")" ]; then
    echo "== $STUB_PATH already committed — resuming at the plan step =="
    RESUME=1
  else
    die "$STUB_PATH exists but is uncommitted/dirty — commit it or remove it, then re-run"
  fi
fi

# --- author the stub (skipped on resume) --------------------------------------
if [ "$RESUME" -eq 0 ]; then
mkdir -p "$(dirname "$STUB_PATH")" || die "could not create $(dirname "$STUB_PATH")"
if [ -n "$STUB_SRC" ]; then
  cp "$STUB_SRC" "$STUB_PATH" || die "could not copy $STUB_SRC into place"
else
  cat > "$STUB_PATH" << 'EOF'
# Smoke B fixture: add a delivery-loop smoke marker to the README

Source: Smoke B operator runbook, Phase 1 (hand-authored fixture stub; the
smoke tests the LOOP, not the work).

Objective: add a single short "Delivery loop" subsection to README.md stating
that changes to this repository flow through the Atlas delivery loop
(plan -> pack -> dispatch -> PR -> evidence -> verification -> Done). One
paragraph, no other files touched.

Acceptance criteria:
- README.md contains a "Delivery loop" heading with exactly one paragraph
  under it describing the loop as above.
- No file other than README.md is modified.
- The paragraph names the acceptance gate as operator-owned (ADR-0008).

Deliberately small and inert: a docs-only change with falsifiable ACs, sized
so the interesting part of the smoke is the machinery around it.
EOF
fi
[ -s "$STUB_PATH" ] || die "stub write failed — $STUB_PATH is missing or empty"

echo "== Stub written to $STUB_PATH =="
echo "--------------------------------------------------------------"
cat "$STUB_PATH"
echo "--------------------------------------------------------------"
echo "Review (edit now in another window if needed), then commit it —"
echo "plan reads HEAD; an uncommitted stub raises DirtyInputError."
printf 'Commit the stub? [y/N] '
read -r answer
[ "$answer" = "y" ] || { rm -f "$STUB_PATH"; die "operator declined — stub removed, nothing committed"; }
git add "$STUB_PATH"
git commit -q -m "smoke-b: fixture inbox stub (operator-authored, Phase 1)" \
  || die "commit failed"
echo "Committed: $(git log -1 --oneline)"
fi

# --- snapshot minted keys BEFORE apply ----------------------------------------
keys_in_render() {
  [ -f docs/planning/tickets.yaml ] \
    && grep -o 'ATLAS-[0-9]\+' docs/planning/tickets.yaml | sort -u || true
}
BEFORE="$(keys_in_render)"

export SMOKE_DB_URL="$DB_URL"

# --- plan: route by backlog posture (ADR-0010 boundary) ------------------------
# The corpus sits AT the 64K single-call output ceiling (planning-large-corpora.md:
# prior live runs at 95-97% of the limit, two of three truncated). Routing:
#   empty backlog  -> `plan --staged` (three bounded stages; first-run supported)
#   non-empty      -> refuse loudly: single-call is a coin flip at the cliff and
#                     staged re-plan is refused until re-plan seeding lands.
#                     Override with SMOKE_ALLOW_SINGLE_CALL=1 if you accept the flip.
BACKLOG_N="$(uv run python -c "
import os
from atlas.storage.db import Database
from atlas.storage.repositories import TicketRepo
db = Database(os.environ.get('SMOKE_DB_URL') or None)
print(len(TicketRepo(db).list()))
" 2>/dev/null)" || die "could not read the backlog count from the store"
echo; echo "== backlog posture: $BACKLOG_N ticket(s) in the store =="
# Post-ATLAS-144: --staged supports re-planning a non-empty store (seeded
# re-plan). Staged is now the default on BOTH postures; single-call remains
# an explicit override only.
if [ "${SMOKE_ALLOW_SINGLE_CALL:-0}" = "1" ]; then
  echo "== atlas plan (single-call, OPERATOR-OVERRIDDEN at the 64K truncation cliff) =="
  uv run atlas plan --repo . "${DB_FLAG[@]}" \
    || die "single-call plan failed — if the error names the 64K truncation, that is the ADR-0010 boundary; re-run or drop the override and use staged"
else
  echo "== atlas plan --staged ($BACKLOG_N ticket(s): seeded re-plan per ATLAS-144; empty store = first run) =="
  if ! uv run atlas plan --staged --repo . "${DB_FLAG[@]}"; then
    die "staged plan failed — see output above. If it refused naming EPIC-LESS ticket keys, that is the ATLAS-144 A-3 guard: those tickets have no per-epic batch to be restated in. Options: attach them to an epic, or rule the unassigned-batch follow-up (candidate ATLAS-145). The committed stub survives either way."
  fi
fi

# --- apply: HUMAN GATE #1 (interactive; exit 0 applied / 1 rejected / 2 refusal)
echo; echo "== atlas apply --add-only — human gate #1: review the diff, answer y/N =="
# --add-only (ATLAS-109/110): skip MODIFY + PROPOSE_ARCHIVE from the re-plan and
# skip frozen-source CONFLICTs (a re-stated DONE/frozen ticket the planner
# drifted); mint only the promoted fixture ADD. A CONFLICT now refuses ONLY on an
# identity/similarity ambiguity — inspect it if it fires (see Edit 2).
uv run atlas apply --add-only --repo . "${DB_FLAG[@]}"
case $? in
  0) echo "Applied." ;;
  1) die "operator rejected the plan at the gate — PlanRun rejected; nothing minted" ;;
  *) die "apply refused (precondition). A CONFLICT now refuses ONLY on an identity
or similarity ambiguity — a duplicate echoed key, or the fixture ADD ambiguously
matching an existing ticket (>=0.85). Frozen-source conflicts from re-stated DONE
tickets are skipped, not refused. Inspect the named conflict: it is a real
matching ambiguity, not the DONE-backlog wall." ;;
esac

# --- extract the minted key(s) --------------------------------------------------
AFTER="$(keys_in_render)"
NEW_KEYS="$(comm -13 <(printf '%s\n' "$BEFORE") <(printf '%s\n' "$AFTER") | grep . || true)"
echo
if [ -z "$NEW_KEYS" ]; then
  die "no new key found in docs/planning/tickets.yaml after apply — inspect the render diff (git diff docs/planning/)"
fi
COUNT=$(printf '%s\n' "$NEW_KEYS" | wc -l)
echo "== Minted key(s) ($COUNT new in tickets.yaml) =="
printf '%s\n' "$NEW_KEYS"
if [ "$COUNT" -gt 1 ]; then
  echo "NOTE: the planner proposed more than the fixture. Identify which key is"
  echo "the smoke fixture from the diff below; the others are real backlog."
  git diff --stat -- docs/planning/
fi

# --- sanity: the context pack renders for each new key --------------------------
echo; echo "== context render sanity =="
while read -r key; do
  [ -n "$key" ] || continue
  if uv run atlas context render "$key" --repo . "${DB_FLAG[@]}" > /dev/null; then
    echo "PASS  $key — pack renders"
  else
    echo "FAIL  $key — pack does NOT render; do not dispatch this ticket"
  fi
done <<< "$NEW_KEYS"

# --- closing instructions --------------------------------------------------------
echo
echo "== Phase 1 complete — operator closeout =="
echo "1. Review the apply writes:      git status && git diff docs/planning/"
echo "   (renders + your stub retired to docs/planning/inbox/processed/)"
echo "2. Commit and push them:"
echo "     git add docs/planning && git commit -m 'smoke-b: apply fixture plan (renders + retired stub)' && git push"
echo "3. Record the fixture key:       export FIXTURE=<key from above>"
echo "4. Proceed to Phase 2:           ./smoke-b-phase2.sh \$FIXTURE"
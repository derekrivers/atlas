#!/usr/bin/env bash
#
# refresh_board.sh — rebuild the local board and push full-spec descriptions.
#
# Runs the four-step refresh runbook in order, stopping at the first failure:
#
#   1. scripts/reset_db.py --yes   wipe backlog + link/cursor state, reseed ATLAS
#   2. atlas plan --staged         regenerate the proposal (first-run-only path)
#   3. atlas apply                 INTERACTIVE diff gate — you confirm y/N
#   4. atlas pm sync --once         push fresh Linear issues w/ full-spec bodies
#
# Step 3 is a human gate by design (docs/runbooks/running-atlas-plan.md): if you
# reject the diff, or apply refuses (stale plan / dirty tree / no TTY), the
# script stops and does NOT touch Linear. The Linear push only ever runs against
# a plan you applied.
#
# Usage:
#   ./scripts/refresh_board.sh         # confirm once, then run
#   ./scripts/refresh_board.sh --yes   # skip the upfront confirm (reset is still
#                                      #   --yes; the apply gate stays interactive)
#
# Requires a terminal for the apply gate, ANTHROPIC_API_KEY (plan), and the
# Linear env (LINEAR_API_KEY / LINEAR_STATE_MAP, for pm sync).

set -euo pipefail

assume_yes=0
for arg in "$@"; do
  case "$arg" in
    -y | --yes) assume_yes=1 ;;
    -h | --help)
      # Print the leading header block only (skip the shebang, stop at the first
      # non-comment line — so inline comments further down don't leak into help).
      awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"
      exit 0
      ;;
    *)
      echo "unknown argument: $arg (try --help)" >&2
      exit 2
      ;;
  esac
done

# Run from the repo root regardless of where the script is invoked from, so the
# default .atlas/atlas.db and docs/planning/ paths resolve correctly.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

confirm() {
  [ "$assume_yes" -eq 1 ] && return 0
  if [ ! -t 0 ]; then
    echo "refresh needs confirmation: re-run with --yes (no TTY available)." >&2
    return 1
  fi
  read -r -p \
    "This wipes the local backlog, spends API credits (staged plan), and pushes to Linear. Continue? [y/N] " \
    reply
  [ "$reply" = "y" ] || [ "$reply" = "Y" ]
}

step() { printf '\n=== %s ===\n' "$1"; }

confirm || {
  echo "Aborted; nothing was changed." >&2
  exit 2
}

step "1/4  Reset DB (wipe backlog + cursor state, reseed ATLAS)"
uv run python scripts/reset_db.py --yes

step "2/4  Plan (staged regeneration)"
uv run atlas plan --staged

step "3/4  Apply (review the diff, then confirm y/N)"
# Hold the gate open and read its exit code by hand: a rejection (1) or refusal
# (2) must stop the run BEFORE the Linear push, not crash through set -e mid-tree.
set +e
uv run atlas apply
apply_rc=$?
set -e
if [ "$apply_rc" -ne 0 ]; then
  echo >&2
  echo "atlas apply exited $apply_rc — plan not applied; skipping Linear sync." >&2
  echo "(1 = you rejected the diff; 2 = refusal, e.g. stale plan / dirty tree / no TTY.)" >&2
  exit "$apply_rc"
fi

step "4/4  PM sync (push fresh issues with full-spec descriptions)"
uv run atlas pm sync --once

step "Done"
echo "Board refreshed: Linear issues now carry the full-spec descriptions."

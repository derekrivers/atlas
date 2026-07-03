#!/usr/bin/env bash
# Smoke B — Phase 0 preconditions, as a script.
#
# Runs EVERY check and prints a findings summary (validator style: collect,
# report, never bail mid-sweep), exiting 0 only if all hard checks pass.
# Read-only throughout: nothing is written to the store, the board, or GitHub.
#
# Usage:
#   ./smoke-b-phase0.sh                  # fresh clone into a temp dir (default,
#                                        #   matches verify-against-the-server)
#   ./smoke-b-phase0.sh --here           # run in the current checkout instead
#   ./smoke-b-phase0.sh --app-json PATH  # also check the Symphony GitHub
#                                        #   plugin's .app.json for {"apps":{}}
#                                        #   (the elicitation-deadlock mitigation)
#   ./smoke-b-phase0.sh --allow-assignee # pass through to atlas preflight
#
# Env consumed (checked in 0.4, names from the code, not from memory):
#   LINEAR_API_KEY LINEAR_TEAM_ID LINEAR_PROJECT_ID GITHUB_TOKEN
#   ATLAS_OPERATOR_ID ANTHROPIC_API_KEY [LINEAR_ASSIGNEE]

set -u  # no set -e: we collect findings, we don't bail

REPO_URL="https://github.com/derekrivers/atlas"
HERE=0
APP_JSON=""
ALLOW_ASSIGNEE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --here) HERE=1 ;;
    --app-json) shift; APP_JSON="${1:?--app-json needs a path}" ;;
    --allow-assignee) ALLOW_ASSIGNEE="--allow-assignee" ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done

PASS=0; FAIL=0; SKIP=0
declare -a FINDINGS

finding() {  # finding <PASS|FAIL|SKIP> <id> <message>
  local status="$1" id="$2" msg="$3"
  FINDINGS+=("$status  $id  $msg")
  case "$status" in
    PASS) PASS=$((PASS+1)) ;;
    FAIL) FAIL=$((FAIL+1)) ;;
    SKIP) SKIP=$((SKIP+1)) ;;
  esac
  printf '%s  %-22s %s\n' "$status" "$id" "$msg"
}

# Every gate streams its output live (a hung step is VISIBLE, not silent),
# runs with stdin closed (a step that prompts fails instead of blocking), and
# is bounded by a hard timeout (default 600s; override per call).
GATE_LOG_DIR="$(mktemp -d /tmp/smoke-b-phase0-logs.XXXXXX)"
run_gate() {  # run_gate <id> <label> <timeout_s> <cmd...>
  local id="$1" label="$2" secs="$3"; shift 3
  local log="$GATE_LOG_DIR/$id.log"
  echo "--> $id: $label (timeout ${secs}s; streaming)"
  if timeout --foreground "$secs" "$@" < /dev/null 2>&1 | tee "$log"; then
    finding PASS "$id" "$label"
  else
    local rc=$?
    if [ "$rc" -eq 124 ]; then
      finding FAIL "$id" "$label — TIMED OUT after ${secs}s (hung step; log: $log)"
    else
      finding FAIL "$id" "$label — exit $rc (log: $log)"
    fi
  fi
}

echo "== Smoke B Phase 0 — preconditions sweep =="
echo

# --- workspace: fresh clone (default) or current checkout ------------------
if [ "$HERE" -eq 1 ]; then
  if [ -f pyproject.toml ] && grep -q '^name = "atlas"' pyproject.toml 2>/dev/null; then
    finding PASS "0.0-workspace" "running in current checkout: $(pwd)"
  else
    finding FAIL "0.0-workspace" "--here given but $(pwd) does not look like the atlas repo root"
    echo; echo "== ABORT: no workspace =="; exit 1
  fi
else
  WORK="$(mktemp -d /tmp/smoke-b-phase0.XXXXXX)"
  if git clone --depth 1 "$REPO_URL" "$WORK/atlas" -q 2>/dev/null; then
    cd "$WORK/atlas"
    finding PASS "0.0-workspace" "fresh clone of $REPO_URL at $(git log -1 --format='%h %s')"
  else
    finding FAIL "0.0-workspace" "clone of $REPO_URL failed"
    echo; echo "== ABORT: no workspace =="; exit 1
  fi
fi

# --- uv bootstrap ------------------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh -s -- -q >/dev/null 2>&1
  export PATH="$HOME/.local/bin:$PATH"
fi
if command -v uv >/dev/null 2>&1 && uv sync --quiet; then
  finding PASS "0.0-uv" "uv present, environment synced"
else
  finding FAIL "0.0-uv" "uv bootstrap or 'uv sync' failed — nothing below is meaningful"
  echo; echo "== ABORT: no environment =="; exit 1
fi

echo
# --- 0.1 baseline gate sweep (in order) -------------------------------------
run_gate "0.1-ruff-check"   "ruff check"                 120 uv run ruff check .
run_gate "0.1-ruff-format"  "ruff format --check"        120 uv run ruff format --check .
run_gate "0.1-mypy"         "mypy atlas tests (strict)"  600 uv run mypy atlas tests
run_gate "0.1-lint-imports" "import spine (KEPT)"        120 uv run lint-imports
run_gate "0.1-pytest"       "pytest"                     900 uv run pytest -q

echo
# --- 0.4 env (checked BEFORE preflight — preflight needs the Linear creds) --
need_env() {  # need_env <id> <var> <why>
  local id="$1" var="$2" why="$3"
  if [ -n "${!var:-}" ]; then
    finding PASS "$id" "$var set ($why)"
  else
    finding FAIL "$id" "$var UNSET ($why)"
  fi
}
need_env "0.4-linear-key"   LINEAR_API_KEY    "Linear client"
need_env "0.4-team-id"      LINEAR_TEAM_ID    "board scoping"
need_env "0.4-project-id"   LINEAR_PROJECT_ID "project scoping, ATLAS-135"
need_env "0.4-gh-token"     GITHUB_TOKEN      "evidence pull / verify / confirm"
need_env "0.4-operator-id"  ATLAS_OPERATOR_ID "human-tier confirm identity"
need_env "0.4-anthropic"    ANTHROPIC_API_KEY "planning client (Phase 1 plan/apply)"
if [ -n "${LINEAR_ASSIGNEE:-}" ] && [ -z "$ALLOW_ASSIGNEE" ]; then
  finding FAIL "0.4-assignee" "LINEAR_ASSIGNEE is set without --allow-assignee (narrows Symphony's poll; preflight will also flag this)"
else
  finding PASS "0.4-assignee" "assignee posture consistent"
fi

echo
# --- 0.2 operator preflight (only meaningful if Linear creds are present) ---
if [ -n "${LINEAR_API_KEY:-}" ]; then
  # NOTE: LinearGraphQLClient's urlopen carries NO timeout (known finding) —
  # the 180s bound here is what turns a stalled route to api.linear.app into a
  # clean FAIL instead of an indefinite hang.
  run_gate "0.2-preflight" "atlas preflight --check-model $ALLOW_ASSIGNEE" \
    180 uv run atlas preflight --check-model $ALLOW_ASSIGNEE
else
  finding SKIP "0.2-preflight" "skipped — LINEAR_API_KEY unset (fix 0.4 first)"
fi

echo
# --- 0.3 elicitation-deadlock mitigation (Symphony-side, path is env-specific)
if [ -n "$APP_JSON" ]; then
  if [ -f "$APP_JSON" ] && uv run python -c "
import json,sys
d=json.load(open('$APP_JSON'))
sys.exit(0 if d.get('apps')=={} else 1)
" 2>/dev/null; then
    finding PASS "0.3-app-json" "$APP_JSON carries {\"apps\":{}} — connector elicitation disarmed"
  else
    finding FAIL "0.3-app-json" "$APP_JSON missing or 'apps' != {} — a required:true connector prompt will DEADLOCK headless dispatch"
  fi
else
  finding SKIP "0.3-app-json" "not checked — pass --app-json <path to the GitHub plugin's .app.json> (manual check until C7 lands)"
fi

echo
echo "== Summary: $PASS pass, $FAIL fail, $SKIP skipped =="
if [ "$FAIL" -gt 0 ]; then
  echo "Phase 0 NOT clear — fix the FAIL findings before Phase 1. Aborting here is free."
  exit 1
fi
if [ "$SKIP" -gt 0 ]; then
  echo "Phase 0 clear on all hard checks; $SKIP check(s) skipped — clear them manually before dispatch (Phase 3)."
fi
echo "Phase 0 clear — proceed to Phase 1 (mint the fixture ticket)."
exit 0
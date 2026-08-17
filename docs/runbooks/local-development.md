# Local Development

Runbook for working on Atlas locally: installing the toolchain, calculating the
smallest safe local validation plan, and understanding the complete CI matrix.
Agents validate ticket requirements and affected surfaces for local confidence;
CI validates the complete repository at the accepted identity. Scoped local
results are agent-tier evidence and never prove repository completion.

## Toolchain

Atlas uses [`uv`](https://docs.astral.sh/uv/) for environment and dependency
management and targets Python >= 3.11. Install `uv`, then sync the locked
environment from the repository root:

```bash
uv sync --locked
```

`--locked` installs exactly what `uv.lock` pins — the same resolution CI uses.
Every command below runs through `uv run`, which executes inside that
environment; there is no virtualenv to activate by hand.

## Deterministic local validation plan

Supply `atlas validation-plan` with exact full Git object ids and every path in
the corresponding base-to-head diff. The CLI derives that diff with read-only
Git, includes both old and new rename paths, and compares it with the supplied
set; it never writes the repository. A mismatch or discovery failure requires
the complete local sweep. Add each registry-owned ticket requirement and
explicit ticket test from the ticket contract:

```bash
uv run atlas validation-plan \
  --base aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --head bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
  --changed-path atlas/verification/validation_plan.py \
  --changed-path tests/test_validation_plan.py \
  --ticket-requirement documentation \
  --ticket-test tests/test_validation_plan.py
```

Use `--json` for canonical compact output and
`--expect-registry-version validation-registry/v1` when the caller must prove
which policy it reviewed. Registered requirement ids are `python`, `static`,
`documentation`, `schema`, `generated-client`, `ui`, `browser`,
`workflow-contract` and `full-sweep`. Requirements and ticket tests are
additive; the CLI has no exclusion option.

Explicit ticket tests must be files at the supplied head and match the runner
that the selected profile invokes: `tests/**/test_*.py` for Pytest,
`apps/operator-ui/tests/acceptance/**/*.test.ts` for acceptance Vitest,
`apps/operator-ui/tests/component/**/*.test.tsx` for browser Vitest, or
Playwright's `.test`/`.spec` TypeScript forms under
`apps/operator-ui/tests/e2e/`. A test-looking path outside those contracts
cannot count as mandatory evidence and selects `full-sweep`.

Run the emitted commands in order. Changed test files and proven explicit
ticket test files appear in `test_targets` even when a broader profile command
contains them. Unknown or invalid paths, an omitted or mismatched diff, Git discovery
failure, an unprovable ticket test, ambiguous identities,
registry-version/digest drift, input over the documented bounds and protected
cross-cutting surfaces select `full-sweep` with explicit fallback reasons. A
caller must not replace that fallback with a narrower manual plan.

The named `full-sweep` profile is the conservative local path. Run it only when
the deterministic plan selects it or the operator explicitly instructs it; do
not add it after a passing scoped plan as a publication ritual. Every selected
command and explicit test target is mandatory. A failure prevents publication,
and any fix that changes the head requires a new exact-identity plan; the prior
plan and results become historical only.

## The gates

CI runs fourteen independent jobs on pull requests (the title job is omitted on
a push to `main`). These jobs remain unfiltered and authoritative regardless of
the local plan. The complete Python gates are:

```bash
uv run pytest                              # tests
uv run ruff check .                        # lint
uv run ruff format --check .               # formatting
uv run mypy atlas tests                    # type-checking
uv run python -m atlas.tools.doc_linter    # documentation rules
uv run lint-imports                        # architecture (import-linter)
```

Reproduce the Operator UI jobs:

```bash
cd apps/operator-ui
npm ci
./node_modules/.bin/playwright install chromium
npm run api:check                          # OpenAPI drift
npm run lint                               # ESLint
npm run typecheck                          # TypeScript project references
npm run test:acceptance                    # Vitest contract tests
npm run test:browser                       # Vitest browser-mode components
npm run build:bundle                       # Vite production bundle
npm run test:e2e                           # seed store, serve live API, e2e
npm run test:a11y                          # axe WCAG 2.2 AA, keyboard, viewports
```

The cold-checkout wrappers run the same UI commands in CI order:

```bash
./apps/operator-ui/scripts/ci.sh           # all non-e2e Operator UI stages
./apps/operator-ui/scripts/ci-e2e.sh       # seeded live-API e2e and a11y stages
```

The PR-only title gate runs `scripts/check_pr_title.py` against the proposed
title and merged title history; `.github/workflows/ci.yml` is the executable
invocation.

The deterministic planner selects from these same content-checking commands;
its `full-sweep` profile runs the unfiltered Python gates plus both Operator UI
wrappers. The PR-title gate remains event-scoped and CI authoritative. Two of
the content gates encode Atlas governance rather than ordinary correctness:

- **`doc_linter`** enforces the documentation contract — every canonical doc is
  registered in `docs/MANIFEST.md`, every referenced path and relative `.md`
  link resolves, ADRs match their model, and `docs/planning/` holds only
  machine-written renders. Hand-edited drift fails here.
- **`lint-imports`** makes the layer spine executable. Layers run high to low —
  `atlas.cli`, `atlas.planning`, `atlas.pm`, `atlas.dependencies`,
  `atlas.storage`, `atlas.linear`, `atlas.core` — and a lower layer importing a
  higher one is a hard failure. The canonical order is mirrored in
  `ARCHITECTURE.md`; the two must change together.

## pre-commit

The pre-commit hooks cover the Python static and governance gates: ruff-check,
ruff-format, mypy, doc-linter, and import-linter. They do not run pytest,
Operator UI, browser, or PR-title jobs. Install once, then let them run on each
commit, or run the whole set on demand:

```bash
uv run pre-commit install            # wire into git commit
uv run pre-commit run --all-files    # run every hook now
```

The omitted suites are deliberately too slow or require PR event data, so run
the applicable commands above before pushing.

## Database schema drift

Commands that perform expensive or stateful work check the store's stamped
Alembic revision before they call models, call Linear, or write. If the local
store is behind the code's migration head, Atlas exits with `SCHEMA_DRIFT` and
names both revisions. Fix it explicitly:

```bash
uv run alembic upgrade head
```

## Running tests

```bash
uv run pytest                                # the whole suite
uv run pytest tests/test_pm_follow_ups.py    # one file
uv run pytest -k follow_ups                  # by keyword
uv run pytest -k follow_ups -v               # verbose
```

`testpaths` is set to `tests/`, so a bare `uv run pytest` always means the full
suite.

## The shape of the suite

- **Unit and contract tests** (`tests/test_*.py`) are the bulk. Boundary
  contracts — for example, that the real Linear client and its in-memory fake
  satisfy the *same* behaviour — are pinned by shared contract runners, so the
  fake can never silently drift from the client it stands in for.
- **Property tests** use [Hypothesis] over generated inputs
  (`tests/model_strategies.py`) to check model invariants beyond hand-picked
  cases.
- **Acceptance tests** (`tests/test_acceptance.py`) pin milestone facts,
  including the hand-verified roadmap ticket count (the *enumeration pin*) and
  the AT-1..AT-7 acceptance criteria. Adding or removing a ticket line means
  updating the pin in the same commit.
- **Executable governance** (`tests/test_import_linter_contract.py`,
  `tests/test_doc_linter*.py`) tests the gate tooling itself, so the rules above
  cannot rot unnoticed.
- **Fakes and fixtures** (`tests/linear_fakes.py`, `tests/planner_fakes.py`,
  `tests/proposal_fixtures.py`, `tests/model_strategies.py`) are how you write a
  test against a boundary without touching the network — prefer them to any real
  call.

### Hypothesis profiles

Hypothesis is **derandomised by default** (the `atlas` profile: a fixed seed, 50
examples), so milestone properties are reproducible by construction — a flaky
milestone test is worse than none. For deliberate, deeper exploration, switch to
the randomised profile:

```bash
HYPOTHESIS_PROFILE=explore uv run pytest   # randomised, 200 examples
```

## Live tests (operator-run)

A handful of tests exercise the **real Linear API** — creating, reading, and
deleting throwaway issues; reading comments; moving workflow state. They are
**skipped in CI and skipped by default locally**, running only when you opt in
through environment variables. This is the [ADR-0008] evidence discipline in
practice: a deterministic ticket is complete on CI green, but a ticket that
mutates or reads real Linear needs operator-run, system-tier evidence that CI
cannot produce.

The base opt-in is four variables; individual live tests require one or two
more pointing at real workspace objects:

```bash
export ATLAS_LIVE_TESTS=1
export LINEAR_API_KEY='lin_api_...'
export LINEAR_TEAM_ID='...'
export LINEAR_PROJECT_ID='...'            # the project's id (UUID), not its slug
# test-specific, for example:
export LINEAR_FOLLOW_UP_ISSUE_ID='...'      # an issue carrying a tagged comment
export LINEAR_NEEDS_HUMAN_STATE_ID='...'    # the needs-human workflow state id

uv run pytest tests/test_linear_client.py -k live -v
```

Two cautions:

- **They mutate the live workspace.** Run them against a throwaway issue or a
  sandbox workspace, never production tickets, and clean up anything they leave
  behind (for instance an inbox stub written to your working tree — do not
  commit it).
- **`LINEAR_*_ID` values are the API ids (UUIDs)**, not the human `ATL-123`
  identifiers. An issue-not-found error is usually that mix-up.

## Phase 15.5 milestone replay (ATLAS-263)

The fixed comparison fixture is
`tests/fixtures/phase_15_5/milestone_v1.json`. Its identity, workload order,
`IND-1..IND-4` workload/plan/path/lane identities, virtual-clock inputs and
thresholds were recorded before measurement. Run the controlled comparison
without a live receipt to prove the comparison while retaining the required
live gate:

```bash
uv run python scripts/phase_15_5_milestone.py \
  tests/fixtures/phase_15_5/milestone_v1.json --pretty
```

Exit 3 means the controlled window passed but ATL-437 is correctly
`PENDING_LIVE_AUTHORITY`; it is not a failed controlled result and is not Phase
15.5 closure. The seeded live-delivery/fault exercise is deterministic test
evidence only:

```bash
uv run python scripts/phase_15_5_milestone.py \
  tests/fixtures/phase_15_5/milestone_v1.json \
  --live-receipt \
  tests/fixtures/phase_15_5/live_authority_seeded_pass.json --pretty
```

An actual bounded receipt for ATL-437 may be evaluated with the same
`--live-receipt` option only after publication of the remediated final head.
The receipt must pin
the PR/head, `CI Pending` observation, worker-stop timestamp, determinate CI
timestamp, reconciler tick/owner, exact transition sequence and absence of
Linear GitHub workflow state mutation. The publishing agent never runs that
post-publication check: it stops at `CI Pending`. The system reconciler and
operator attach the PR-linked receipt without changing the candidate head.
Seeded evidence never substitutes for that live window.

ATL-437's first published head is retained as a failed production-reachability
sample: CI completed, but the supported PM cadence never called the trusted
handoff service, so no genuine reconciliation row or Linear exit existed. The
remediated final head restarts the live window. After the agent has stopped at
`CI Pending` and normal system-tier evidence ingestion has populated the store,
the system/operator runs the supported adapter from that final code identity:

```bash
uv run atlas pm sync --once -v
```

`GITHUB_TOKEN`, the existing Linear credentials/state map and
`LINEAR_PROJECT_ID`/`LINEAR_TEAM_ID` are required production preconditions. The
initial complete pull may observe Linear already at `CI Pending` while the local
store still says `Ready for Agent`, `In Progress` or another Symphony-active
predecessor. This is supported poll-compression recovery: confirm the durable
transition records the actual direct edge and
`pm-engine:linear-poll-compression`, with no invented `PR Open` row. Do not
manually insert intermediate transitions or require an AgentRun before retrying
the normal one-shot path.

The verbose output must name one bounded `CI handoff adapter` result with exact
repository, PR and full head, classification, decision, reason and mutation
count. Console output is observability only: verify the append-only
`ci_handoff_reconciliations` row and corresponding Linear transition. A missing
or contradictory latest-episode identity holds before a GitHub or Linear call;
never repair that hold with a title guess, rollup inference or manual state
move. A second tick must not repeat a confirmed write.

## Before you publish and hand off

A branch is ready for publication when, from a clean `uv sync --locked`:

1. `git rev-parse --show-toplevel`, `git remote get-url origin` and
   `git symbolic-ref --quiet --short HEAD` prove the assigned workspace, exact
   repository and ticket branch, and the intended PR uses that same-repository
   head against `main`;
2. `git fetch origin main && git rebase origin/main` succeeds for this candidate
   publication before the validation plan is calculated;
3. its validation plan names the exact base and head and includes every changed
   path, ticket requirement and explicit ticket test;
4. every ordered command and explicit test target in the emitted plan passes,
   including the complete local sweep whenever `full-sweep` is selected;
5. the exact plan, commands and results are reported as agent-tier confidence,
   without claiming that scoped checks prove the complete repository result;
   and
6. for any change that reads or writes real Linear, the relevant live test has
   been run by hand and its result recorded on the PR ([ADR-0008]).

Publish that unchanged validated head once. In the Symphony workflow, move the
ticket through `PR Open` to `CI Pending` and stop in the same turn; do not poll
CI or wait for review. A failed selected local check never reaches publication.
After handoff, only the system-tier reconciler may move the exact head to
`Review Required` on complete required-check success or `Changes Requested` on
a definite implementation failure. The latter re-dispatches the preserved
workspace; infrastructure and ambiguous outcomes remain CI Pending without an
agent turn.

Complete CI at that exact candidate identity remains the system-tier authority
and runs every required repository job unchanged. Review Required admits the
operator acceptance sequence; final completion additionally requires the
accepted exact-head verdict, any required human approval, manual merge and
merged proof. No local profile removes or skips a CI gate, and a shorter local
run never weakens CI.

[Hypothesis]: https://hypothesis.readthedocs.io/
[ADR-0008]: ../decisions/0008-ci-sourced-evidence-with-trust-tiers.md

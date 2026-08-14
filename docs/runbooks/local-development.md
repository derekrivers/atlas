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
the corresponding base-to-head diff. The planner does not run Git or discover
paths itself. Add each registry-owned ticket requirement and explicit ticket
test from the ticket contract:

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

Run the emitted commands in order. Changed test files and explicit ticket test
files appear in `test_targets` even when a broader profile command contains
them. Unknown or invalid paths, an omitted diff, ambiguous identities,
registry-version/digest drift, input over the documented bounds and protected
cross-cutting surfaces select `full-sweep` with explicit fallback reasons. A
caller must not replace that fallback with a narrower manual plan.

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

## Before you push

A branch is ready for publication when, from a clean `uv sync --locked`:

1. its validation plan names the exact base and head and includes every changed
   path, ticket requirement and explicit ticket test;
2. every ordered command in the emitted plan passes, including the complete
   local sweep whenever fallback is mandatory;
3. the plan and results are reported as agent-tier confidence, without claiming
   that scoped checks prove the complete repository result; and
4. for any change that reads or writes real Linear, the relevant live test has
   been run by hand and its result recorded on the PR ([ADR-0008]).

After publication, complete CI at that exact candidate identity remains the
system-tier completion authority. It runs every required repository job; no
local profile removes or skips a CI gate.

[Hypothesis]: https://hypothesis.readthedocs.io/
[ADR-0008]: ../decisions/0008-ci-sourced-evidence-with-trust-tiers.md

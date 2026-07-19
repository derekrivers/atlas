# Runbook: Agent PR Acceptance

Destination: `docs/runbooks/pr-acceptance.md`. The operator's acceptance
protocol from the moment an agent PR lands to the moment the board says
Done. Gospel: deviations are findings, not improvisations. If it
conflicts with an ADR or the verification engine, the repository wins and
this document is fixed in the same session.

Companion to `review-doctrine.md` (what is reviewed) and
`reviewer-session.md` (how a reviewer session runs). This document is the
acceptance *sequence*. The reviewer recommends; only the operator
approves (ADR-0009).

## The spine

**review → evidence → confirm → verify → merge at the verdict commit →
board Done → sync.**

The single binding invariant: the verdict pins to the PR head commit, and
the merge decision must consume that same commit. Anything that moves the
head after evidence is pulled restarts the spine from evidence.

## 0. Preconditions

- PR title carries the ticket key in house form (`... (ATLAS-NN)` or a
  leading `ATLAS-NN`, or the `ATLAS-0NNM` meta form for doc-only
  landings); `lint-pr-title` enforces this. The key is the **ATLAS-NN in
  the issue title and context pack, never the ATL-N Linear board number**.
- CI green at the head commit. `unrecognised CI job … -> BUILD_RESULT`
  warnings for the workflow rollup and CodeQL are benign.
- Nothing merges to main underneath an open agent PR under acceptance.
  One PR through the spine at a time until concurrency is deliberately
  raised.

## 1. Review (reviewer-tier, not the gate)

Fresh-clone the PR head, review per `review-doctrine.md`, run the full
gate sweep locally (`ruff check`, `ruff format --check`, `mypy`,
`lint-imports`, `pytest` with `ATLAS_LIVE_TESTS=0`, `doc_linter`). Scope
rules bind: an unexpected file surface is flag-and-route; a reversal of a
recorded gate assumption must be recorded in the same change; a
cross-ticket reference introduced without a dependency edge is a finding.
Review is input to the operator, not the acceptance gate.

## 2. Freeze the head

No pushes, rebases, or GitHub "update branch" from here to merge. Note the
head SHA. If it moves, return to step 3 — never merge on a verdict pinned
to a superseded commit (the L-6 stale-verdict lesson).

## 3. Evidence

```
atlas evidence pull --pr <N> --repo <owner>/<repo>
```

Requires `GITHUB_TOKEN`. **`reviews: 0` is normal** — agent PRs are
authored under the operator's account, GitHub forbids self-approval, and
the loop does not use GitHub reviews as gate evidence. Re-running is
append-only.

## 4. Confirm (the human gate)

```
atlas confirm --pr <N> --repo <owner>/<repo> --operator <id>
```

Writes the human-tier `MANUAL_APPROVAL` and acceptance-criterion
confirmations the evaluators consume. This — not a GitHub review — is the
human gate. Confirm only what was verified in step 1.

## 5. Verify

```
atlas verify --pr <N> --repo <owner>/<repo>
```

Composes the verdict from stored evidence against the required-check
matrix for the ticket's type and risk. **Read the report, not the exit
code.** Non-PASSED routing: missing human-tier → redo confirm; failing
machine evidence → Changes Requested (the agent's resume re-runs CI on a
new head; spine restarts at step 3); scope/acceptance failure → operator
judgement.

## 6. Merge — at the verdict commit

Verdict PASSED → merge now, while the head is provably the verdict commit.
Squash mints a new SHA on main; acceptable because the verdict gated the
pre-merge decision at the verified head. A sibling change landing on main
between verdict and merge does not invalidate the verdict; a merge
*conflict* does — route it through Changes Requested so the agent rebases
under its contract, never hand-resolve on the agent's branch.

## 7. Done is a hand motion

**Drag the ticket to Done in Linear yourself.** No `atlas` command moves
it — status is operator-owned and one-directional (Linear → Atlas,
ADR-0006); `stateId` is not in the outbound payload. `atlas pm sync
--once` only *records* the transition after you drag it. This is the most
frequently mistaken step: sync does not move the card.

## 8. Migration parity — before the next sync

If the merged PR's diff touched `atlas/storage/migrations/versions/`, run
`uv run alembic upgrade head` before the next `atlas pm sync`. The store
is a different surface from the code; a merged migration is not an applied
one. Skipping this crashes the next tick with `OperationalError: no such
column …` (and, on write paths, discards LLM spend). ATLAS-174's guard now
fails these fast with a named error, but the discipline is still to
upgrade.

## 9. Silence discipline

One-shot commands report their result on stdout as of ATLAS-170/178, but
INFO logging is off by default. For any command: silence + expected board
state = success; any WARNING is the only voice the system has; never infer
success from silence alone — verify on the observable (board header,
`atlas pm report`, `atlas lessons show`). Use `-v` for INFO.

## Appendix — planning-side ordering (until the planning runbook carries it)

1. **Apply artifacts commit before the next sync.** `atlas apply` dirties
   the tree (renders + stub retirement); a sync against the dirty tree
   degrades every embed (ADR-0006 committed-only). Sequence: stub commit →
   plan → apply → **commit apply artifacts** → PR/merge → sync.
2. **Never reset an apply commit.** Apply advances three surfaces (repo,
   store, Linear); git rewind touches one. A packaging mistake is fixed by
   recovering or reconstructing the commit, never by retrying the apply —
   retry mints a duplicate key.
3. **The apply-commit message carries the real PlanRun UUID** — the
   provenance pointer from repo history to the store. Never a placeholder.
4. **A cross-ticket reference in an AC must be backed by a dependency
   edge.** Otherwise the scheduler can dispatch both tickets in parallel
   and duplicate-deliver (the ATLAS-102/104 class). Stub-minted tickets
   cannot currently declare edges — hold the dependent in Needs Human by
   hand until this is fixed.

## One-line rules earned by incident

- The verdict is the gate; review and CI are its inputs.
- The human gate is `atlas confirm`, never a GitHub review.
- `reviews: 0` is healthy; rollup/CodeQL `unrecognised CI job` warnings
  are noise.
- Freeze the head between evidence and merge; a moved head restarts the
  spine.
- Done is a hand motion; sync only records it.
- Upgrade the schema after a migration-carrying merge, before the sync.
- Needs Human is pull-invisible: repair and push passes cannot see a
  ticket parked there.
- Silence is not success; the observable is.

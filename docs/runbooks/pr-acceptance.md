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

**review → freeze → evidence → confirm → verify PASSED → merge at the
verdict commit → verify merged proof → schema upgrade → sync twice → Done.**

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
- Parallel development and review are allowed before step 2. From head freeze
  through merge, only one PR occupies the acceptance spine. A sibling merge
  makes every trailing PR rebase and restart at step 3.
- Read-only exact-head diagnostics are available with
  `uv run atlas pr status --pr <N> --repo <owner>/<repo>` (`--json` for the
  typed payload). The state vocabulary and exact definition live in
  `docs/atlas/symphony-integration.md#exact-head-pr-integration-assessment`;
  use that diagnostic before deciding whether a PR is current enough to enter
  the freeze.
- If a Review Required PR is mechanically stale (`behind`, `diverged`, or
  `conflicted`) before evidence is pulled, use the operator-owned lane rather
  than routing through a Symphony implementation cycle:
  `uv run atlas pr rebase prepare --pr <N> --repo <owner>/<repo>`. Resolve only
  the stopped worktree conflicts, then run
  `uv run atlas pr rebase continue --workspace <path>` until it reports
  `ready_to_publish`, and publish with
  `uv run atlas pr rebase publish --workspace <path>`. The lane refuses if the
  PR moved, `main` moved, the PR is no longer open/non-draft/same-repository, or
  the close-set tickets are not all `review_required`. Publish also verifies
  that local `origin` has exactly one push destination and that destination
  resolves to the same repository named by `--repo`, writes a durable
  `lease_push_pending` state before the force-with-lease boundary, and recovers
  that state on retry by comparing `origin` with the expected old and rebased
  heads. It pushes to the captured validated destination, writes a receipt under
  `.atlas/rebase-receipts/`, and leaves tickets in `review_required`.

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

If the head is already mechanically stale before this freeze, use the rebase
lane from step 0 first. Once evidence has been pulled, any head rewrite restarts
the spine from evidence; do not use GitHub Update branch during the frozen
interval.

## 3. Evidence

```
uv run atlas evidence pull --pr <N> --repo <owner>/<repo>
```

Requires `GITHUB_TOKEN`. **`reviews: 0` is normal** — agent PRs are
authored under the operator's account, GitHub forbids self-approval, and
the loop does not use GitHub reviews as gate evidence. Re-running an
unchanged source creates no duplicate evidence.

## 4. Confirm (the human gate)

```
uv run atlas confirm --pr <N> --repo <owner>/<repo> --operator <id>
```

Before writing confirmations, compare the live ticket criteria with the
criteria reviewed in step 1. Any wording or criterion-set drift restarts the
review; never confirm a remembered or superseded criterion. The command writes
the human-tier `MANUAL_APPROVAL` and acceptance-criterion confirmations the
evaluators consume. This — not a GitHub review — is the human gate.

## 5. Verify

```
uv run atlas verify --pr <N> --repo <owner>/<repo>
```

Composes the verdict from stored evidence against the required-check matrix
for the ticket's type and risk. **Only an explicit PASSED report opens the
merge gate; the command exit code does not.** Non-PASSED routing: missing
human-tier → redo confirm; failing machine evidence → Changes Requested (the
agent's resume re-runs CI on a new head; spine restarts at step 3);
scope/acceptance failure → operator judgement. Prefer the fail-closed driver:

```
uv run python scripts/close_ticket.py <N> --repo <owner>/<repo> \
  --operator <id>
```

## 6. Merge — at the verdict commit

Verdict PASSED → merge now, while the head is provably the verdict commit.
Squash mints a new SHA on main; acceptable because the verdict gated the
pre-merge decision at the verified head. No sibling lands during this
freeze-to-merge interval. A trailing Review Required PR that becomes stale
after the merge uses `atlas pr rebase prepare`, resolves conflicts only inside
the managed `.atlas/rebase-workspaces/` worktree, publishes through the explicit
old-head lease, and restarts its evidence spine; never hand-resolve a conflict
on the primary checkout or through GitHub Update branch. The rebase lane runs
with rerere and rerere autoupdate disabled, so every conflict stop remains an
operator decision even when the repository has remembered resolutions.

## 7. Record merged proof and verify again

```
uv run atlas verify --pr <N> --repo <owner>/<repo>
```

Run verification after GitHub reports the PR merged. This records the
system-tier `PR_MERGED` evidence at the verdict commit. Without that proof the
completion service must refuse Done.

## 8. Migration parity — before the next sync

After updating local `main`, run `uv run alembic upgrade head` before the next
`atlas pm sync` (unconditionally is safe). The store
is a different surface from the code; a merged migration is not an applied
one. Skipping this crashes the next tick with `OperationalError: no such
column …` (and, on write paths, discards LLM spend). ATLAS-174's guard now
fails these fast with a named error, but the discipline is still to
upgrade.

## 9. Gated completion and reconciliation

Run `uv run atlas pm sync --once -v` twice. The first tick may move the Linear
card from Review Required to Done only when the persisted verdict is PASSED and
the matching system-tier `PR_MERGED` evidence exists. The second tick pulls
that Linear state back into Atlas. Never drag a card to Done manually: doing so
bypasses the completion gate and creates an integrity anomaly.

## 10. Silence discipline

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
- Mechanically stale Review Required PRs use `atlas pr rebase`; the publish
  guard is the pinned old-head lease, not GitHub Update branch.
- Done is a gated system transition; never drag it manually.
- Upgrade the schema after a migration-carrying merge, before the sync.
- Needs Human is pull-invisible: repair and push passes cannot see a
  ticket parked there.
- Silence is not success; the observable is.

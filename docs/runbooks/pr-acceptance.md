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

The incoming state chain is explicit. The agent's deterministic local plan is
agent-tier confidence only; after one publication it moves the ticket to
`CI Pending` and stops. The system-tier reconciler alone consumes complete CI
for the exact published head and moves a pass to `Review Required` or a definite
implementation failure to `Changes Requested`. This runbook begins at Review
Required. Acceptance and final completion remain later claims: neither a
shorter valid local plan nor the CI handoff weakens or replaces any CI job,
human gate, exact-head check, manual merge or merged-proof requirement.

## The spine

**review → exact-head current → freeze → evidence → confirm → verify PASSED →
exact-head still current → merge at the verdict commit → verify merged proof →
schema upgrade → sync twice → Done.**

The single binding invariant: the verdict pins to the PR head commit, that
head must contain the exact current `main` snapshot admitted at the start of
the spine, and the merge decision must consume that same commit. Anything that
moves the head after evidence is pulled restarts the spine from evidence.

## 0. Preconditions

- PR title carries the ticket key in house form (`... (ATLAS-NN)` or a
  leading `ATLAS-NN`, or the `ATLAS-0NNM` meta form for doc-only
  landings); `lint-pr-title` enforces this. The key is the **ATLAS-NN in
  the issue title and context pack, never the ATL-N Linear board number**.
- The Linear card is `Review Required` because Atlas recorded complete
  current-head system-tier CI evidence and performed the fenced handoff from
  `CI Pending`. The agent never writes this transition. A green GitHub rollup
  by itself is not this authority.
  `unrecognised CI job … -> BUILD_RESULT` warnings for the workflow rollup and
  CodeQL are benign.
- Parallel development and review are allowed before step 2. From head freeze
  through merge, only one PR occupies the acceptance spine. A sibling merge
  makes every trailing PR use the Phase 12 rebase lane and restart at step 3.
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
- Ownership rule: agents keep ATLAS-168's current-main rebase discipline before
  PRs and every push, validate the resulting frozen head, publish once, and stop
  at `CI Pending`; the system-tier reconciler owns CI-pending exits. Operators
  use the Phase 12 lane for mechanical staleness after `Review Required`;
  `Changes Requested` is used only when implementation or other semantic
  remediation must return to Symphony.

## 0.5. CI-pending handoff

After publication, the agent moves `PR Open → CI Pending`, releases its working
slot and stops. Atlas, not the agent or browser, consumes the required
system-tier evidence at the exact PR head. A complete passed set moves the card
once to `Review Required`; a complete determinate implementation failure moves
it once to `Changes Requested`. Pending, missing, infrastructure, stale,
malformed or contradictory evidence leaves the card in `CI Pending` with a
typed reconciliation reason.

Do not drag a CI-pending card into review or rework and do not use GitHub's
rollup, a local command exit or an agent message as a substitute. If a Linear
write response is ambiguous, the durable fence requires a later complete board
observation to prove whether the source or target state won; that observation
does not retry the write. A changed head invalidates the recorded authority and
starts the evidence chain again for the new commit. The handoff considers only
evidence belonging to the ticket's product (and, when explicitly ticket-scoped,
that ticket). If newer same-head evidence changes the selected check results in
the final pre-write window, Atlas records `evidence_changed`, leaves the card in
`CI Pending` and lets the next tick classify the new set.

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

The fail-closed driver performs this exact-head assessment before pulling
evidence or prompting the operator. If the head is already mechanically stale
before this freeze, use the rebase lane from step 0 first. Once evidence has
been pulled, any head rewrite restarts the spine from evidence; do not use
GitHub Update branch during the frozen interval.

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
evaluators consume. If no decisions remain, it exits successfully and prints
`No outstanding confirmations ...`; do not retry that as missing work. The
summary separately counts passed/approved and failed/rejected records, and names
every skipped action as unresolved even when other decisions were recorded. An
empty close-set or an unknown-only close-set prints
`No confirmation assessment performed ...` and exits with the precondition code;
fix the close-set instead of treating that result as successful exhaustion. This
— not a GitHub review — is the human gate.

The governed acceptance-session form of this same gate is an application
action, not a second approval model. It accepts the session ID, pinned criteria
fingerprint, every stable integer criterion index exactly once and explicit
manual approval true. Stable indexes resolve through the server-owned canonical
snapshot (sorted ticket key, then stored list index); the caller cannot submit
criterion text, actor, repository, ticket key or head SHA. Missing, duplicate,
unknown or extra indexes, a mismatched fingerprint or false manual approval are
validation-only outcomes and persist nothing.

For a valid submission, Atlas locks the session, re-runs the shared exact-head
assessment and re-reads all close-set ticket definitions before the write.
Head, live `main`, close-set, ticket status or criteria movement makes the
session terminal stale and writes no confirmation. Otherwise the same domain
service and evidence writer used by `atlas confirm` append the per-criterion
confirmations and ticket-scoped `MANUAL_APPROVAL` records at the pinned head.
The records, `confirmations_ready` advance and action receipt are one atomic
commit; verification is still the separate next step. Same-key retries replay
the receipt, and concurrent or altered retries cannot append another set.

## 5. Verify

```
uv run atlas verify --pr <N> --repo <owner>/<repo>
```

Composes the verdict from stored evidence against the required-check matrix
for the ticket's type and risk. **Only an explicit PASSED report with a valid
`head_commit` can advance toward the merge gate; the command exit code does
not.** `atlas verify --json` includes a top-level `blocking_checks` list for the
ordered required checks that are not PASSED, including their check type, status,
typed reason, evidence IDs, ticket identity and exact `head_commit`. The
fail-closed driver consumes that structured payload; when a pre-merge verdict is
not PASSED, it names every blocking check before refusing the merge gate. It
then performs a second live exact-head assessment after the JSON verdict and
before showing the merge prompt. The live head must equal both the initial
exact-head snapshot and the verified `head_commit`, and the live base SHA,
branch identities, and repository identities must match the initial snapshot.
Any PR-head movement, `main` movement, eligibility change, compare failure, or
indeterminate mergeability blocks the merge prompt and restarts the spine at
step 3. Non-PASSED routing: missing human-tier → redo confirm; failing machine
evidence discovered during acceptance → operator-owned
`Review Required → Changes Requested` only when implementation remediation must
return to Symphony; mechanical staleness → Phase 12 rebase lane;
scope/acceptance failure → operator judgement. CI failures were already
classified at `CI Pending` only by the system-tier reconciler; the agent never
performs that classification. Prefer the fail-closed driver:

```
uv run python scripts/close_ticket.py <N> --repo <owner>/<repo> \
  --operator <id>
```

The governed acceptance-session form performs the same ordering without
calling that script or parsing CLI JSON. Its verification action is available
only after the session evidence and confirmation steps are complete. It runs a
fresh exact-head/criteria check, calls the canonical verifier in process, and
accepts only explicit top-level PASSED at the session head. It immediately
repeats the exact-head/criteria check and stores historical readiness, verified
head, verdict UUID, final assessment identity and the operator-action receipt
atomically. Every non-PASSED verdict and every identity, eligibility or criteria
mismatch is displayed as a typed blocker; a receipt/store failure is never
reported as success.

On every later console refresh, current authority comes from the bounded
read-only live-readiness service, not from the stored milestone. A moved head,
moved `main`, repository or eligibility change, criteria drift, timeout,
malformed GitHub response or other failed live read closes the displayed gate
without rewriting session history. Treat `merge_ready: true` only as advice to
perform the exact-head GitHub merge manually during the existing one-PR freeze.
The console action owner is synchronous and process-local: do not treat it as a
distributed lock or an asynchronous job. Phase 14's closure milestone proves
cross-tab ownership within the supported single process and proves that the
acceptance workflow itself performs no GitHub/Git, Linear, Symphony, schema or
PM-sync mutation.

## 6. Merge — at the verdict commit

Verdict PASSED plus the second exact-head assessment → merge now, while the
head is provably the verdict commit and contains the admitted `main` snapshot.
Squash mints a new SHA on main; acceptable because the verdict gated the
pre-merge decision at the verified head. No sibling lands during this
freeze-to-manual-merge interval. This final assessment does not eliminate the
residual race after the prompt and before the operator's manual GitHub merge;
the one-PR freeze remains binding. A trailing Review Required PR that becomes
stale after the merge uses `atlas pr rebase prepare`, resolves conflicts only
inside the managed `.atlas/rebase-workspaces/` worktree, publishes through the
explicit old-head lease, and restarts its evidence spine; never hand-resolve a
conflict on the primary checkout or through GitHub Update branch. The rebase
lane runs with rerere and rerere autoupdate disabled, so every conflict stop
remains an operator decision even when the repository has remembered
resolutions.

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
- Agent-tier local validation ends at CI Pending; the system-tier reconciler
  alone admits Review Required or requests implementation changes.
- The human gate is `atlas confirm`, never a GitHub review.
- `reviews: 0` is healthy; rollup/CodeQL `unrecognised CI job` warnings
  are noise.
- Freeze the head between evidence and manual merge; a moved head or moved
  `main` restarts the spine.
- Mechanically stale Review Required PRs use `atlas pr rebase` and remain
  `review_required`; the publish guard is the pinned old-head lease, not
  GitHub Update branch.
- Changes Requested is only for semantic remediation that must return to
  Symphony.
- Done is a gated system transition; never drag it manually.
- Upgrade the schema after a migration-carrying merge, before the sync.
- Needs Human is pull-invisible: repair and push passes cannot see a
  ticket parked there.
- Silence is not success; the observable is.

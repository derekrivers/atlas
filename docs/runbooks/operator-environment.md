# Operator Environment

Destination: `docs/runbooks/operator-environment.md`. The facts about the
operator's *local machine and accounts* that Atlas depends on but does not
control — credentials, the Codex/Symphony runtime, the database path. This
document exists because these facts were rediscovered by incident three
times over one session; each cost real time, and none had a home. If a
setup fact bites you and it is not here, that is the bug — add it.

This is operator-environment truth, not repository truth. It is deliberately
NOT a place for secrets: no token values, no keys, no ids that grant
access. It records *which* credential is needed and *where it is read
from*, never the credential itself.

## GitHub credentials — two independent channels

Atlas and its agents authenticate to GitHub through **different**
credentials, and confusing them is the single most expensive setup error
observed.

- **Atlas CLI** (`atlas evidence pull`, `verify`) reads `GITHUB_TOKEN`
  from the operator's environment (`atlas/github/client.py`). Keep it
  exported in the shell you run `atlas` from. Needs, on the repo:
  Contents: Read, Pull requests: Read, Checks: Read, Metadata: Read.
- **Agent sessions** (git push, PR ops) run Codex with
  `shell_environment_policy.inherit=core` (WORKFLOW.md), which **strips**
  the operator's exported `GITHUB_TOKEN`. The session therefore
  authenticates with the **on-disk git credential** — the `gho_` token
  written by `gh auth login` / `gh auth setup-git`, resolved by the git
  credential helper. Needs, on the repo: Contents: Read **and write**,
  Pull requests: Read and write.

Consequences to internalise:

- The two can hold different tokens with different scopes and diverge
  silently. An agent stranded an hour of verified work at a push 403
  because its on-disk token lacked write while the operator's env token
  had read (ATLAS-171 fixed the agent-side blind spot; the operator side
  has no hook — see below).
- `gh` prefers `GITHUB_TOKEN`/`GH_TOKEN` from the environment over its
  stored credential. So a read-only `GITHUB_TOKEN` exported in your
  interactive shell will **shadow** the write-capable `gho_` token for
  your own `git push`, producing the same 403 on the operator's machine.
  Fix: either grant the one fine-grained PAT both channels' scopes and use
  it everywhere, or do not export `GITHUB_TOKEN` in the shell you push
  from. Verify what a push will actually use with:
  `echo url=https://github.com | git credential fill` (run from inside the
  repo).
- Recovery when a push is denied mid-session: the work is not lost. Agent
  workspaces live under `~/code/atlas-workspaces/<ATL-key>/` (set by
  `workspace.root` in WORKFLOW.md, NOT the `/tmp` default;
  `before_remove: true` means they are never reaped). Find the commit and
  push it from your own shell:
  `for d in ~/code/atlas-workspaces/*/; do git -C "$d" cat-file -e <sha> 2>/dev/null && echo "$d"; done`

## Operator rebase workspaces

`atlas pr rebase` uses both credential channels. The GitHub reads for
assessment and post-push verification use the Atlas CLI `GITHUB_TOKEN`; the
local `git fetch`, `git rebase`, and lease-guarded `git push` use the git
credential helper for `origin`. A token that can read PRs but cannot push will
therefore prepare a workspace and fail only at publish, leaving the worktree
recoverable. Before the publish boundary, Atlas resolves
`git remote get-url --push --all origin` and refuses unless there is exactly
one push destination whose repository identity matches the rebase manifest;
this prevents a checkout with a fork or mirror as `origin` from rewriting the
wrong branch. The lease push uses that captured destination. The manifest
records the sanitized `origin` identity, not a token-bearing URL.

Operator Git config does not decide conflict resolution in this lane. Atlas
invokes both initial and continued rebases with `rerere.enabled=false` and
`rerere.autoupdate=false`, so remembered resolutions are not reused or staged
automatically.

The managed worktrees live under `.atlas/rebase-workspaces/` in this repository
and receipts live under `.atlas/rebase-receipts/`. The whole `.atlas/` root is
ignored by Git, so these files are local operational state, not PR content.
Never move a workspace by hand: `continue`, `publish`, and `abort` require the
canonical path to remain beneath that root and require the workspace manifest to
match the current repository. A successful publish removes the linked worktree
through Git only after a receipt exists; `lease_push_pending` and
`push_succeeded_unverified` workspaces are not abortable because the remote
branch may already have changed. Rerun `publish` to reconcile or verify those
states.

## Codex runtime

- WORKFLOW.md pins `model="gpt-5.5"`, which needs a current Codex CLI
  (verified on 0.142.5). The snap `codex` is capped at 0.114.0 and cannot
  run it; an npm-global update may land off PATH. Install the official CLI
  so it is first on PATH; verify the pin with `atlas preflight
  --check-model` before dispatch. (Full detail: bootstrap-guide.md.)
- **The Codex connector patch is version-pinned and self-expiring.** Any
  `.app.json` workaround under
  `~/.codex/plugins/cache/openai-curated-remote/<connector>/<version>/`
  applies only to that version's directory. A plugin version bump creates
  a fresh directory with a fresh unpatched `.app.json`, and the workaround
  silently ceases to apply. Re-apply it after any Codex plugin update, or
  expect the connector's required-approval gate to reappear. (Observed: a
  0.1.8 github-plugin bump ate an earlier patch.)
- **A foreign `atlas` binary can shadow the project on PATH.** If
  `atlas <subcommand>` prints a usage line offering flags Atlas does
  not have (`--git`, `--info`, `--init`), you are running someone
  else's tool. Always invoke through `uv run atlas ...` from the repo
  root — every runbook, agent prompt, and gate sweep already does.
  Identify the impostor with `which atlas`.

## Operator API writable mode

Writable API routes are off unless `atlas api serve --enable-writes` is used.
The existing read-only loopback API can still be started without an operator
token:

Do not export `ATLAS_API_ENABLE_WRITES` or `ATLAS_API_BIND_HOST` by hand. Those
variables are internal handoff state from the CLI to the imported API app, and
each `atlas api serve` invocation overwrites them from its actual flags before
launching Uvicorn.

```bash
uv run atlas api serve --host 127.0.0.1 --port 8000
```

Before enabling writes, set `ATLAS_OPERATOR_TOKEN` in the shell that launches
the API. The value is a local bootstrap credential, not repository state: do
not commit it, paste it into docs, put it in `VITE_` variables, or pass it in a
URL. Generate it with a cryptographic random source, for example:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

The server refuses writable startup if the token is absent, is outside the
43-to-512 printable ASCII character range, or fails the 128-bit estimated
entropy floor. Writable serving also refuses non-loopback bind hosts; remote
serving remains unsupported until a later HTTPS/Secure-cookie design lands.

For the supported browser topology, keep that writable API on
`127.0.0.1:8000` and start the UI's Vite server on loopback in another shell:

```bash
npm --prefix apps/operator-ui run dev
```

Open the Vite URL and let its same-origin `/api` proxy reach the API. Do not
open a direct cross-origin API URL, add CORS, expose either process remotely or
put the operator token in a `VITE_` variable. Loopback HTTP is deliberately the
only supported topology in Phase 13; it does not provide transport
confidentiality and the session cookie cannot use `Secure` until a later HTTPS
design is accepted.

The seeded acceptance command builds and drives this topology with a fresh
temporary store and deterministic test-only credentials:

```bash
npm --prefix apps/operator-ui run verify
```

The live browser suites exercise promote/reject, hostile requests, replay,
races, receipt failure, accessibility and responsive states. Their Playwright
configuration retains no screenshots, traces or videos. Never enable those
artifacts for a credential-bearing run unless a separately approved redaction
and retention design exists.

## Database

- The store is a single SQLite file at
  `~/projects/atlas/.atlas/atlas.db`. There is no `ATLAS_DB` override set
  by default; `--db` on a command targets a different file.
- **The CLI does not print which database it used.** A report and a raw
  `sqlite3` query can therefore silently address different assumptions
  about state — an anomaly count was chased across three wrong theories
  before it turned out a pasted report was simply stale relative to a
  re-query. When a number surprises you, regenerate the report against the
  known file before theorising; do not compare a remembered report to a
  live query.
- Editing a lesson before promotion (the design's edit-then-promote path)
  currently requires raw SQL against this file. It fails **silently** on a
  WHERE miss (SQLite updates zero rows without error — always follow an
  UPDATE with `SELECT changes();` and require `1`), and it **races the
  live process**: use a heredoc with `.timeout 5000` so the write waits
  for the lock instead of failing instantly. UUIDs are stored dashless in
  the WHERE clause; confirm the id with `SELECT quote(id) …` first. (A
  supported `atlas lessons edit` command is a carry-forward.)

## Minting: apply writes to two places

`atlas apply` writes the SQLite store AND the working tree. The store
is durable. The working-tree half — the four `docs/planning/` renders
plus the consumed stubs moved into `inbox/processed/` — exists only
until you commit it.

- **Never `git reset --hard` (or `git checkout -- .`, or switch
  branches discarding changes) after `atlas apply` until the mint is
  committed and pushed.** Doing so destroys the renders and the stub
  retirements while the store marches on with the minted tickets. The
  loop keeps working, because Symphony and the CLI read the store —
  so the divergence is silent until something reads the *committed*
  tree.
- Two things read the committed tree and will surface it, late and
  confusingly: `atlas plan --stubs-only` re-promotes any stub still
  sitting in `inbox/`, minting DUPLICATE tickets for delivered work;
  and the context-pack indexer resolves ticket `source_anchor`s
  against committed `processed/` stubs, so a pack render fails with
  `UnknownDocumentError` and the ticket is pushed to Symphony
  definition-only, without its context.
- The habit that prevents all of it: after `apply`, immediately
  `git add -A docs/planning/` (the `-A` matters — retired stubs land
  untracked in `processed/`), commit, and PR before running anything
  else. Reconciling later means a hand-authored stub-retirement PR,
  because the only regeneration path is another `apply`, which against
  an un-retired inbox re-mints.
- Symptom-to-cause: committed renders whose header
  `ticket_key_high_water` is lower than the highest key in the store
  means one or more mints were never committed.

## Board operation

- Status is operator-owned (ADR-0006). Dragging a card to Done is a manual
  act no `atlas` command performs; `atlas pm sync --once` records it after.
- A ticket in **Needs Human** is invisible to the dispatcher and to the
  sync's repair/push passes. Prefer the stub's `depends_on`
  front-matter to hold a dependent ticket: it names sibling stubs in
  the same batch (by filename) or existing keys, `atlas apply`
  materialises the edges, and `promote_ready` withholds the ticket
  until every blocker is Done. `Needs Human` remains the manual hold
  for cases no edge expresses.
- Follow-up inbox stubs (`docs/planning/inbox/<KEY>-<n>.md`) are written by
  the sync's comment scanner from `atlas:proposed-follow-up`-tagged Linear
  comments. They are untracked working-tree files; the next `atlas plan`
  consumes them. Operator-authored stubs are different: they must be
  COMMITTED before planning (ADR-0006 refuses a dirty or untracked
  input), so they ride a PR to `main` like any other content. Triage
  before planning, or they mint tickets unattended.
- **Meta labels are read from PR titles, not commit subjects.**
  Squash-merge takes the commit subject, so a PR titled
  `... (ATLAS-036M)` can land as a commit carrying no label at all.
  Reconstructing the meta ledger from `git log` therefore
  under-counts and collides. The PR title is authoritative.
- The acceptance chain for a merged PR is one command:
  `uv run python scripts/close_ticket.py <pr>` (ATLAS-040M). It pulls
  evidence, hands over to interactive `confirm`, pauses for the
  manual merge, independently verifies the merge with GitHub before
  running `verify`, ticks twice, and reports each ticket's status
  read from the store. Run it only after CI is green on the final
  head: evidence is commit-pinned, so updating a branch invalidates
  evidence pulled at the old SHA.

## Symphony ceiling controlled-ramp runbook

This is the operator procedure for the later Phase 15 live milestone. It is
not authorisation to run that milestone during an implementation ticket. Ten
is a proven maximum, never a target, and success never requires filling every
available slot.

### Authority, branch and gate record

The dedicated branch is exactly `phase-15-atlas-253-ceiling-ramp`. Create it
from the then-current `origin/main`, keep one draft milestone PR open for the
whole exercise, and never merge or cherry-pick an intermediate ceiling commit.
Ordinary committed `main` must continue to declare `max_concurrent_agents: 1`
and `max_turns: 10` until Gate 10 passes and the single Phase 15
milestone/closure PR is ready to merge. Only the operator may change the
milestone-branch declaration. Values 3, 5 and 7 are valid only on that branch
during this procedure and are never independently mergeable to `main`.

`WORKFLOW.md`'s `agent.max_concurrent_agents` is the single controlling
Symphony worker ceiling. The operator alone edits it. The delivery policy's
`approved_symphony_ceiling` is a recorded mirror for admission checks; working,
review and lane budgets remain independent limits, while occupied slots are
the actual Symphony session identities observed at the instant of capture.
Before each window the mirror must equal the declared branch value and every
working or lane budget must be at or below it.

Migration `0025` and policy revision one, whose historical ceiling is three,
remain immutable history and must not be cited as the current live policy.
Before the milestone branch is created or Gate 1 begins, the operator appends
and activates a new policy revision with `approved_symphony_ceiling=1` and
`working_budget=1`, with valid reserve and lane limits. The operator records
that revision and fingerprint and observes them alongside committed
`WORKFLOW.md` at one. Failure to prove this current-policy reconciliation stops
the milestone before any live ramp window.

No Atlas endpoint, CLI, agent or automation may edit `WORKFLOW.md`, Symphony
configuration, delivery policy, acceptance evidence or milestone receipts for
this procedure. Those remain operator actions in their owning systems. The
runbook uses only read observations and immutable identifiers from their
receipts. It never starts a live worker from CI.

For every PASS or FAIL, the operator posts one comment on the single milestone
PR. Its first line is `atlas:symphony-ceiling-gate v1`, followed by these
fields with no omissions:

```yaml
branch: phase-15-atlas-253-ceiling-ramp
head_sha: <40-hex commit loaded by Symphony>
workflow_blob_sha: <40-hex WORKFLOW.md blob>
level: <1|3|5|7|10>
previous_proven_level: <none|1|3|5|7|10>
declared_ceiling: <1|3|5|7|10>
max_turns: 10
policy_revision: <immutable revision>
policy_fingerprint: <fingerprint>
working_budget: <integer>
review_budget: <integer>
lane_budget_fingerprint: <fingerprint>
window_started_at: <UTC timestamp>
window_ended_at: <UTC timestamp exactly 60 minutes later>
pm_sync_receipt_ids: <ordered complete list>
admission_run_ids: <ordered complete list>
board_fingerprints: <ordered complete list>
symphony_session_ids_start_peak_end: <three explicit sets>
working_occupancy_start_peak_end: <three integers>
review_occupancy_start_peak_end: <three integers>
lane_occupancy_peaks: <risk/component mapping>
changes_requested_start_peak_end: <three integers>
acceptance_session_ids: <ordered list, empty only when not required>
acceptance_arrivals: <integer>
acceptance_exact_head_completions: <integer>
phase_14_closure_ref: <reference or not-required>
exercise_results: <named result mapping>
outcome: <PASS|FAIL>
stop_reasons: <ordered list, empty only for PASS>
retained_or_restored_level: <1|3|5|7|10>
rollback_head_sha: <40-hex commit or not-required>
active_session_ids_after_pause_or_lowering: <explicit set or not-exercised>
operator_identity: human/operator
recorded_at: <UTC timestamp>
```

The comment is the durable gate receipt; the successful Phase 15 closure
report references all five comment links. A screenshot, configured scalar,
aggregate count or agent assertion is not gate evidence. Raw Linear payloads,
exception text and credentials are never copied into the receipt.

### Exact edit and common preflight

Gate 1 observes the unchanged declaration. After a gate passes, the operator
changes only the scalar line in the branch front matter and commits it on the
same milestone branch:

```yaml
agent:
  max_concurrent_agents: <next-level>
```

The only permitted sequence is `1 -> 3`, `3 -> 5`, `5 -> 7`, then `7 -> 10`.
The prompt body below the front matter, `max_turns: 10` and every other workflow
field remain byte-for-byte unchanged. Before loading a level, the operator
verifies all of the following:

1. The checked-out branch name is exactly
   `phase-15-atlas-253-ceiling-ramp`; its head and `WORKFLOW.md` blob are
   recorded, and the milestone PR is still unmerged.
2. Current `origin/main` declares exactly one and keeps `max_turns: 10`. The
   branch declaration is the requested level, is at most ten and differs from
   the last proven declaration only by the one permitted scalar transition;
   Gate 1 starts from the unchanged value one.
3. Every prerequisite PASS receipt named below exists on the milestone PR and
   pins the immediately preceding level. No FAIL receipt remains unresolved.
4. The current active policy is the reconciled one-agent revision before Gate
   1. For later levels, the operator has paused new admission while changing
   the declaration and its policy mirror. The new immutable policy revision
   matches the declaration; its independent working/review/lane budgets and
   Changes Requested reserve validate.
5. A complete fresh board observation and successful PM-sync receipt exist;
   there is no unresolved admission write fence, partial pull, stale policy or
   indeterminate Linear result.
6. Symphony is explicitly loaded from the recorded branch head. The operator
   records actual active session identities before resuming admission; neither
   this check nor a lower ceiling claims to cancel them.

Any failed preflight is a Gate FAIL without starting the observation window.

### Observation window and common decision rule

Every level has one fixed 60-minute window. It starts at the first successful
complete PM-sync receipt after the matching branch head, policy revision and
running mode are all observed. It ends exactly 60 minutes later; the operator
captures a complete successful receipt at or after the end boundary and at
least twelve successful receipts spanning the window. A missing receipt,
required exercise or identity at the boundary fails the gate; the window is
not extended until the evidence looks favourable.

Collect every PM-sync receipt, admission run, policy/board fingerprint,
Symphony session observation and acceptance-session identity in the interval.
For all levels, PASS requires:

- the declared ceiling and policy mirror stay equal and unchanged;
- occupied Symphony slots never exceed the declaration, while working, review,
  reserve and every matching lane occupancy remain within their own budgets;
- a full review budget admits no new ticket, Changes Requested work is not
  starved, and each admission is reproduced from its pinned inputs;
- paused or draining mode produces no admission, and stale, partial or
  indeterminate input produces no unaccounted write; and
- all required level exercises pass with complete, internally consistent
  identities. Occupancy below the declared ceiling is acceptable.

Stop immediately and record FAIL for a ceiling/budget breach, an admission
while paused or draining, an unselected or second write, an unresolved
stale/partial/indeterminate result, missing or contradictory evidence, branch
or policy drift, or review occupancy equal to its budget in two consecutive
successful receipts. Pause admission at the stop boundary.

### Gate 1 — serialized baseline admission, pause and rework

Prerequisites are the controlled fixture of more than ten independent tickets,
the Phase 15 admission observability surfaces, green deterministic admission
and fault fixtures, and both `origin/main` and the unmodified milestone branch
declaring one. The current active policy reconciliation to one must already be
proved. Phase 14 closure is not required for this serialized baseline.

Within the window, reproduce a normal admission, prove both paused and draining
modes admit nobody, return to running through a new operator-attributed policy
revision, and prove the Changes Requested reserve keeps rework dispatchable.
A PASS receipt for all four exercises makes one the last proven level and is
the only authority to edit the branch from 1 to 3.

### Gate 3 — first controlled increase and review pressure

Gate 3 cannot begin without the Gate 1 PASS receipt and the exact `1 -> 3`
branch edit. Within the window, prove the first higher declaration remains a
maximum rather than a target, occupied Symphony slots never exceed three, a
full review budget stops new admission, and Changes Requested work remains
dispatchable within its reserve. Gate 3 PASS is the only authority to edit the
branch from 3 to 5.

### Gate 5 — stable review and stale-write protection

Gate 5 cannot begin without the Gate 3 PASS receipt and the exact `3 -> 5`
branch edit. Within the window, prove review occupancy never exceeds its
budget, a full review budget stops new admission, and the queue ends no higher
than it began. Inject one stale board-or-policy decision and one partial or
ambiguous Linear write result through the controlled live fixture: neither may
admit an unselected ticket or permit a second write, and the write fence must be
reconciled by a later complete pull. Gate 5 PASS is the only authority to edit
the branch from 5 to 7.

### Gate 7 — lanes, recovery and acceptance capacity

Gate 7 cannot begin without the Gate 5 PASS receipt, including its stable-review
and stale-write evidence, and the exact `5 -> 7` branch edit. Within the window,
prove risk/component lane limits hold and a Changes Requested ticket recovers
without starvation or an Atlas scheduling action.

Ten additionally requires Phase 14 to be closed before Gate 10 starts and
adequate exact-head acceptance throughput in the Gate 7 window. Adequate means
at least three distinct acceptance sessions reach passed verification and
manual-merge readiness without head drift, exact-head completions are at least
the number of review arrivals in the window, review occupancy never breaches
its budget, and ending review occupancy is no higher than starting occupancy.
The Gate 7 receipt must pin the Phase 14 closure reference and every acceptance
session identity. Only a PASS satisfying all of those conditions authorises
the `7 -> 10` edit.

### Gate 10 — maximum, not target, and closure

Gate 10 cannot begin without the Gate 7 PASS receipt, Phase 14 closure, the
adequate exact-head throughput proof and the exact `7 -> 10` branch edit. Run
the common exercises across the controlled wave of more than ten independent
tickets; actual concurrent occupancy need not reach ten. Gate 10 passes only
when the fixed window and all common conditions pass with complete evidence.

After PASS, the operator adds the five receipt links and exact ten declaration
to the Phase 15 closure report and makes the milestone/closure PR green at its
final exact head. That one PR may then be manually merged. Its resulting
`main` tree must contain the closure report and exactly
`max_concurrent_agents: 10`; a value below or above ten cannot close Phase 15.

### Stop, rollback and non-closure

On any preflight or window failure, the operator keeps the last proven value if
the next edit was not loaded, or changes only the scalar back to the last proven
value on the milestone branch. The operator also restores the policy mirror,
keeps admission paused, captures the post-action active session identities and
posts the FAIL receipt with the rollback commit. If occupied sessions exceed a
lowered declaration, they finish through Symphony's normal lifecycle; lowering
the branch ceiling and pausing Atlas admission do not terminate sessions,
cancel workers or delete workspaces.

The milestone PR stays unmerged, no ceiling commit is cherry-picked, ordinary
committed `main` remains at one and Phase 15 remains open. A receipt proving
only 1, 3, 5 or 7 is an honest incomplete milestone, never authority for
partial closure. A later retry starts a new fixed window from the
retained/restored proven value and must satisfy every subsequent gate again.

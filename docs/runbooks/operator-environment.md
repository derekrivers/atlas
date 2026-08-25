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
- Preserved agent workspaces live under
  `~/code/atlas-workspaces/<ATL-key>/` (set by `workspace.root` in
  `WORKFLOW.md`, not Symphony's `/tmp` default). The diagnostic and recovery
  sequence for a denied push belongs to
  `docs/runbooks/troubleshooting.md#git-push-or-publish-fails-with-403`.

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

- The sole current Symphony model authority is the executable
  `WORKFLOW.md` `codex.command`. Do not infer or copy its value from this
  facts document. Verify the live pin and installed CLI together with
  `uv run atlas preflight --check-model` before dispatch.
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

- The managed operational store is the single SQLite file at
  `/root/atlas/.atlas/atlas.db`. `ATLAS_DATABASE_URL`, `--db` and service
  configuration can target a different store, so establish the effective
  identity rather than inferring it from the checkout.
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

## Managed PM runtime

The production PM scheduler is the recurring `atlas-pm-sync.service`. Its
executable is an immutable accepted release beneath
`/root/atlas-runtime/pm-sync/<exact-git-sha>`; the mutable `/root/atlas`
checkout is not the long-lived service executable. The service and
`atlas pm sync --once` contend for the same nonblocking writer ownership on the
canonical SQLite file. A one-shot command normally refuses with `PM writer
already active` while the managed service is running; it is not a way to canary
or supplement the recurring daemon.

Deployment, migration, service activation, natural-cadence canary, deployment
receipt and rollback/incident boundaries are owned only by
`docs/runbooks/pm-runtime-deployment.md`. Acceptance does not perform those
operations. The local observational receipts live beneath
`.atlas/pm-runtime-deployments/`; they contain no secrets and grant no database,
admission, policy, ticket-state or rollback authority.

While PM is active, never unlink, replace, restore or recreate the canonical
database path. Writer ownership follows the opened inode. All backup, migration,
restore or replacement work requires the managed service stopped, no remaining
PM process and successful proof that ownership has been released.

## Planning and minting filesystem facts

`atlas apply` mutates both the selected Atlas store and the checkout's
`docs/planning/` renders/processed stubs. Those working-tree artifacts remain
ordinary uncommitted files until published, while the store mutation is already
durable. The repository's `.atlas/` operational root is ignored by Git.

The governed planning/minting sequence, recovery rules and exact commands belong
to `docs/runbooks/planning-phases-and-ticket-stubs.md` and
`docs/runbooks/running-atlas-plan.md`. Never hand-edit `docs/planning/`.

## Board operation

- Operator decisions remain operator-owned (ADR-0006), but verified completion
  is reconciled by the managed PM cadence. Never drag a card to Done manually;
  that bypasses the completion gate.
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
- Human acceptance begins only after `Review Required`; its exact-head,
  confirmation, manual-merge, merged-proof and read-only completion-observation
  sequence is owned by `docs/runbooks/pr-acceptance.md`.

## Symphony ceiling controlled-ramp runbook

The canonical operator procedure for `atlas-symphony.service` is
`docs/runbooks/symphony-runtime-operation.md`. It owns immutable workflow
materialisation, the supported release and procedure identity, service
reload/restart, process/runtime readback, the controlled ceiling sequence and
rollback.

Current supported facts remain:

- service unit: `atlas-symphony.service`;
- frozen supported Symphony release:
  `e5c5e48917e9e91ffb6709ab5a2a02c5af16bf02`;
- the running VPS must prove its process-owned runtime identity before any
  admission resumes;
- runtime proof pins the exact commit and workflow blob;
- integration/review budgets remain independent of the configured worker
  ceiling;
- ordinary committed-main ceiling while Phase 15 is open: `1`;
- `max_turns: 10`;
- strict operator-controlled ramp: `1 -> 3 -> 5 -> 7 -> 10`; and
- ATLAS-253 remains operator-paused; this documentation refactor authorises no
  Gate 1 execution.

The Phase 15 design owns the milestone criteria and evidence contract. The
runtime runbook owns only how the operator establishes the exact
process-owned runtime identity those gates require.

### Compatibility facts guarded by the repository linter

This is a non-procedural index retained for the existing ceiling-contract
linter. The commands and transitions below are not executable authority here;
their canonical sequence is the Symphony runtime runbook and their gate meaning
is the Phase 15 design.

- Dedicated branch: `phase-15-atlas-253-ceiling-ramp`.
- Historical retained receipt marker: `atlas:symphony-ceiling-gate v1`.
- Bounded identity fields include `origin_main_sha:` and `merge_base_sha:`.
- The only permitted sequence is `1 -> 3`, `3 -> 5`, `5 -> 7`, then `7 -> 10`.
- Validation uses `--symphony-milestone-level <1|3|5|7|10>`.
- Every level has one fixed 60-minute window.
- Only after that process-owned proof succeeds may policy activation proceed.
- Current `origin/main` declares exactly one and keeps `max_turns: 10`.
- Only the operator may change the milestone-branch declaration.
- Values 3, 5 and 7 are valid only on that branch and are never independently
  mergeable to `main`.
- Policy reconciliation uses the existing governed Phase 15 policy-revision
  boundary.
- Runtime identity is
  `vps-systemd-immutable-workflow-readback-v1` for
  `atlas-symphony.service` at release
  `e5c5e48917e9e91ffb6709ab5a2a02c5af16bf02`.
- Immutable materialisation uses
  `git show <gate-commit>:WORKFLOW.md > <immutable-workflow-file>`.
- Proof comes from the process-owned `/api/v1/runtime` response and its
  `workflow_content_sha256`.
- `fixture-only-no-live-runtime-v1` is for fixture/schema regression only and
  is never production runtime evidence.
- Rollback restores the previous proven gate's exact immutable workflow file.
- mainline progress alone does not force a Gate 1 restart.
- The current checkpoint is the proven Attempt-3 ceiling-one identity; future
  live proof uses the separately ratified v2 workload/receipt contract.
- The bounded validator's successful cumulative outcome is
  `RECEIPT_SEQUENCE_VALIDATED`.
The ramp adds no endpoint, CLI, agent action or automation that edits delivery
policy.
- No Atlas endpoint, CLI, agent or automation may edit `WORKFLOW.md`, Symphony
  configuration, acceptance evidence or milestone receipts.

### Gate 1 — serialized baseline admission, pause and rework

Gate 1 proves the unchanged runtime identity; it authorises no increase.

### Gate 3 — first controlled increase and review pressure

Gate 3 cannot begin without the Gate 1 PASS receipt.

### Gate 5 — stable review and stale-write protection

Gate 5 cannot begin without the Gate 3 PASS receipt.

### Gate 7 — lanes, recovery and acceptance capacity

Gate 7 cannot begin without the Gate 5 PASS receipt.

### Gate 10 — maximum, not target, and closure

Gate 10 cannot begin without the Gate 7 PASS receipt, Phase 14 closure and
adequate exact-head acceptance throughput.

### Stop, rollback and non-closure

The operator retains or restores the last proven runtime identity; the runtime
runbook owns the steps.

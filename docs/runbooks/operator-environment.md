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
`git remote get-url --push origin` and refuses unless that push URL identifies
the same repository named by the rebase manifest; this prevents a checkout with
a fork or mirror as `origin` from rewriting the wrong branch. The manifest
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

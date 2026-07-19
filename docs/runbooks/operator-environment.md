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

## Board operation

- Status is operator-owned (ADR-0006). Dragging a card to Done is a manual
  act no `atlas` command performs; `atlas pm sync --once` records it after.
- A ticket in **Needs Human** is invisible to the dispatcher and to the
  sync's repair/push passes. Use it deliberately to hold a dependent
  ticket until its prerequisite merges (there is no dependency-edge
  mechanism for stub-minted tickets yet).
- Follow-up inbox stubs (`docs/planning/inbox/<KEY>-<n>.md`) are written by
  the sync's comment scanner from `atlas:proposed-follow-up`-tagged Linear
  comments. They are untracked working-tree files; the next `atlas plan`
  consumes them. Triage before planning, or they mint tickets unattended.

# Symphony Integration Design

Status: Active design document for Phase 8. Written one phase ahead per the
phase-readiness rule; Phase 8 tickets must anchor to headings in this
document. Companion to ADR-0006 (field ownership), ADR-0007 (planning),
ADR-0008 (evidence).

## Boundary

Symphony is a scheduler and runner: it polls Linear, guarantees that every
dispatchable issue has an agent running in an isolated per-issue workspace,
and loads its behaviour from a repo-owned `WORKFLOW.md`. Per the Symphony
specification, the orchestrator reads the tracker; ticket writes (state
transitions, comments, PR links) are performed by the coding agent using
tools available in the workflow environment, and a successful run may end
at a handoff state rather than `Done`.

Atlas therefore does not orchestrate agents. Atlas decides *what* is
dispatchable (planning, dependencies, readiness), supplies *context*
(packs), and judges *outcomes* (evidence, verification, lessons). Symphony
owns everything between "ticket is ready" and "PR exists at a handoff
state".

```text
Atlas (plan, deps, ready, pack) ──sync──► Linear ──poll──► Symphony
                                                              │
Atlas (evidence, verification,  ◄──CI/PR/status────  agent in workspace
       failure analysis, lessons)
```

## State mapping

Atlas `TicketStatus` is the superset; Linear carries a projection of it;
Symphony sees only Linear states via its `active_states` /
`terminal_states` configuration.

| Atlas status        | Linear state      | Symphony classification    |
| ------------------- | ----------------- | -------------------------- |
| backlog             | Backlog           | not fetched                |
| planned             | Planned           | not fetched                |
| blocked             | Blocked           | not fetched                |
| ready_for_agent     | Ready for Agent   | active (dispatchable)      |
| in_progress         | In Progress       | active (running)           |
| pr_open             | PR Open           | active (running)           |
| review_required     | Review Required   | handoff — neither active nor terminal |
| changes_requested   | Changes Requested | active (re-dispatchable)   |
| needs_human_decision| Needs Human       | handoff — neither active nor terminal |
| done                | Done              | terminal                   |
| rejected            | Canceled          | terminal                   |

Decisions encoded here:

- **`Ready for Agent` is the only entry point to dispatch.** Atlas's PM
  Engine is the only writer that moves a ticket into it (readiness rule:
  dependencies done, criteria present, pack rendered).
- **Handoff states stop work without cleanup.** Per the Symphony spec's
  reconciliation rules, a state that is neither active nor terminal causes
  the worker to terminate while the workspace is preserved — exactly right
  for `Review Required` and `Needs Human`, where a human verdict may send
  the ticket back to `Changes Requested` and the agent resumes in the same
  workspace.
- **`Changes Requested` is active**, so review feedback re-dispatches
  automatically with workspace continuity.
- Symphony front matter (see Workflow contract):
  `active_states: [Ready for Agent, In Progress, PR Open, Changes Requested]`,
  `terminal_states: [Done, Canceled, Duplicate]`.

## Context pack delivery

**Decision: the rendered context pack is embedded in the Linear issue
description at sync time (delivered by ATLAS-164).** At every definition
push — create and update, all pushable statuses — the PM Engine writes the
issue description as:

```text
<definition description>

---
ATLAS CONTEXT PACK v1 | pack_id: <uuid> | rendered_at: <ts>
<rendered_markdown from the ContextPack>
```

Rationale: Symphony renders `issue.description` into the agent prompt via
the `WORKFLOW.md` template, so embedding requires no Atlas API, no network
access from the workspace, and no credentials beyond what Symphony already
holds. The `pack_id` and `rendered_at` travel in the header, so staleness
is visible at dispatch time and every agent run is attributable to an
exact render (`AgentRun.input_context_pack_id` needs the deferred
pack-persistence ticket — packs are transient today, so the embedded
`pack_id` references a pack no store yet holds).

Rules (the three ATLAS-164 gate rulings):

- **Refresh on definition change only (D-3).** The embedded pack re-renders
  exactly when the sync cursor re-pushes the definition
  (`updated_at > linear_synced_at`) — never per sync. Per-sync refresh
  already contradicts the merged ATLAS-148 request-budget pin (zero pushes
  on an untouched board), and unchanged-definition corpus detection would
  need persisted pack state plus O(affected-tickets) `update_issue` calls
  for a routine docs edit. Corpus staleness between definition pushes is
  therefore accepted: the window that matters (`Ready for Agent` →
  `In Progress`, frozen after dispatch) is short on a working board, and
  the header's `rendered_at` keeps the accepted staleness visible. Once
  `In Progress`, the description is frozen — re-rendering context under a
  running agent creates mid-flight scope drift. If freshness is wanted
  later, the persisted-pack + corpus-sha design goes through the
  pack-persistence ticket, not the sync tick.
- **Overflow truncates the pack with a visible marker (D-1).** When the
  composed description exceeds the pinned `EMBED_DESCRIPTION_LIMIT`
  (100,000 chars — roughly twice the ~48,000-char structural ceiling the
  pack builder's fail-closed 12,000-token budget implies, and well under
  the 250,000-character message-body cap Linear documents for
  email-created issues, the only size figure Linear publishes; no GraphQL
  description limit is documented), the PACK tail alone is truncated and a
  marker line names the truncation and the full render
  (`atlas context render <KEY>`). Definition fields are never cut.
  Everything stays inside the one owned description field. The
  previously-documented fallback — committing the full pack to
  `docs/planning/packs/<ticket-key>.md` — is rejected and must not
  return: it would add a second standing `docs/planning/` writer
  (ADR-0007), and an uncommitted `packs/<key>.md` is invisible to the
  dispatched agent's HEAD-reading workspace — the fallback doesn't merely
  cost an ADR-0007 exception, it fails to deliver the very milestone it
  exists to serve.
- **A render failure pushes definition-only, typed and logged (D-2).** On
  an enumerated typed failure (the token-budget raise, the
  ingestion/anchor errors, the retriever preconditions, a
  documents-loader failure) the push degrades to exactly the
  definition-only payload, one `PACK_RENDER_FAILURE` DebtItem is appended
  (naming the ticket key, the failure class, and the degradation), and
  the tick continues; a non-typed exception still crashes the tick loudly
  — fail-closed, never a blanket handler. The cursor is not stamped on
  the fallback, so `updated_at` stays ahead of `linear_synced_at` and the
  next tick retries the full embed once the render condition clears. The
  log and DebtItem summary name the ticket as cursor-unstamped, and
  `atlas pm report` counts still-unstamped `PACK_RENDER_FAILURE` tickets.
  Already-stamped historical victims whose Linear description lacks the
  `ATLAS CONTEXT PACK v1` header are repaired by the operator-invoked
  `atlas pm sync --repair-packs` sweep, not by the plain periodic tick.

## Workflow contract

Atlas ships the repo-owned `WORKFLOW.md` Symphony loads. Its front matter
carries the tracker config and state lists above; its body is the per-issue
prompt template. The prompt instructs the agent to:

1. Read the embedded Atlas context pack in the issue description and treat
   its constraints, non-goals, and definition of done as binding.
2. Move the issue to `In Progress` on start; open a PR referencing the
   ticket key; move to `PR Open`, then `Review Required` when CI is green
   — the agent performs all transitions, per the Symphony model.
3. Never mark its own work `Done`. `Done` requires Atlas verification
   (system-tier evidence per ADR-0008) plus any required human approval.
4. On blockers or ambiguity, comment on the issue and move it to
   `Needs Human` rather than improvising outside the pack's scope.
5. File follow-up observations as issue comments tagged `atlas:proposed-
   follow-up`; the PM Engine converts them into plan proposals (ADR-0007)
   — agents never create tickets directly.

### Mainline freshness discipline

Symphony's `hooks.before_run` fetches `origin/main` before every attempt,
including `Changes Requested` resumes. That keeps the local ref current while
leaving conflict resolution in the Atlas-owned contract body, where the agent
can apply judgement.

The contract requires the agent to run
`git fetch origin main && git rebase origin/main` immediately before opening
the PR, before every push, and before moving to `Review Required`. Conflicts
that touch only files inside the context pack's scope are resolved by the
agent and noted in the PR description. Any conflict touching a file outside
that scope is a blocker: the agent comments on Linear and moves the ticket to
`Needs Human`.

ADR-0008 fixes the ordering: rebase precedes push precedes CI, so
system-tier evidence pins to the final head that is current against
`origin/main` at handoff. The agent never rebases after entering
`Review Required`. If a sibling PR merges first and makes the verdict stale,
the operator either updates the branch directly (using GitHub's update-branch
control or an equivalent rebase and force-push), or routes the ticket through
`Changes Requested`. Either route changes the head commit, so evidence is
re-pulled against the new head before the acceptance chain continues; on a
`Changes Requested` resume, the agent rebases, pushes, and reruns CI on the new
head.

`hooks.after_create` performs a full clone, not `git clone --depth 1`. A
depth-1 clone can lack the merge base after a later fetch, which makes
`git rebase origin/main` fail fatally. The repository is small enough that the
full clone is the deterministic choice. The rejected alternative was to keep
`--depth 1` and fetch `--unshallow` in the sync step; that adds moving parts
with no current payoff. The recorded evidence trail for the motivating
conflict class is the Phase 8 closure report §5 carry-forward, "WORKFLOW:
rebase-onto-fresh-main-before-PR (the #188 conflict class)".

GitHub merge queue or auto-merge branch update is the platform-level answer if
agent-side rebasing stops scaling, but it is deferred from v1.
This workflow sets `max_concurrent_agents: 3` (ATLAS-041M); the
bound is review throughput rather than agent capacity, and raising
it further or configuring a merge queue remains a separate
operator decision.

### Exact-head PR integration assessment

`atlas pr status --pr <N> --repo <owner>/<repo>` is the shared read-only
answer to whether a pull request's exact head contains the exact current
`main` commit. The command fetches one PR snapshot, reads that snapshot's
`base.sha` and `head.sha`, and calls GitHub REST compare as
`GET /repos/{owner}/{repo}/compare/{base.sha}...{head.sha}`. Branch names,
local `origin/main`, and GitHub's approximate `mergeable_state` are not
freshness evidence.

The exact-head definition is intentionally narrower than "GitHub says this
can merge": a PR is `current` only when it is open, non-draft, same-repository,
targets the literal base ref `main`, the compare response has `behind_by == 0`,
the compare `merge_base_commit.sha` equals the PR snapshot's `base.sha`, and
`mergeable` is known not-conflicted. `mergeable: null`, a missing compare
field, contradictory compare counts, or a transport failure fails closed and
cannot yield `current`.

State vocabulary:

- `eligibility`: `eligible`, `merged`, `closed`, `draft`, `fork_head`, or
  `non_main`.
- `ancestry`: `current`, `behind`, `diverged`, or `indeterminate`.
- `mergeability`: `mergeable`, `conflicted`, or `indeterminate`.
- `integration_status`: `current`, `behind`, `diverged`, `conflicted`,
  `indeterminate`, or `ineligible`.

The assessment carries the repository, PR number/state/draft/merged flags,
head ref/SHA/repository, base ref/SHA/repository, compare status, ahead/behind
counts, merge-base SHA, and all derived statuses. `--json` emits the same typed
fields for automation. The command writes nothing: no Git fetch, branch update,
GitHub mutation, Atlas-store row, or Linear transition. Exit code is zero only
for `integration_status: current`; any rendered non-current, indeterminate, or
ineligible assessment exits non-zero. Setup and transport failures render a
single clean error line and no traceback.

### Operator-owned PR rebase lane

`atlas pr rebase` is the operator-owned lane for a mechanically stale PR after
handoff to `Review Required`. It is not a Symphony implementation resume and it
does not change Linear or Atlas ticket status. The lane starts only when
`prepare` reuses the exact-head assessment above and sees an open, non-draft,
same-repository PR targeting `main` with a determinate stale state:
`behind`, `diverged`, or `conflicted`. `current` is a named no-op;
`indeterminate` and ineligible PRs are named refusals. The PR title/body must
resolve to at least one existing `ATLAS-NN` ticket, and every ticket in that
close-set must already be `review_required`.

The command surface is:

```bash
uv run atlas pr rebase prepare --pr <N> --repo <owner>/<repo>
uv run atlas pr rebase continue --workspace <path>
uv run atlas pr rebase publish --workspace <path>
uv run atlas pr rebase abort --workspace <path>
```

`prepare` creates a detached linked worktree under
`.atlas/rebase-workspaces/` at the assessed original PR head, writes an atomic
versioned manifest, fetches the pinned base/head objects, and runs
`git -c rerere.enabled=false -c rerere.autoupdate=false rebase
<pinned-base-sha>` inside that worktree. The operator's primary checkout
branch, index, tracked files, and local branch refs are not checked out, reset,
or rewritten. A clean rebase records `ready_to_publish`. A conflict records
`conflicts_pending`, prints the exact
`git diff --name-only --diff-filter=U` paths, and leaves the stopped rebase for
the operator. `continue` refuses while unresolved entries remain, then runs the
same rerere-disabled `git rebase --continue` non-interactively after the
operator has staged the resolution; a later conflict records another conflict
set.

`publish` is the only remote-write boundary. Before pushing it refetches the
live PR snapshot, resolves `git remote get-url --push --all origin`, refuses
unless there is exactly one push destination whose repository identity matches
the manifest repo slug, and refetches remote `main`/head refs. It then requires
the live head SHA, base SHA, branch, repository identity, open state, draft
flag, local `origin` identity, and remote refs to match the manifest's pinned
values. Fresh mergeability is diagnostic only at this point: the identity and
exact SHA pins govern the safety gate. Immediately before the remote write, the
manifest records `lease_push_pending` with the expected old head, rebased head,
and sanitized `origin` repository identity. The push argv uses the captured
validated destination and the explicit expected-value lease form:

```bash
git push --force-with-lease=refs/heads/<branch>:<original-head-sha> \
  <validated-origin-push-url> <rebased-head-sha>:refs/heads/<branch>
```

Bare `--force`, implicit `--force-with-lease`, GitHub Update branch, merge
commits, fork PRs, and automatic conflict resolution remain out of scope. After
a successful push the manifest first records `push_succeeded_unverified`; bounded
GitHub refetches must then report the exact rebased head and the shared
assessment must report `current` with the pinned `main` SHA. If an interruption
leaves the manifest at `lease_push_pending`, rerunning `publish` reconciles
`origin`: old head means the lease push may proceed, rebased head is recovered
as `push_succeeded_unverified`, and any other head is refused without another
push. Only after verification is a receipt written under
`.atlas/rebase-receipts/` and the managed worktree removed. Rerunning `publish`
from `push_succeeded_unverified` verifies and cleans up without repeating the
old-head lease push.

`abort` accepts only a canonical path beneath `.atlas/rebase-workspaces/` whose
manifest matches this repository and the requested workspace. It aborts an
in-progress rebase when present and removes that named linked worktree through
Git. Traversal, symlink escape, missing or foreign manifests, the primary
worktree, `lease_push_pending`, any manifest recording a successful push, and
already-published receipts are refused without deletion.

### GitHub write-access probe

`hooks.before_run` also probes GitHub write access immediately after
`git fetch origin main`, before the agent receives the ticket prompt. The
probe runs inside the dispatched Codex session with that session's actual Git
credential path, so it tests the credential that will later publish the PR
branch. It uses `GIT_TERMINAL_PROMPT=0 git push --dry-run origin
"HEAD:${probe_ref}"` against a generated `refs/heads/atlas-write-access-probe-*`
destination. The dry run exercises GitHub's receive-pack permission path while
leaving no branch, tag, or other remote ref behind.

On failure, the hook exits non-zero, and Symphony aborts the attempt before
any implementation work starts. The diagnostic is intentionally short and
secret-free:

```text
Atlas before_run failed: GitHub write-access probe failed for <repo>.
Git output:
<git push --dry-run stderr/stdout>
This Codex session runs with shell_environment_policy.inherit=core, so the operator's exported GITHUB_TOKEN is not visible here.
The most likely cause is that the agent session's on-disk GitHub credential lacks write access for <repo>; the Git output above is the evidence for the exact failure.
The non-mutating GitHub write-access probe failed before agent work began; fix the agent session's credential path or repository access and dispatch again.
```

That wording names the credential boundary that matters for Atlas dispatch:
Atlas operator commands may read the operator's exported `GITHUB_TOKEN`, but
`WORKFLOW.md` starts Codex with `shell_environment_policy.inherit=core`, so
that exported token is stripped from the agent. The agent instead authenticates
Git operations with whatever on-disk Git credential helper is visible in its
session. An operator-side `atlas preflight` would therefore test the wrong
auth channel; the check has to live in `before_run`.

The motivating evidence trail is ATLAS-102 on 2026-07-16. The agent rebased
onto current `main`, resolved the duplicate-delivery conflict correctly, and
ran the full gate sweep green, but `git push --force-with-lease` failed with
HTTP 403 and the PR-body update via `gh` also returned 403. Verified work was
left only in the preserved workspace as commit `286dc9a` in
`~/code/atlas-workspaces/ATL-250`; recovery depended on
`workspace.before_remove: true` and the workspace root not being `/tmp`. The
failed handoff left work only in the workspace. The probe moves that failure
to the start of the attempt instead of discovering it after a completed
handoff.

## Ticket transitions: one writer per state edge

To prevent races between the PM Engine and agents:

- **Atlas PM Engine writes:** `backlog/planned/blocked → ready_for_agent`
  (readiness), `review_required → done` (post-verification),
  `review_required → changes_requested` (operator verdict relay), and any
  administrative archive/reject.
- **The agent writes:** `ready_for_agent → in_progress → pr_open →
  review_required`, `changes_requested → in_progress`, and any active state →
  `needs_human_decision` when the workflow requires a human gate.
- The PM Engine treats any observed transition outside this ownership as a
  reconciliation anomaly: it logs it, records a `DebtItem` if recurring,
  and never silently reverts a running agent's state.

## Retry and failure seam

**Symphony owns intra-ticket execution reliability:** session crashes,
stalls, turn continuation, exponential backoff, CI-shepherding within the
agent's own loop. Atlas never restarts agent sessions.

**Atlas owns ticket-level outcomes.** The PM Engine watches for: tickets
cycling `changes_requested ↔ pr_open` more than N times (default 3);
tickets dwelling in an active state beyond a configurable horizon; and
verification failures after handoff. Its responses are planning-level, not
execution-level: file a failure-analysis note, propose a ticket split
(oversized-ticket lesson), or route to `Needs Human`. Repeated failure
patterns become DRAFT lessons (ADR-0009).

`AgentRun` records are created by Atlas from observation — Linear activity,
PR metadata, and CI evidence — not by the agent reporting on itself,
consistent with the evidence trust model.

## Evidence flow

Unchanged from ADR-0008. Symphony and its agents produce **no evidence
records**. CI on the agent's PR produces system-tier evidence pinned to the
head commit; PR review outcomes are ingested as review evidence; the
Verification Engine gates `Done` on them. The agent's role is to make
evidence exist (push commits, keep CI green), never to assert it.

## Security posture

Per the Symphony spec, implementations must document their trust posture:

- The Linear API key lives with the Symphony service only. Agents perform
  tracker writes through the tooling the workflow exposes (e.g. a
  `linear_graphql`-style brokered tool or `gh` CLI for GitHub), never by
  reading raw tokens from disk.
- Workspaces receive no Atlas database credentials. Everything the agent
  needs from Atlas is in the embedded pack or the repo checkout.
- Workspace isolation, sandbox, and approval policy follow the Symphony
  defaults for a single-operator trusted environment; revisit alongside
  ADR-0009 before any hosted deployment.

## Open items (resolve before Phase 8 starts, not before Phase 5)

- ~~Linear description size limit in practice.~~ Resolved at the ATLAS-164
  gate: Linear publishes NO description size limit for the GraphQL API
  (developer docs, editor docs, and community reports searched; no
  observed 400 in this repo's live history). The only documented figure
  anywhere is the 250,000-character message-body cap for email-created
  issues — the reference point the 100,000-char pin sits well under. The
  overflow rule above (truncation-with-marker) encodes the finding.
- Exact follow-up comment schema for `atlas:proposed-follow-up`.
- Whether `Changes Requested` re-dispatch should require a fresh pack
  render (current position: no — same pack, feedback arrives via PR
  review comments the agent reads).

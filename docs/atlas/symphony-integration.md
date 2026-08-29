# Symphony Integration Design

Status: Delivered integration design for Phases 8, 12 and the Phase 15.5 scoped
validation handoff. Phase 8 established the agent handoff contract; Phase 12
delivered exact-head assessment, the operator-owned lease-guarded rebase lane
and the binding acceptance-freshness restart; Phase 15.5 moves CI observation
out of the agent session. These contracts remain authoritative until
superseded by a later canonical design. Companion to ADR-0006 (field
ownership), ADR-0007 (planning), ADR-0008 (evidence).

`post-review-release-orchestration.md` is that later target design for the
post-review boundary, but is not active runtime authority: this document's
state map, operator-owned rebase lane and manual-merge contract remain live
until the target lifecycle is separately implemented and activated.

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
owns everything between "ticket is ready" and "the published PR enters CI
Pending".

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
| ci_pending          | CI Pending        | handoff — neither active nor terminal |
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
  for `CI Pending`, `Review Required` and `Needs Human`, where a later Atlas or human verdict may send
  the ticket back to `Changes Requested` and the agent resumes in the same
  workspace.
- **`Changes Requested` is active**, so review feedback re-dispatches
  automatically with workspace continuity. The PM admission snapshot counts it
  as working occupancy before considering new tickets and preserves any
  configured Changes Requested reserve. Admission never demotes it, delays its
  re-dispatch with an Atlas status write, cancels its worker or consumes its
  workspace-lifecycle authority.
- **`CI Pending` is integration pressure, not working occupancy.** It is
  excluded from Symphony's active states, counted only against the
  operator-owned integration budget and held without cancelling or demoting
  existing work when that budget is lowered. Symphony can enter it only from
  `PR Open`; it cannot leave it, and the generic PM status pull refuses to treat
  a mapped exit as Atlas-owned CI evidence.
- **Admission is a single Linear state edge, not scheduling.** The periodic and
  one-shot PM sync paths share a database lease and may move at most one
  revalidated dependency-ready issue into `Ready for Agent`. A stale or
  transport-ambiguous decision writes no second issue; an unresolved write is
  fenced until a later complete Linear pull reconciles the selected issue.
  Symphony alone decides when an active issue runs and owns the workspace.
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

Atlas ships the repo-owned `WORKFLOW.md` Symphony loads. Its front matter owns
executable tracker states, hooks, worker limits and the Codex command. Its body
must establish the rendered issue/context identity and enough fail-closed
routing authority to require the detailed canonical lifecycle at
`docs/runbooks/symphony-agent-execution.md` before code or state mutation.

The executable spine preserves these architectural invariants: `CI Pending` is
not an active agent route; agents never merge or mark Done; one ticket uses one
branch and PR; every normal PR is issue-bound; Changes Requested input is
resolved before In Progress; and missing or conflicting detailed doctrine fails
closed. The execution runbook owns the commands, parsing, validation sequence,
publication readback, remediation resolution and same-PR rework procedure.

The agent owns `ready_for_agent → in_progress → pr_open → ci_pending` and no
CI-pending exit. The system-tier reconciler alone moves a passing exact head to
`Review Required` or a definite implementation failure to `Changes Requested`.
Because `CI Pending` is not active, the explicit transition ends Symphony
ownership without relying on silence or another turn.

### Issue-bound publication and remediation resume

Normal Symphony publication is correlated to the dispatched Linear issue, not
only to the Atlas key in its title. Every normal PR contains exactly one
standalone `Closes <issue.identifier>` line and publication readback
must prove the frozen same-repository head. Changes Requested preserves the
same workspace, branch and PR.

The architecture separates human semantic input, system-CI classification and
provider diagnostics. Human review uses the versioned
`atlas:remediation:v1` current-candidate envelope. Only the system-tier
`CI Pending → Changes Requested` transition is classification authority; a raw
provider failure does not independently prove Atlas
`IMPLEMENTATION_FAILURE`. Bounded diagnostics can guide repair only after
issue, publication and head correlation. This does not create a new state-edge
writer and does not alter the `CI Pending` stop contract. Exact trust predicates,
envelope parsing, failure routing and freeze semantics live in the execution
runbook.

### Mainline freshness discipline

`hooks.before_run` fetches `origin/main` before every attempt, including a
Changes Requested resume. The execution contract then requires
`git fetch origin main && git rebase origin/main` before candidate publication,
followed by validation of the frozen result. ADR-0008 fixes that order so
system-tier CI attaches to the head current against main at handoff. After CI
Pending, mechanical staleness belongs to the Phase 12 operator lane while
semantic remediation returns through Changes Requested; any head movement makes
old validation and acceptance evidence historical.

The full clone in `hooks.after_create` is deliberate: depth-1 clone can lack
the merge base after a later fetch. The Phase 8 closure report §5 records the
motivating “#188 conflict class”. GitHub merge queue remains the platform-scale
alternative and is deferred from v1. Detailed agent commands and conflict
routing belong to the execution runbook.

### Symphony ceiling ownership

`WORKFLOW.md.agent.max_concurrent_agents` is the single configured Symphony
worker ceiling. It is not a second ceiling, delivery-policy budget or
observed-slot count. The operator alone changes it. Ordinary committed `main` remains at one
while Phase 15 is open, `max_turns: 10` is outside the ramp, and intermediate
values 3, 5 and 7 exist only on the milestone branch.

Historical migration `0025` and policy revision one remain immutable; their
former value is not current runtime authority.

A repository edit is not runtime proof. The supported design requires an exact
immutable workflow materialisation, `atlas-symphony.service` restart, bounded
process identity and process-owned runtime readback before a coherent policy
mirror can be activated. The canonical operator commands, runtime receipt and
rollback sequence live in
`docs/runbooks/symphony-runtime-operation.md`. The milestone acceptance criteria
and evidence contract remain in `docs/atlas/multi-agent-delivery-control.md`.
Neither document grants agents or automation live-transition authority.

Atlas working/integration/review budgets, Changes Requested reserve,
risk/component limits and protected lanes remain independent admission limits.
Actual occupied slots are observed Symphony sessions. A budget or occupancy may
be below the configured ceiling; none is a target to fill.

### Exact-head PR integration assessment

`atlas pr status --pr <N> --repo <owner>/<repo>` is the shared read-only
answer to whether a pull request's exact head contains the exact current
`main` commit. The command fetches one PR snapshot for identity and exact head,
then independently resolves the current `main` branch head because a PR
payload's historical `base.sha` can remain pinned across sibling merges. It
calls GitHub REST compare as
`GET /repos/{owner}/{repo}/compare/{current-main-sha}...{head.sha}`. Local
`origin/main` and GitHub's approximate `mergeable_state` are not freshness
evidence.

The exact-head definition is intentionally narrower than "GitHub says this
can merge": a PR is `current` only when it is open, non-draft, same-repository,
targets the literal base ref `main`, the compare response has `behind_by == 0`,
the compare `merge_base_commit.sha` equals the resolved current-main SHA, and
`mergeable` is known not-conflicted. `mergeable: null`, a missing compare
field, contradictory compare counts, or a transport failure fails closed and
cannot yield `current`.

ATLAS-259 tested whether a clean GitHub synthetic merge could safely widen this
definition and recorded **FAIL**. The provider candidate commit/tree was exact,
but successful required Check Runs were pinned to the contributor head while
the candidate itself had no Check Runs. Symphony must therefore continue to
require contributor-branch ancestry of live `main`; it must not dispatch,
confirm or infer an `exact-base clean` exception from mergeability, a merge ref,
candidate tree equality, workflow logs or an unpinned branch ref. Head, base,
candidate, missing, conflict, malformed and indeterminate observations remain
non-authoritative.

ATLAS-260 subsequently recorded **FAIL** for a system-tier candidate
attestation. Its harness synthesized the candidate-to-job mapping and simulated
the external cryptographic verifier, so it did not prove a trusted producer/
signer lifecycle, GitHub OIDC provenance or independent exact-candidate job
execution. The current no-rewrite approach is retired. Symphony has no
candidate-attestation consumer or new state edge, this exact-head classifier is
unchanged, and the operator-owned rebase lane remains the only route for a head
that lacks live-`main` ancestry.

State vocabulary:

- `eligibility`: `eligible`, `merged`, `closed`, `draft`, `fork_head`, or
  `non_main`.
- `ancestry`: `current`, `behind`, `diverged`, or `indeterminate`.
- `mergeability`: `mergeable`, `conflicted`, or `indeterminate`.
- `integration_status`: `current`, `behind`, `diverged`, `conflicted`,
  `indeterminate`, or `ineligible`.

The assessment carries the repository, PR number/state/draft/merged flags,
head ref/SHA/repository, base ref/SHA/repository, compare status, ahead/behind
counts, merge-base SHA, and all derived statuses. Its base SHA source is
`live_branch` when the eligible assessment resolved the branch independently,
and `historical_pr_snapshot` when ineligibility stopped before that read;
consumers cannot mistake the latter for movement of live `main`. `--json` emits
the same typed fields for automation. The command writes nothing: no Git fetch,
branch update, GitHub mutation, Atlas-store row, or Linear transition. Exit
code is zero only for `integration_status: current`; any rendered non-current,
indeterminate, or ineligible assessment exits non-zero. Setup and transport
failures render a single clean error line and no traceback.

The fallback after the spike is the existing eligible stale route named by this
assessment:
`uv run atlas pr rebase prepare --pr <N> --repo <owner>/<repo>`. The operator
continues or aborts only inside that lane's managed worktree and publishes only
through its exact old-head lease. The new head restarts CI, confirmation and
acceptance; old candidate or old-head evidence never crosses the rewrite.

The canonical close driver uses the same assessment in process. Its initial
freshness assessment runs after local/token/operator preflight but before
`atlas evidence pull`, any Atlas write, confirmation, verification, or operator
prompt. Only `integration_status: current` enters the acceptance spine; stale,
conflicted, ineligible, indeterminate, or failed assessments exit before the
spine and name `atlas pr rebase prepare --pr <N> --repo <owner>/<repo>` when the
PR is eligible for the Phase 12 lane. After evidence, confirmation, and a
PASSED `verify --json`, the driver performs a second fresh assessment before
displaying the merge prompt. The live head must equal both the initial head and
the verified head, and the live base SHA, branch identities, and repository
identities must match the initial snapshot. Any movement restarts acceptance.

### Durable acceptance-session consumer

Phase 14's durable acceptance-session foundation consumes the exact same
in-process assessment; it does not call `atlas pr status`, parse CLI output or
reimplement the classifier. Creation accepts only an open, non-draft,
same-repository PR targeting literal `main` whose shared assessment is
`current`, then resolves the existing close-set parser and requires every live
ticket to be `review_required`. It snapshots the current stored acceptance
criteria in sorted ticket-key/index order before making the single session
insert. Caller or cached UI criterion text is never authoritative.

The session pins repository/PR, close-set, head and base refs/SHAs/repository
identities, structured assessment fields, criteria fingerprint and the
server-owned `human/operator` actor. Those identities cannot be updated.
There is one non-terminal session per repository/PR; head movement must first
make the old session terminal `stale`, after which creation records a new row
without retargeting or deleting the old row.

Creation-command replay is reserved for the same idempotency identity. A new
command colliding with an active session compares every pinned identity,
close-set, current ticket existence/status and criteria fingerprint. An
identical collision is refused as `active_session_exists`; any movement stales
the old row and returns the typed mismatches so a retry starts a new durable
lifecycle. Creation that discovers a missing or non-`review_required` ticket
also terminalises the previously active session before returning its typed
preflight refusal.

The shared pure freshness comparator reports all head, live-base, ref,
repository, eligibility, ticket existence/status and criteria mismatches. It
does not compare an ineligible assessment's `historical_pr_snapshot` base as a
live identity. A mutation that observes any mismatch atomically marks the
session stale. Read use is non-mutating: the stored-status projection labels
any persisted readiness `historical_only` and explicitly not current merge
authority. The later live-readiness service composes fresh bounded reads
separately; there is no polling or hidden refresh write.

Behind, diverged and conflicted creation preflight returns the existing exact
`atlas pr rebase prepare --pr <N> --repo <owner>/<repo>` operator recovery
command and creates no session. Draft, fork-head, non-main, merged, closed,
unknown and indeterminate cases remain distinct typed refusals. This foundation
adds no evidence, confirmation, verification, HTTP, GitHub/Linear write, Git
operation or merge path.

The Phase 14 verification consumer preserves the same ordering. It refuses
before the verifier unless evidence and confirmation summaries are complete and
a fresh shared assessment plus live criteria reproduce the pinned session. It
resolves changed files, performs another assessment to close the pre-verifier
race, and invokes the canonical `run_verify` service in process over the pinned
close-set. Only explicit top-level PASSED with a valid exact session
`head_commit` is admissible; CLI exit status, CI state and stored old-head checks
have no authority. A third assessment and live criteria read immediately after
PASSED must reproduce repository, PR, head/base refs and SHAs, eligibility,
integration and criteria identity.

Historical `merge_ready` is committed only with the verified head, verdict UUID,
final assessment identity, criteria fingerprint and operator-action receipt in
one transaction. The later `AcceptanceSessionLiveReadinessService` is the sole
GET-time authority: it validates that history, then performs one fresh shared
assessment and current criteria read without writing. Movement, indeterminate
state, timeout, malformed response or any other external-read failure returns
false with all typed reasons and never falls back to cached true. Neither the
action nor the read service runs Git, mutates GitHub or Linear, transitions a
ticket, upgrades schema or invokes PM sync; Atlas continues to advise a manual
GitHub merge only.

The Phase 14 closure milestone mechanically traps Symphony entry points along
with GitHub/Git, Linear, schema and PM-sync mutations while driving the built UI
against the live API and store. Successful readiness and every failure/race
case leave the traps empty. This is not a new Symphony command surface: the
one-action owner is synchronous and process-local, and the residual interval
from the last fresh GET to the operator's manual merge remains governed by the
one-PR freeze.

### Operator-owned PR rebase lane

Phase 12's `atlas pr rebase` lane handles a mechanically stale
`Review Required` PR without returning it to Symphony or changing ticket state.
It uses a managed worktree, rerere-disabled operator conflict resolution,
pinned old-head lease publication and exact post-push readback. Semantic
conflicts are not eligible for this lane. The architecture boundary is that the
operator owns this action and the new head restarts acceptance; the exact
procedure is owned by `docs/runbooks/pr-acceptance.md`.

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
  (readiness), `ci_pending → review_required` or `ci_pending →
  changes_requested` (system-tier CI classification), `review_required → done` (post-verification),
  `review_required → changes_requested` (operator verdict relay), and any
  administrative archive/reject.
- **The agent writes:** `ready_for_agent → in_progress → pr_open`, then only
  `pr_open → ci_pending` after the PR is published; it also owns
  `changes_requested → in_progress`, and any active state →
  `needs_human_decision` when the workflow requires a human gate.
- **No browser or Symphony exit writer exists.** Neither surface may write a
  `ci_pending` exit or relay one as though Atlas classified CI.
- The PM Engine treats any observed transition outside this ownership as a
  reconciliation anomaly: it logs it, records a `DebtItem` if recurring,
  and never silently reverts a running agent's state.

## Retry and failure seam

**Symphony owns intra-ticket execution reliability while a ticket is active:**
session crashes, stalls, turn continuation and exponential backoff. CI-pending
classification is Atlas-owned and non-active; Atlas never restarts agent
sessions.

**Atlas owns ticket-level outcomes.** The PM Engine watches for: tickets
cycling `changes_requested ↔ in_progress/pr_open` more than N times (default 3);
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
evidence possible by publishing the validated head, never to observe, reproduce
or assert CI. Local plan results are agent-tier confidence; the CI-pending
reconciler consumes system-tier CI; `Review Required` admits operator
acceptance; final verified and merged proof gates `Done`. The complete CI matrix
does not change when the local plan is shorter.

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

## Resolved design notes

- ~~Linear description size limit in practice.~~ Resolved at the ATLAS-164
  gate: Linear publishes NO description size limit for the GraphQL API
  (developer docs, editor docs, and community reports searched; no
  observed 400 in this repo's live history). The only documented figure
  anywhere is the 250,000-character message-body cap for email-created
  issues — the reference point the 100,000-char pin sits well under. The
  overflow rule above (truncation-with-marker) encodes the finding.
- Follow-up comments and `Changes Requested` input semantics are resolved.
  Their exact agent-facing contracts are owned by
  `docs/runbooks/symphony-agent-execution.md`; this document retains only the
  architectural state and authority boundaries.

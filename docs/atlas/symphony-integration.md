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
| rejected            | Cancelled         | terminal                   |

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
  `terminal_states: [Done, Cancelled, Canceled, Duplicate]`.

## Context pack delivery

**Decision: the rendered context pack is embedded in the Linear issue
description at sync time.** When the PM Engine moves a ticket to
`Ready for Agent`, it writes the issue description as:

```text
<human-readable summary>

---
ATLAS CONTEXT PACK v1 | pack_id: <uuid> | rendered_at: <ts>
<rendered_markdown from the ContextPack>
```

Rationale: Symphony renders `issue.description` into the agent prompt via
the `WORKFLOW.md` template, so embedding requires no Atlas API, no network
access from the workspace, and no credentials beyond what Symphony already
holds. The pack's `input_doc_shas` and `pack_id` travel with it, so
staleness is detectable and every agent run is attributable to an exact
pack (`AgentRun.input_context_pack_id`).

Rules:

- The PM Engine refreshes the embedded pack on every sync **while the
  ticket is in `Ready for Agent` only**. Once `In Progress`, the
  description is frozen — re-rendering context under a running agent
  creates mid-flight scope drift.
- If a rendered pack exceeds the Linear description limit, the PM Engine
  embeds the objective/constraints/criteria sections plus a repo path, and
  commits the full pack to `docs/planning/packs/<ticket-key>.md` for the
  workspace checkout to read. This is the fallback, not the default.

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

## Ticket transitions: one writer per state edge

To prevent races between the PM Engine and agents:

- **Atlas PM Engine writes:** `backlog/planned/blocked → ready_for_agent`
  (readiness), `review_required → done` (post-verification),
  `review_required → changes_requested` (operator verdict relay), and any
  administrative archive/reject.
- **The agent writes:** `ready_for_agent → in_progress → pr_open →
  review_required`, and `changes_requested → in_progress`.
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

- Linear description size limit in practice → validates the fallback rule.
- Exact follow-up comment schema for `atlas:proposed-follow-up`.
- Whether `Changes Requested` re-dispatch should require a fresh pack
  render (current position: no — same pack, feedback arrives via PR
  review comments the agent reads).

---
name: atlas-ticket-remediation
description: |
  Safely remediate an Atlas ticket already dispatched in Changes Requested.
  Use when Codex must resolve bounded Linear feedback, correlate the trusted
  current PR and contributor head, freeze the remediation set before coding,
  update the same PR, and return the candidate to the governed CI handoff.
---

# Remediate an Atlas ticket

Read the current canonical authority and follow it. This skill owns procedure
and navigation, not policy. If this skill conflicts with canonical repository
authority, the repository authority wins and this skill is defective.

## Authorities and composition

Read `docs/runbooks/symphony-agent-execution.md` at execution time. `WORKFLOW.md`
owns the executable dispatch spine, and `docs/atlas/symphony-integration.md`
owns state-edge and exact-head architecture.

Load and follow the `linear` skill for all bounded Linear reads, comments, and
state mutations. Load and follow the `atlas-validation` skill only after the
remediated candidate is frozen. Neither composed skill owns remediation policy.

## Resolve before changing state

1. Resolve the exact rendered Linear issue identifier and Atlas ticket key.
2. While the issue remains in `Changes Requested`, use the `linear` skill's
   bounded remediation-context query. Require the exact dispatched issue,
   complete bounded comment and attachment connections, and the unchanged
   state.
3. Resolve exactly one issue-bound trusted GitHub publication using the current
   predicate in the execution runbook and `linear` skill. Never infer it from a
   title, branch, comment, or remembered PR.
4. Read that PR once through the native authenticated `gh` CLI. Require the
   repository, number, same-repository branch, literal `main` base, current
   contributor head, and preserved workspace to agree.
5. Resolve human-review envelopes and/or the already-completed exact-head
   system-CI diagnostic exactly as the execution runbook permits. The Linear
   state transition supplies CI classification authority; raw GitHub failures
   are diagnostic only.

If identity, pagination, publication, envelope, diagnostic, workspace, or state
is missing, inconsistent, or ambiguous, post one bounded blocker comment with
the `linear` skill, move the issue to `Needs Human`, and stop.

## Freeze and remediate

Freeze a bounded remediation record containing the issue and ticket identities,
repository and PR, current contributor head, preserved workspace head, selected
comment/envelope IDs, selected completed-failure diagnostic IDs, and the exact
in-scope remediation text. Do not reread comments or CI during implementation.

Only after that record is frozen may the issue move from `Changes Requested` to
`In Progress`. Address only the frozen in-scope set. Do not create a replacement
PR, widen the ticket, classify CI, or use the operator rebase lane for semantic
remediation.

## Republish through the existing handoff

Follow the execution runbook for current-main rebase, conflict boundaries,
candidate freeze, and publication identity. Load and follow the
`atlas-validation` skill for the new exact base/head. A failed selected check
stays `In Progress`; a head change invalidates old inputs and results.

Push the same ticket branch, update the same PR, preserve or correct its exact
closing line, and read the publication back. Then move through `PR Open` to
`CI Pending` and stop in the same turn. Never poll CI, wait for review, merge,
mark Done, or claim system-tier authority.

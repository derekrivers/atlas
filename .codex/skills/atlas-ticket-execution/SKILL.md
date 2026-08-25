---
name: atlas-ticket-execution
description: |
  Execute an ordinary first-pass Atlas ticket dispatched by Symphony in Ready
  for Agent, In Progress, or PR Open. Use when Codex must implement the bounded
  ticket contract, validate and publish one exact candidate, and hand it off at
  CI Pending. Do not use for Changes Requested remediation.
---

# Execute an Atlas ticket

Read the current canonical authority and follow it. This skill owns procedure
and navigation, not policy. If this skill conflicts with canonical repository
authority, the repository authority wins and this skill is defective.

## Authorities and composition

Read `WORKFLOW.md`, `docs/runbooks/symphony-agent-execution.md`, and
`docs/runbooks/operational-practice.md` at execution time. Read
`docs/decisions/0008-ci-sourced-evidence-with-trust-tiers.md` for evidence
authority. The ticket description and its current Context Pack, when present,
remain the binding implementation scope.

Load and follow the `linear` skill for bounded Linear reads, comments, and state
mutations. Load and follow the `atlas-validation` skill only after the candidate
is frozen. Neither composed skill owns ticket scope or lifecycle policy, and
this skill does not handle `Changes Requested`.

## Establish the execution identity

1. Resolve the exact rendered Linear issue identifier and the `ATLAS-<n>` ticket
   key at the start of its rendered title. Read the full ticket description and
   current Context Pack when present.
2. Treat the ticket's acceptance criteria, non-goals, and definition of done as
   binding. Missing, incomplete, ambiguous, or identity-mismatched input fails
   closed through the execution runbook.
3. Establish the assigned repository root and workspace, origin URL, current
   branch and head, working-tree state, and fetched `origin/main` identity. Use
   only the assigned checkout and the ticket-specific branch based on current
   `origin/main`; never work on `main`, a detached head, or a sibling branch.
4. Handle every named design gap exactly as the execution runbook requires:
   comment with the bounded proposal, move to `Needs Human`, and stop until the
   operator returns a ratified resolution.

For a `Ready for Agent` entry, use `linear` to resolve the governed state UUID,
move to `In Progress`, create the ticket branch from the fetched exact
`origin/main`, verify its identities, and begin. An `In Progress` entry resumes
the bounded implementation. A `PR Open` entry performs only the publication
readback and handoff below.

## Implement and freeze one candidate

- Implement only the ticket contract. Do not widen scope for adjacent findings.
  Record out-of-scope work only with the execution runbook's canonical
  `atlas:proposed-follow-up` comment; never create the follow-up ticket.
- Immediately before the initial publication, run the exact current-main fetch
  and rebase required by the execution runbook. Resolve only in-scope conflicts.
  For any out-of-scope conflict, post the bounded blocker, move to `Needs Human`,
  and stop.
- Commit the final implementation after the successful rebase and freeze the
  exact base, head, and complete changed-path identity set. Any later head change
  invalidates the plan and results.
- Load and follow `atlas-validation` for that frozen candidate, including every
  ticket validation requirement and declared test. Do not independently select,
  replace, narrow, or supplement validation commands. A failed selected command
  or explicit test prevents publication and leaves the ticket `In Progress`.

## Publish and stop

After the exact frozen candidate passes its selected plan:

1. Recheck the repository, branch, base, and frozen head identities.
2. Publish that head once through the single ticket branch and single
   same-repository PR targeting `main`. Do not create a replacement PR.
3. Keep the Atlas ticket key at the start of the PR title and exactly one
   standalone PR-body relationship `Closes <issue.identifier>`, using the
   rendered Linear identifier rather than the Atlas key.
4. Read the publication back through the authenticated native `gh` CLI. Verify
   the exact repository, PR number, same-repository head branch, literal `main`
   base, frozen validated head, and exact closing-line set.
5. Record the exact base/head, changed paths, validation profiles and fallback
   reasons, commands and results, and explicit test results in the PR
   description or one handoff comment.
6. Use `linear` to move to `PR Open`. Verify the published frozen head once more,
   move to `CI Pending`, and stop in the same turn.

## Hard limits

Never poll, wait for, reproduce, or classify CI. Never author tickets, create a
follow-up ticket directly, mark the ticket Done, merge, accept, deploy PM,
mutate Symphony runtime, widen scope, or create a replacement PR. Do not perform
semantic `Changes Requested` remediation; that route belongs to the separate
remediation adapter. Local validation is agent-tier confidence only, and only
the system-tier reconciler owns the exit from `CI Pending`.

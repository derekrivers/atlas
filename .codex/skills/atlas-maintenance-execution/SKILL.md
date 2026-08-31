---
name: atlas-maintenance-execution
description: |
  Execute one hand-dispatched Atlas maintenance unit or coordinate a bounded
  maintenance campaign identified by a non-key ATLAS-NNNM meta-label. Use when
  Codex must parallelise read-heavy investigation or review with subagents,
  choose single- versus parallel-worktree implementation topology, enforce path
  ownership and dependencies, validate exact candidates, and publish maintenance
  PRs without entering the Symphony or Linear ticket lifecycle.
---

# Execute Atlas maintenance

Read the current canonical authority and follow it. This skill owns procedure
and navigation, not policy. If this skill conflicts with canonical repository
authority, the repository authority wins and this skill is defective.

## Authorities and distinction

Read `AGENTS.md`, `docs/MANIFEST.md`,
`docs/runbooks/operational-practice.md`, and
`docs/runbooks/agent-ticket-prompt.md` at execution time. The operator's
hand-dispatched maintenance contract is binding scope.

This is not `atlas-ticket-execution`. That skill remains the canonical-ticket
path dispatched by Symphony and owns its governed Linear lifecycle. An
`ATLAS-NNNM` maintenance meta-label is non-canonical, mints no ticket, creates
no ticket YAML, and grants no Linear mutation authority.

Compose `atlas-investigate` for fresh identity and collision evidence,
`atlas-validation` after freezing a candidate, and `atlas-pr-review` for
independent semantic review. Do not compose `linear`,
`atlas-ticket-remediation`, or `atlas-pr-acceptance`.

The governing distinction is: **subagents parallelise cognition; worktrees
parallelise mutation.** The primary agent owns decomposition, role selection,
scope, synthesis, contradiction resolution, dependency order, final write
authority in its checkout, validation, publication, and handoff.

## Phase A — Bootstrap

1. Establish the repository root, origin URL, branch, exact `HEAD`, working-tree
   state, and fetched `origin/main` identity.
2. Classify the work as hand-dispatched maintenance and record its exact
   `ATLAS-NNNM` non-key meta-label.
3. Prove the label does not collide with any remote branch or open or historical
   maintenance PR. Never infer freshness from the prompt.
4. Enumerate every open PR and its complete changed-path set before selecting
   mutable paths.
5. Classify the scope as one maintenance unit or a campaign of independently
   publishable units. Record explicit non-goals, including no Linear lifecycle.

Fail closed on an ambiguous repository, label, branch, PR, or path identity.

## Phase B — Parallel discovery

When at least two independent investigative dimensions exist:

1. Define two to four bounded read-heavy lanes.
2. Select the narrowest suitable custom role from `.codex/agents/`. Treat its
   `sandbox_mode` as a default, not proof of effective isolation: live parent
   permission overrides may widen a child. Record the effective runtime
   boundary and never claim read-only enforcement from the role file alone.
3. Give each child one question, explicit scope, prohibited actions, and an
   exact return format. Repeat the no-write contract even for a configured
   read-only role.
4. Spawn lanes in parallel where the runtime supports it. If the runtime cannot
   select a project custom-agent name, bind the child prompt to the checked-in
   role file and report that limitation. If the configured limit or current
   execution slots prevent full concurrency, use bounded waves and report the
   limitation; never claim a lane ran when it did not.
5. Perform no implementation edits while requested discovery lanes are active.
6. Wait for every requested child result.
7. Consolidate agreements, contradictions, missing evidence, proposed path
   ownership, dependencies, and risks.

Subagents advise; they do not vote. Resolve disagreement against canonical
Atlas authority and identify inference explicitly.

## Phase C — Implementation topology

Use a single worktree and one writer by default. Read-only subagents may
continue to explore or review.

Use a parallel-worktree campaign only when units have disjoint mutable path
sets, do not depend on one another's unmerged behavior, and can validate and
publish independently. Before starting writers, assign every unit:

- a unique maintenance meta-label and branch;
- an exact starting SHA;
- explicit owned and excluded paths;
- one primary writer; and
- declared dependencies and serialization edges.

Never allow multiple writers in one mutable checkout. If two units require the
same mutable path, serialize them. Optimistic merge-conflict resolution is not
a concurrency model.

## Phase D — Implementation

- Let the assigned primary worker make all edits in its checkout.
- Keep discovery and review subagents read-only. When their findings require an
  independent writer, start `atlas-maintenance-worker` as the sole writer in a
  separate isolated worktree with its own unit identity; do not promote a
  read-only specialist in place.
- Make no Linear mutation, ticket YAML, `atlas apply`, planning-render edit,
  production database mutation, or runtime/service operation unless the unit
  explicitly authorizes a distinct operational action.
- Do not widen scope because a child suggests adjacent work. Record it as
  deferred maintenance input only.

## Phase E — Validation

Load and follow `atlas-validation` as the sole validation authority.

1. Establish exact base and frozen head identities.
2. Enumerate the complete base-to-head changed-path identity set.
3. Supply every explicit maintenance-unit validation requirement through the
   CLI's `--ticket-requirement` inputs and every declared test through its
   `--ticket-test` inputs; the flag names do not turn the unit into a ticket.
4. Calculate the exact plan and run every selected command and explicit target.
5. Never narrow a selected profile or add an unselected full sweep as ritual.
6. Treat a failed selected check as blocking publication. Any head change
   invalidates the old plan and results.

Local results remain agent-tier confidence. Complete CI at a published exact
head remains system-tier authority.

## Phase F — Independent review

For meaningful changes, delegate bounded read-only review lanes where useful:
semantic correctness, tests and recovery, governance and documentation, and
safety and liveness. Apply `atlas-pr-review` to the exact maintenance contract
and candidate. Wait for every requested result. The primary writer synthesizes,
decides disposition, and makes any fixes.

Do not let a read-only reviewer modify the writer checkout. Seeded-defect probes
required by Atlas review must run only in a disposable isolated reviewer
checkout; when that is unavailable, report the unperformed probe explicitly
rather than fabricating evidence.

## Phase G — Publication and stop

Immediately before publication:

1. Recheck the repository, branch, exact head, label collision, and open-PR path
   ownership.
2. Fetch current `origin/main`. If it advanced, safely rebase or refresh only
   within the declared unit scope.
3. Freeze the resulting base/head and rerun `atlas-validation` for that exact
   candidate.
4. Publish one bounded maintenance PR targeting `main`.
5. Record the maintenance label, base and head SHAs, changed paths, validation
   plan and results, subagent roles and findings incorporated, runtime limits,
   and deferred work.

Do not fabricate a canonical Atlas ticket, add a closing relationship, mutate
Linear, merge, poll CI, accept, deploy, or begin the next maintenance campaign.
Stop after publication and hand the PR to the operator.

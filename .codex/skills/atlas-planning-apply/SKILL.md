---
name: atlas-planning-apply
description: |
  Execute Atlas's governed operator planning promotion and apply workflow after
  planning inputs are committed, reviewed, and ratified. Use when Codex must
  run plan --stubs-only, inspect the proposal, obtain explicit operator
  approval, apply it, and verify minted identities, dependencies, generated
  renders, and inbox retirement.
---

# Apply Atlas planning inputs

Read the current canonical authority and follow it. This skill owns procedure
and navigation, not policy. If this skill conflicts with canonical repository
authority, the repository authority wins and this skill is defective.

## Authorities

Read `docs/runbooks/running-atlas-plan.md`,
`docs/runbooks/planning-phases-and-ticket-stubs.md`, and
`docs/atlas/planning-engine-specification.md` at execution time. Read
`docs/decisions/0006-source-of-truth-hierarchy.md` and
`docs/decisions/0007-generative-planning-with-deterministic-reconciliation.md`
for render, key, proposal, and apply authority.

## Preconditions

Require the exact repository, committed ratified planning-input head, clean
planning input set, intended database identity, current schema, and Atlas
product row specified by the runbook. Do not continue from an uncommitted,
stale, partial, or unratified batch.

## Plan and inspect

1. Run `uv run atlas plan --stubs-only` against the intended repository and
   database.
2. Inspect the complete proposal and PlanRun identity. Require the expected ADD
   count to equal the approved stub count.
3. Verify titles, objectives, anchors, and dependency edges against the
   ratified batch.
4. Stop on any unexpected `MODIFY`, `PROPOSE_ARCHIVE`, `CONFLICT`, collapse,
   count, identity, provenance, or integrity result. Do not approve a surprising
   proposal merely because its input stubs were reviewed.

A generated proposal is not approval. Preserve it unchanged while the operator
reviews the exact diff.

## Explicit approval and apply

Only after the operator explicitly approves that exact proposal, run
`uv run atlas apply` and use the runbook's confirmation path. Never infer
approval from a previous batch, a generated proposal, silence, automation, or
this skill invocation.

Do not assign keys, mutate the store, or write planning renders by hand. The
Atlas CLI owns those deterministic operations.

## Verify the result

Immediately after apply:

- verify the PlanRun is applied with operator approval;
- verify the monotonic minted key range and high-water count;
- verify every resolved dependency edge against the approved DAG;
- verify `docs/planning/epics.yaml`, `tickets.yaml`, `dependencies.yaml`, and
  `roadmap.mmd` changed together as Atlas-owned renders;
- verify every consumed stub and its batch manifest moved from the active inbox
  to `docs/planning/inbox/processed/`; and
- inspect `git status --short` for the complete apply-owned planning diff and no
  unrelated path.

Stage and preserve the complete planning tree as the runbook requires. Do not
discard or partially commit apply artifacts after the store has advanced.

Linear publication is a later PM-owned boundary. A Linear sync failure is not
permission to rerun `atlas apply` or edit the store or renders surgically.

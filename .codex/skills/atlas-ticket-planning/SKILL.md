---
name: atlas-ticket-planning
description: |
  Turn a ratified Atlas phase or design into governed, dependency-aware planning
  inbox stubs and a planning-batch manifest. Use when Codex must decompose an
  approved capability into independently reviewable tickets, prepare planning
  inputs, or validate a batch before the operator-controlled mint/apply handoff.
---

# Plan Atlas tickets

Read the current canonical authority and follow it. This skill owns procedure
and navigation, not policy. If this skill conflicts with canonical repository
authority, the repository authority wins and this skill is defective.

## Authorities

Read `docs/MANIFEST.md`, the owning ratified phase/design document, and
`docs/runbooks/planning-phases-and-ticket-stubs.md`. Use
`docs/atlas/planning-engine-specification.md` for plan/apply and reconciliation
contracts and `docs/decisions/0007-generative-planning-with-deterministic-reconciliation.md`
for key and apply authority.

## Prove the design is ready

1. Identify the ratified design, phase boundary, milestone, and every material
   operator/programme decision it depends on.
2. Surface unresolved architectural or operator decisions. Do not bury them in
   implementation notes or let an execution ticket invent programme policy.
3. Stop if the design cannot anchor each ticket to a current canonical heading
   or if no reasonable decomposition resolves overlapping authority.

## Decompose the batch

- Give each ticket one coherent behavior or contract family and one genuine
  review/authority boundary.
- Keep schema, policy, runtime, state-transition, and protected-surface ownership
  non-overlapping.
- Express real technical prerequisites as dependencies. Preserve independent
  parallel lanes rather than serializing preferred human order.
- Hold milestone or release tickets behind the evidence that authorizes them.
- Make each ticket independently falsifiable, scoped, documented, and small
  enough to produce one stable candidate.
- Do not invent Atlas ticket keys. Existing dependencies may use existing keys;
  new sibling dependencies use the exact backward-only stub identities defined
  by the runbook.

Review the batch as a system: every intended capability appears exactly once,
the dependency graph is acyclic, independent lanes remain independent, and no
two tickets claim the same authority.

## Produce governed inputs

Create the ordered `docs/planning/inbox/inbox-stub-NN-<slug>.md` files and the
single `docs/planning/inbox/planning-batch-<slug>.yaml` manifest using the exact
schemas, path rules, base identity, ordering, and coverage contract in
`planning-phases-and-ticket-stubs.md`. Do not hand-edit `docs/planning/` renders.

Validate through the repository-owned surfaces that are available:

1. Run the packaged `validation/validate_phase_bundle.py` when working from an
   external governed handoff package.
2. Run `git diff --check` and review the complete manifest-listed diff.
3. After the planning inputs are committed, run
   `uv run python -m atlas.tools.doc_linter` against the committed corpus.

Atlas currently exposes the definitive batch-integrity guard through the
operator's `atlas plan --stubs-only` and `atlas apply` paths, not a separate
read-only batch-validation CLI. Do not invent a parser or broaden this workflow
to add one. If no packaged validator exists, report that unavailable validation
surface explicitly.

## Stop and hand off

The local planning agent stops after the validated planning-input commit. Do not
run `atlas plan`, `atlas apply`, implement a minted ticket, assign a key, mutate
the Atlas store, push, or open a PR unless a separate authority explicitly owns
that action.

Hand the committed, ratified inputs to the operator. The operator may then load
and follow the `atlas-planning-apply` skill.

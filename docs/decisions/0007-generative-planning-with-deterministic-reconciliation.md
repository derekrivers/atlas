# ADR-0007: Generative planning with deterministic reconciliation

## Status

Accepted

## Context

Generative planning is a hard requirement for milestone 1: `atlas plan` must
use an LLM to read the Atlas documents and produce epics, tickets, and
dependencies. Naive regeneration is unsafe: LLM output is non-deterministic,
so re-running the planner after a doc edit would churn ticket identity,
orphan in-flight work, and break any later Linear sync. The planner also
constitutes an agent writing to the planning layer, which ADR-0006 restricts.

## Decision

Planning is split into two commands with a deterministic layer between the
model and the backlog.

**`atlas plan` (propose).** The LLM reads the input documents and emits a
*proposal* — a structured, schema-validated candidate backlog. The proposal
is never written to `docs/planning/`. The planner:

- receives the current backlog in its context and is instructed to echo
  existing ticket keys where work is unchanged;
- must anchor every epic and ticket to a document section
  (`source_anchor: <doc path>#<heading slug>`);
- must not mint ticket keys; any new item is emitted with `key: null`.

**Deterministic reconciler.** Pure code (no model) converts the proposal
into a diff against the current backlog:

1. Match by echoed key.
2. Else match by `source_anchor` equality.
3. Else match by similarity of title + objective above a fixed threshold.
4. Unmatched proposal items become `ADD` (keys assigned monotonically by the
   reconciler, never the model).
5. Unmatched existing items become `PROPOSE_ARCHIVE` — never silent delete.
6. Tickets in `in_progress` or any later status are immutable to planning;
   diffs touching them are rejected.

**Validation gates** (all must pass before a diff is reviewable): schema
validity; dependency DAG acyclicity; every ticket has ≥1 acceptance
criterion and a valid `source_anchor`; all dependency targets exist; no
orphan epics; key integrity (no model-minted keys).

**`atlas apply` (commit).** Presents the human-readable diff; on operator
approval, writes `docs/planning/*.yaml`, assigns new keys, and records a
`PlanRun` (input doc SHAs, model/provider, prompt version, output hash, diff
summary, approver). Apply is the only legal writer of planning renders.

## Rationale

The gate on non-deterministic output is the diff review, not byte-identical
regeneration. Identity stability is guaranteed by construction because only
the reconciler assigns keys and only unchanged-or-approved deltas land. This
keeps the model responsible for semantic decomposition (the Harness-1
division: policy decides, environment bookkeeps) while the environment owns
identity, validation, and provenance.

## Consequences

- Milestone 1 acceptance tests change from "byte-identical output" to
  "empty reconciled diff on unchanged docs" (see the Planning Engine
  Specification).
- A `PlanRun` entity is added to the data model.
- Plan approval is the first human handoff state in the Atlas loop.
- Prompt templates become versioned artifacts referenced by `PlanRun`.

## Alternatives considered

- Deterministic parser of the hand-written roadmap: rejected as the
  milestone (generative planning is required), retained as a test fixture —
  the seeded roadmap is the reference corpus the planner is evaluated
  against.
- Free regeneration with content-hash IDs: rejected; hashes change whenever
  wording changes, which is exactly when identity must persist.
- Model-managed keys: rejected; identity is recoverable bookkeeping and
  belongs in the environment.

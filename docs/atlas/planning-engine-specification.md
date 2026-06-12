# Atlas Planning Engine Specification

Status: Active. Implements ADR-0006 and ADR-0007. Supersedes section 6 of
the system specification and section 4 of the technical architecture where
they conflict.

## 1. Purpose

The Planning Engine converts Atlas documents into a dependency-aware backlog
using an LLM proposer, a deterministic reconciler, mechanical validation
gates, and a human-approved apply step. The model makes semantic
decomposition decisions; the environment owns identity, validation, diffing,
and provenance.

```text
Documents ──► Proposer (LLM) ──► Proposal ──► Validation Gates
                                                   │
Current backlog ──────────► Reconciler ◄───────────┘
                                 │
                            Plan Diff ──► Operator review ──► atlas apply
                                                                  │
                                              docs/planning/*.yaml + PlanRun
```

## 2. Commands

### 2.1 `atlas plan`

1. Collect inputs: `PRODUCT.md`, `ARCHITECTURE.md`, `ROADMAP.md`,
   `WORKFLOW.md`, all accepted ADRs, all `docs/atlas/` documents, and
   `docs/domain/` documents if present. Record the git blob SHA of every
   input.
2. Load the current backlog from `docs/planning/` (empty on first run).
3. Render the planner prompt (versioned template) containing the documents,
   the current backlog, and the output schema.
4. Call the configured model with structured output. Parse into the
   Proposal schema; a parse failure fails the run.
5. Run validation gates (section 5).
6. Run the reconciler (section 4) to produce a Plan Diff.
7. Persist a `PlanRun` with `status: proposed` and print the diff.

`atlas plan` never writes to `docs/planning/`.

Inputs are read from HEAD: each document's content is the blob at its
recorded SHA, so content and SHA are consistent by construction. A
working tree that is dirty or carries untracked files within the input
set fails ingestion with a typed error — planning runs only against
committed state (ADR-0006); there is no untracked-file fallback.

### 2.2 `atlas apply`

1. Load the most recent `PlanRun` with `status: proposed`.
2. Refuse if fresh ingestion (which itself fails with a typed error on
   a dirty input set) yields input doc SHAs that no longer match the
   recorded `input_doc_shas` (stale plan).
3. Display the diff; require explicit operator confirmation.
4. Assign keys to `ADD` items (monotonic `ATLAS-n` from a persisted
   counter; the counter never reuses keys, including archived ones).
   Tickets take `ATLAS-<n>` and epics `ATLAS-E<n>`, each from its own
   monotonic counter; neither ever reuses a key.
5. Write `docs/planning/epics.yaml`, `tickets.yaml`, `dependencies.yaml`,
   and regenerate `docs/planning/roadmap.mmd` (Mermaid render of the
   dependency DAG).
6. Finalize the `PlanRun` to `status: applied` with `approved_by: operator`
   — the single permitted finalising transition (§6).

Rejecting a diff sets `status: rejected`. Apply is the only legal writer of
planning renders (ADR-0006); the doc linter flags out-of-band edits.

## 2.3 Anchor slug algorithm

`source_anchor` slugs are GitHub-style: heading text lowercased, characters
outside `[a-z0-9 -]` stripped, spaces to hyphens, duplicate headings within
a file suffixed `-1`, `-2`. The ingestion index (ATLAS-21) is the single
implementation; the doc linter and the renderer reuse it. Headings inside
fenced code blocks are not headings; the scan excludes them.

## 2.4 Diff presentation

`atlas plan` prints, and `PlanRun.diff_summary` stores, one block per entry:
type (ADD / MODIFY / PROPOSE_ARCHIVE / CONFLICT), key or `new:<n>`, title,
anchor, and for MODIFY a per-field before/after list. Summary line first:
counts per type. The key counter lives in the `key_counters` table
(knowledge-core.md#key-counter).

## 3. Proposal contract

The proposal shape — the envelope (`epics`, `tickets`, `dependencies`,
`planner_notes`), ProposalEpic, ProposalTicket, ProposalDependency,
reference forms, and required-field sets — is defined once, in
`data-model-and-schemas.md` §3.11 (Planning Proposal Contract); this
specification maintains no copy. Rules about gate behaviour rather than
shape:

- The model never invents keys (ADR-0007); key integrity is gate 6's.
- The reconciler resolves `new:<n>` and `new_epic:<n>` references after
  key assignment; the parser validates index bounds before the
  reconciler runs.
- JSON Schema for the proposal is generated from the Proposal Pydantic
  models (ATLAS-23). JSON examples in documentation are illustrative
  only; the models are the single contract.

## 4. Reconciler

Pure deterministic code. Matching passes, in order:

1. **Key match** — proposal item echoes an existing key.
2. **Anchor match** — equal `source_anchor` and same entity type.
3. **Similarity match** — normalised title + objective similarity ≥ the
   per-run threshold. The threshold is a per-run parameter with the spec
   default 0.85 (`DEFAULT_SIMILARITY_THRESHOLD`), supplied by the caller,
   fixed for the run, and recorded in `PlanRun.similarity_threshold` — no
   config file, environment variable, or settings layer. Normalisation:
   casefold; every non-alphanumeric character becomes a space;
   whitespace-split into a token set. Similarity is the Sørensen–Dice
   coefficient over the token sets of the concatenated title and
   objective — 2·|A∩B| / (|A| + |B|); two empty sets score 1.0.
4. **No match** — proposal item becomes `ADD`.

Existing items unmatched by any pass become `PROPOSE_ARCHIVE`. Nothing is
ever deleted.

Match ambiguity: duplicate echoed keys are a `CONFLICT` naming every
claimant. The anchor pass matches only unambiguous 1:1 (anchor, entity
type) pairs; ambiguous groups fall through to the similarity pass.
Similarity assignment is greedy by descending score under a total
deterministic order; an exact score tie competing for the same item is a
`CONFLICT` — never a silent arbitrary choice.

Matched items diff field-by-field into `MODIFY` entries (or no-ops).
Immutability rule: tickets with status `in_progress`, `pr_open`,
`review_required`, `changes_requested`, `done`, or `rejected` are frozen to
planning. Any `MODIFY` or `PROPOSE_ARCHIVE` touching them invalidates that
diff entry and is reported as a planner conflict for the operator.

Dependency edges reconcile by resolved (source, target) identity after
matching: proposal-only edges are `ADD`, backlog-only edges are
`PROPOSE_ARCHIVE` (archiving a ticket archives its edges as their own
explicit entries), and a changed `reason` is `MODIFY`. An edge entry
whose source ticket is frozen follows the immutability rule and becomes
`CONFLICT`; targeting a frozen ticket is permitted — new work may depend
on completed work.

Diff entry types: `ADD`, `MODIFY` (with per-field before/after),
`PROPOSE_ARCHIVE`, `CONFLICT`.

## 5. Validation gates

All gates must pass before a diff is presented. Failures fail the
`PlanRun` with a machine-readable reason.

1. Schema validity (Pydantic).
2. Dependency graph is a DAG (NetworkX acyclicity over the post-apply
   projected state).
3. All dependency targets resolve.
4. Every ticket has ≥1 acceptance criterion and a `source_anchor` that
   resolves to a real document heading at the recorded SHA.
5. No orphan epics (every epic has ≥1 ticket) and no epic-less tickets
   unless `ticket_type: tech_debt`.
6. Key integrity: no non-null keys absent from the current backlog.
7. Size guard: a ticket with more than 7 acceptance criteria or more than
   10 dependencies is rejected as oversized (per the seeded lesson that
   oversized tickets reduce agent success).

Attribution: per-item, context-free checks are enforced by the Proposal
models and fail as gate 1 (schema validity); gates 4 and 7 cover the
context-dependent remainder (anchor resolution; dependency count). A
violation fails in exactly one attributable place.

## 6. PlanRun schema

The `PlanRun` model and the `plan_runs` table are defined once, in
`data-model-and-schemas.md` §3.10; this specification deliberately
maintains no copy. A `PlanRun` row is inserted at `status: proposed`;
exactly one finalising transition to `applied`, `rejected`, or `failed` is
permitted, setting only `approved_by`, `applied_at`, and `failure_reason`.
All other fields are immutable after insert, and rows are never deleted.

## 7. Acceptance tests (milestone 1)

The milestone is met when all of the following pass against the seeded
Atlas documents:

- **AT-1 Validity.** `atlas plan` produces a proposal passing all gates;
  the projected backlog is an acyclic DAG and every ticket is traceable to
  a document anchor.
- **AT-2 Stability.** Running `atlas plan` twice on unchanged docs yields a
  reconciled diff that is empty or contains only `MODIFY` entries on
  free-text fields with similarity ≥ 0.95 — never key churn, never
  `ADD`/`PROPOSE_ARCHIVE` pairs for the same logical work.
- **AT-3 Locality.** Editing a single document section produces a diff
  whose `ADD`/`MODIFY` entries are anchored to that section, or are
  dependency edges adjacent to it.
- **AT-4 Immutability.** A diff touching an `in_progress` or `done` ticket
  surfaces as `CONFLICT` and `atlas apply` refuses it.
- **AT-5 Provenance.** Every apply produces a `PlanRun` whose
  `input_doc_shas` match the git tree, and `atlas apply` refuses a stale
  plan after a doc edit.
- **AT-6 Key authority.** No key in any applied backlog originates from the
  model; the key counter is monotonic across archives.
- **AT-7 Reference corpus.** Against the hand-written implementation
  roadmap, the planner's proposal covers ≥90% of hand-written tickets by
  anchor match (the roadmap is the evaluation fixture, per ADR-0007).

## 8. Non-goals for milestone 1

No Linear writes. No Symphony dispatch. No HTML roadmap (Mermaid render
only). No estimation logic (`estimated_effort` exists on the Ticket
model from Phase 1, remains null, and is populated when critical-path
analysis lands in Phase 3, ATLAS-32). No automatic re-planning triggers; planning runs only when
the operator invokes it.

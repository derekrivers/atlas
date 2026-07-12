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
2. Load the current backlog from operational state (the database; empty on
   first run). `docs/planning/` holds renders of that state (ADR-0006), not
   the source the reconciler reads.
3. Render the planner prompt (versioned template) containing the documents,
   the current backlog, and the output schema.
4. Call the configured model with structured output. Parse into the
   Proposal schema; a parse failure records a failed run (the failure
   contract below). The parser tolerates a surrounding markdown code
   fence or leading/trailing prose, extracting the embedded JSON object
   (a model may add a fence despite the template's "no surrounding text"
   instruction); `raw_output_hash` is over the true raw output regardless
   (the extraction is parse-time only, never on the hashed bytes), and
   genuinely non-JSON output still fails as a typed parse error.
5. Run validation gates (section 5).
6. Run the reconciler (section 4) to produce a Plan Diff.
7. Persist a `PlanRun` with `status: proposed` and print the diff.

`atlas plan` never writes to `docs/planning/`.

Failure contract. Failures split by whether the model's raw output exists.
Before it does — a dirty or untracked input set (a typed ingestion error), a
missing product, an empty input set, or a model-call failure — `atlas plan`
exits with a typed message and persists no `PlanRun`. Once raw output exists,
the outcome is recorded: a truncation (the model hit its output token limit,
`stop_reason: max_tokens`), a parse failure (gate 1), or any gate 2–7 failure
inserts a `PlanRun` at `proposed` and finalises it to `failed` (§6) with a
machine-readable `failure_reason`, preserving the full provenance chain
including `raw_output_hash` (over the partial output on truncation) — a failed
run is as auditable as a successful one. Only an all-gates-pass run remains at
`proposed` for `atlas apply`.

Output capacity boundary. A proposal is a single model response, so Milestone 1
plans corpora whose proposal fits the model's maximum output (64K tokens for
the pinned `claude-sonnet-4-6`; the call streams). A corpus large enough to
exceed it is truncated and recorded as a `failed` run with a specific
truncation `failure_reason` — distinct from a parse error, so the cause is
unambiguous — rather than misparsed as broken JSON. The committed Atlas corpus
already sits against this ceiling (one full proposal fits at ~95–97% of it; small
length variation truncates), so the boundary is resolved by design in
planning-large-corpora.md (ADR-0010): generation is split into bounded stages and
assembled into one full-state proposal before the reconciler. Until that lands
(ATLAS-103..107), single-call truncation is honest and named, not a silent
corruption.

Inputs are read from HEAD: each document's content is the blob at its
recorded SHA, so content and SHA are consistent by construction. A
working tree that is dirty or carries untracked files within the input
set fails ingestion with a typed error — planning runs only against
committed state (ADR-0006); there is no untracked-file fallback.

The committed follow-up inbox is read as a *separate* input source
alongside the corpus: its top-level stubs are merged into the planner
input — anchor index, document payload, and recorded `input_doc_shas` —
for visibility and provenance, but the inbox is not part of the §2.1
corpus globs and the `processed/` subdir is excluded from the document
payload (retired stubs are consumed follow-ups the planner never re-reads).
The `processed/` files DO feed anchor resolution and provenance (ATLAS-159):
their headings join the anchor index so a stub-minted ticket's durable
anchor keeps resolving at gate 4 after retirement, and their SHAs are
pinned in `input_doc_shas` so gate 4's "resolves at the recorded SHA" and
the AT-5 staleness re-check cover them. Each ACTIVE stub is additionally
indexed under its future `processed/` path (the same blob at its durable
address — retirement is a pure move), so an anchor minted against that
path resolves in the very run that mints it; the alias is index-only,
never pinned (the blob is already pinned at its real path). An active stub
sharing its basename with a retired one is a typed fail-closed error. The
inbox is committed-only on the same fail-closed contract (an uncommitted
stub is a typed dirty error), and an empty inbox is a no-op. The
producer/consumer mechanism is owned by pm-engine-and-linear-sync.md
"Follow-up ingestion".

**Deterministic promotion.** Beyond provenance, each committed inbox stub
is promoted to exactly one proposed `ADD` ticket by pure code — no model
call, no prompt (ADR-0005: the environment never infers a quantitative
value or makes a semantic judgement). Promotion runs after the model's
proposal is parsed and before the validation gates, so a promoted ticket
is validated and reconciled exactly like a model-emitted one and takes a
monotonic key at apply like any other `ADD`. Same stub + same backlog
yields a byte-identical injected ticket.

The stub declares its ticket in a **YAML front-matter block** at the top
of the file. The block carries the semantic fields — `title`, `objective`,
`context`, `ticket_type`, `epic_ref`, and the non-empty `acceptance_criteria`
(≤ 7), `non_goals`, `test_requirements`, `definition_of_done` lists. The
promoter supplies only mechanical defaults: `source_anchor` defaults to the
stub's own first heading at its durable `inbox/processed/` path — the
address apply's retirement gives the file, known at promotion time — so
the anchor resolves at gate 4 from birth (the active stub is indexed at
both addresses, ATLAS-159) and never dangles when its own apply retires
the stub, `relevant_docs` to `[<stub path>]`, and `tags` /
`component` / `implementation_notes` / `documentation_requirements` to their
empty forms. `priority` and `risk_level` are taken from the front-matter if
present, else the single pinned constants `priority = 50` and
`risk_level = low` — a fixed mechanical write, never an inferred per-stub
value. `epic_ref` names the epic the ticket belongs to; when it is an
existing backlog epic key that the parsed proposal omitted (or emitted
keyless), the epic is re-stated verbatim from the backlog into the proposal
so the promotion is self-contained rather than dependent on the model
re-emitting the epic with its key. An `epic_ref` naming an epic that is in
neither the proposal nor the backlog is a typed failure, not a silent drop.

The optional `depends_on` list (ATLAS-153) declares the promoted ticket's
dependency edges deterministically; promotion turns each entry into one
`depends_on` edge with a single pinned mechanical reason (never inferred
per-stub, ADR-0005). Each entry is an **existing ticket key** — an edge
from the new ticket to that ticket; an unknown key fails gate 3 as a typed
`GATE3_UNRESOLVED_TARGET` — or a **sibling stub filename in the same batch**
(basename match, `.md` suffix) — an edge between the two new tickets; an
`.md` entry naming no sibling (or the stub itself) is a typed promotion
failure. The contract is honoured identically on the generative and
stubs-only paths, so a stub means the same thing whichever door mints it.

Promotion is **fail-closed**: a committed stub whose front-matter block is
missing or invalid — or whose fields violate the ticket contract — raises a
typed error at plan time (the same fail-closed posture as an uncommitted
stub), naming the stub path and the offending field. A committed stub is
never silently skipped and a malformed ticket is never emitted.

This imposes a **forward coupling**: once promotion is live, *every*
committed inbox stub MUST carry a valid front-matter block or planning fails
closed. Today's follow-up producer (pm-engine-and-linear-sync.md, ATLAS-45)
writes machine stubs with **no** front-matter; committing one of those would
be a hard, typed stop. The producer follow-on therefore MUST emit the
front-matter contract above before machine-written stubs can be committed.
Dedup and retirement are unchanged: a promoted stub is retired to
`inbox/processed/` on apply (§2.2) and `processed/` is excluded from the
inbox read, so a promoted, applied stub is never re-read and never yields a
second `ADD`.

**Stubs-only entry path (ATLAS-153).** `atlas plan --stubs-only` gives
deterministic promotion its own door: generation is skipped entirely and the
proposal is built by pure code — the current backlog re-stated verbatim with
its real keys (a no-op to the reconciler by construction: no `MODIFY`, no
`PROPOSE_ARCHIVE`, no frozen `CONFLICT`) plus the promoted stubs. The
assembled proposal flows through the same gates (section 5), the same
reconciler (section 4; the promotion-collapse pre-pass runs and is trivially
a no-op — there are no model tickets to collapse), and persists the same
`PlanRun` shape, which `atlas apply` (§2.2) consumes unchanged, stub
retirement included. No `PlannerClient` is constructed, so no API key is
required; `--stubs-only` and `--staged` are mutually exclusive at the CLI.
An empty committed inbox is a clean-exit precondition failure naming the
inbox — never an empty-diff `PlanRun`. Because there is no model to create
epics and no parse stage to bounds-check a placeholder, a stub's `epic_ref`
must name an existing epic key under this path (a placeholder ref is a typed
precondition failure). Provenance records the mode: `generation_stages` is
the empty list (a generative run always stores ≥ 1 stage record), the model
and prompt columns carry pinned `none` / `stubs-only` sentinels, and
`raw_output_hash` is over the constructed proposal's canonical JSON;
`input_doc_shas` still pins corpus + inbox + `processed/` (ATLAS-159), so
the AT-5 staleness re-check holds identically. A gate failure (e.g. a `depends_on` entry naming a
nonexistent ticket) records a `failed` run exactly as a generative gate
failure would (§6).

### 2.2 `atlas apply`

1. Load the most recent `PlanRun` with `status: proposed`.
2. Refuse if fresh ingestion (which itself fails with a typed error on
   a dirty input set) yields input doc SHAs that no longer match the
   recorded `input_doc_shas` (stale plan).
3. Display the diff; require explicit operator confirmation. Confirmation
   is an interactive `y/N` prompt, or `--yes` for non-interactive use; with
   neither a TTY nor `--yes`, apply refuses rather than assume consent. No
   write of any kind happens before confirmation.
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

On both the applied and the rejected outcome — both mean "considered" —
apply retires the inbox stubs that fed the plan to
`docs/planning/inbox/processed/`, an idempotent move (the staleness
re-check in step 2 folds the inbox AND the `processed/` subdir into the
fresh SHA set (ATLAS-159), so a stub change — active or retired — between
plan and apply reads as stale). The mechanism is owned by
pm-engine-and-linear-sync.md "Follow-up ingestion".

Atomicity. The DB commit (counter increment + backlog rows + the
finalising transition, in one transaction) is the single linearisation
point. The renders are a deterministic projection of committed state, so
apply writes them to temp files and atomically moves them into place only
after the commit. A crash before the commit leaves nothing durable (the
transaction rolls back; temp files are inert); a crash during the
post-commit move is repaired by re-running apply, which completes the
pending move from the committed state. After any crash the backlog is
either fully pre-apply or fully post-apply — keys, rows, and renders never
disagree.

Applied diff entries. Milestone 1 apply materialises `ADD` items and
excludes `PROPOSE_ARCHIVE` items from the renders. A diff that touches a
frozen ticket (`CONFLICT`) is refused (AT-4). `MODIFY` application is
deferred to a follow-up; a diff containing `MODIFY` entries is refused
rather than applied with invented semantics.

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
counts per type. Promotion-dedup collapses (§4, ATLAS-151) render one
`COLLAPSE` line each directly after the summary — naming the absorbed
model ticket, the surviving promotion ticket, and the shared anchor — and
add a `COLLAPSE <n>` count to the summary line only when nonzero; a
collapse-free diff renders byte-identically to the four-type shape. The
same lines appear in `atlas apply`'s confirmation prompt. The key counter
lives in the `key_counters` table (knowledge-core.md#key-counter).

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

Promotion dedup (pre-pass, ATLAS-151): `reconcile` accepts the set of
promotion-injected proposal tickets (`promotion_indices`). Identity is
POSITIONAL — `atlas plan` records the index range `promote_inbox_stubs`
appended to the proposal's tail; `atlas apply` reconstructs the same set
as the trailing `len(inbox)` tickets of the stored proposal, which is
sound because the AT-5 staleness re-check pins the apply-time inbox to
plan time. Before the matching passes, a keyless model-emitted ticket
whose `source_anchor` equals a promotion-injected ticket's anchor is
collapsed into the promotion ticket: it takes no part in matching, emits
no `ADD`, and its dependency edges are re-pointed to the surviving
promotion ticket and deduplicated against the survivor's own edges (a
duplicate↔survivor edge degenerates to a self-loop and is dropped).
Deterministic promotion content always wins; one committed stub yields
exactly one ticket `ADD` regardless of what the model re-emits. The diff
records one collapse line per absorbed ticket (§2.4), surfaced at both
gates. Rationale: three live reproductions (the declined 2026-07-08
double-emission, the cancelled duplicate mints ATLAS-149/150, and
ATLAS-155/158) matched or diverged on every candidate content feature —
edges, anchors, titles, full content — so recognition heuristics over
duplicate features are unstable; only the pipeline's positional knowledge
of what it injected is reliable. Boundary: a re-emission citing a foreign
anchor (the ATLAS-149/150 shape) never collides with a promotion anchor
and is deliberately not collapsed — foreign-anchor re-emission remains a
gate-read concern at the operator's confirm step.

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
  roadmap, the planner's proposal clears the AT-7 coverage bar defined in
  §7.1–§7.2 — a **pair** of gating floors: exact-anchor `anchor_coverage`
  `>= ANCHOR_COVERAGE_FLOOR = 0.50` (catastrophe catch) **and** the unified
  `exact ∪ content` floor `>= UNIFIED_COVERAGE_FLOOR = 0.80`, content measured
  containment-aware, citation excluded (the historical "≥90% by anchor match"
  is now the exact-anchor floor, superseded per §7.2) — the roadmap being the
  evaluation fixture, per ADR-0007.

## 7.1 AT-7 coverage metric

> **Superseded bar (ATLAS-112, resolved in §7.2).** Every "90%" in this
> section is the *historical* exact-anchor pass line. The live AT-7 bar is
> now the **pair** recorded in §7.2: exact-anchor `anchor_coverage` as a
> strict floor (`ANCHOR_COVERAGE_FLOOR = 0.50`) **and** the unified
> `exact ∪ content` floor (`UNIFIED_COVERAGE_FLOOR = 0.80`, content
> containment-aware, citation excluded — pinned by ATLAS-124). Read every
> figure below under this note — the 0.90 is retained only to describe how
> exact-anchor was originally defined, not as a current threshold.

AT-7's exact-anchor metric (the single implementation is the acceptance
suite's `anchor_coverage`, ATLAS-29) is measured as follows:

- **Hand-written tickets.** A hand-written ticket is a line in
  `docs/atlas/implementation-roadmap.md` matching `^ATLAS-<n> <title>` —
  the key at line start. Wrapped continuation lines (indented, no leading
  key) and `Retired:` lines are not tickets. Headings and ticket lines
  inside fenced code blocks are excluded (§2.3).
- **Anchor.** Each ticket's anchor is
  `docs/atlas/implementation-roadmap.md#<slug>`, where `<slug>` is the
  ingestion slug (§2.3) of the nearest preceding Markdown heading — the
  `## Epic:`/`# Phase` section the ticket sits under, the finest anchor a
  roadmap ticket has.
- **Match.** A hand-written ticket is covered iff at least one proposed
  ticket's `source_anchor` equals its anchor exactly — no similarity, no
  tolerance.
- **Coverage.** `covered ÷ total`. The historical exact-anchor pass line was
  `≥ 0.90`; under the §7.2 resolution that line is now the strict floor
  (`anchor_coverage >= ANCHOR_COVERAGE_FLOOR = 0.50`), with the unified
  `exact ∪ content` floor (`UNIFIED_COVERAGE_FLOOR = 0.80`, §7.2) the second
  gate. Because matching is at the section heading, covering a section with
  one proposed ticket covers every hand-written ticket in it: the metric
  measures what fraction of the roadmap's ticket-bearing sections the proposal
  reaches, ticket-weighted.
- **Conservative floor.** Exact-anchor matching can only undercount — a
  correct proposal anchored to an adjacent heading scores as a miss, never
  a false hit — so the reported coverage is a lower bound on true coverage.
  A result below the exact-anchor floor (`anchor_coverage < 0.50`), or below
  the unified floor (`exact ∪ content < 0.80`, §7.2), is therefore a
  planner-quality signal to investigate, not something to resolve by loosening
  the matcher.

## 7.2 AT-7 work-coverage finding (ATLAS-112)

Two live staged runs against the same corpus scored 82.6% and 63.0% on
`anchor_coverage` — a ~20-point swing. Offline analysis of the lower run
showed all 34 of its misses were Phase 4–8 tickets (Delivery Coordination,
Execution Context, Evidence-Driven Delivery, Autonomous Delivery) whose
**work is present in the proposal** but which the planner anchored to their
**design documents** (`pm-engine-and-linear-sync.md`, `context-renderer.md`,
`evidence-pipeline.md`, `symphony-integration.md`) rather than to the
roadmap epic heading the hand-written ticket used.

`anchor_coverage` therefore conflates two questions: *did the planner cover
the work?* and *did it anchor where the roadmap author did?* The swing is in
the second. Anchoring a PM-engine ticket to `pm-engine-and-linear-sync.md#sync-loop`
is arguably **more precise** than anchoring it to a vague epic heading; it is
a different convention, not a defect, and is **not to be "corrected"** to
raise the number (that would be a Goodhart failure).

**Content-coverage metric.** `content_coverage` (a sibling of `anchor_coverage`
in the acceptance suite) measures work coverage: a hand-written roadmap ticket
is covered iff *some* proposed ticket's title clears a recorded threshold on
**either** of two scores, **independent of which document the proposed ticket is
anchored to**. Both reuse the reconciler's single tokeniser; there is exactly
one similarity implementation and one containment implementation in the
codebase.

- **Comparand.** Title-vs-title (both sides pass an empty objective). The
  roadmap carries a terse title; concatenating the planner's descriptive
  objective onto its side structurally dilutes the coefficient and drives
  genuinely-covered work below threshold. Comparing like with like is a
  measurement-correctness choice, not a score optimisation.
- **Scores.** The symmetric Sørensen–Dice coefficient (`reconciler.similarity`)
  **or** the directional containment ratio (`reconciler.containment`, ATLAS-124)
  — covered iff `max(symmetric, containment) >= threshold`. Containment is
  `|roadmap_tokens ∩ proposed_tokens| / |roadmap_tokens|`: of the roadmap
  title's tokens, the fraction present in the proposed title. It recovers the
  length-asymmetry false negative where a terse roadmap title is a literal
  subset of a longer proposed title — e.g. "Ticket synchronisation" ⊂ "Ticket
  synchronisation sync_tick (pull status, push definitions)" scores symmetric
  Dice 0.44 < 0.50 yet containment 1.0. It is asymmetric and directional, so it
  credits subset matches regardless of how much extra the proposal says; disjoint
  token sets give 0.0, so it raises recall only on genuine subsets and never
  manufactures a cross-ticket false positive. It is a separate function beside
  `reconciler.similarity`, which is **unchanged** — the reconciler's matching
  semantics are untouched.
- **Threshold.** `CONTENT_COVERAGE_THRESHOLD = 0.5` — at least half the
  combined token mass overlaps (or, by containment, half the roadmap title's
  tokens present). Set for correctness and **recorded**; it is never tuned
  against the metric, and the reconciler's 0.85 entity-match threshold does not
  transfer because that compares title+objective pairs for identity, a different
  question.

**Corrected adjacency analysis.** The offline tool classifies an exact-anchor
miss as an *adjacent-anchor undercount* only when a planner **ticket** is
anchored to a heading **near** the wanted heading **within the same
document** — within one position in the document's heading index, or sharing
the immediate parent heading. The earlier "any anchor in the same document"
test was defeated by the roadmap holding many unrelated Phase 0–3 ticket
anchors, so every roadmap-doc miss trivially passed it (a false 100%
optimistic ceiling). A candidate anchored to a *different document* is never
adjacent — that is a different-document anchoring choice, the case this
finding is about.

**Falsifiability.** The offline evaluation (`scripts/at7_miss_analysis.py`,
free, no API call) reports both metrics side by side and prints an explicit
verdict on whether the exact-anchor misses are predominantly
design-doc-anchored Phase 4–8 work — it reads **CONFIRMED or CONTRADICTED**,
so the tool can prove the finding wrong, not only confirm it. On the one
durably-saved capture (10 epics / 95 tickets) it reports exact-anchor 63.4%,
content-coverage 68.8%, and CONFIRMED (27/34 misses are Phase 4–8 work
present but anchored off the roadmap). These are stated as measurement; a
higher content figure is **not** a success signal.

**Run-to-run spread.** Only one capture is durably on disk; the 82.6% run was
never saved and regenerating it is an API call not spent here. Exact-anchor
spread is therefore the documented swing (82.6% vs the saved capture);
content-coverage spread requires a second saved capture — a free byproduct of
the next staged run, scored by the same tool — and is not manufactured from a
single file.

> **Operator decision RESOLVED (ATLAS-112), PINNED (ATLAS-124).** The AT-7
> bar is a pair of gating floors. Content-coverage measures what AT-7 is for
> — whether the roadmap's work is covered, independent of anchoring
> convention — and the CONFIRMED finding above shows exact-anchor alone fails
> work that is present but re-anchored, so it cannot be the sole gate. The
> exact-anchor floor is kept as a cheap, unambiguous catastrophe catch; both
> come from the same offline tool. Chosen because it measures the right thing,
> not because it scores higher.
>
> - Exact-anchor floor — `anchor_coverage >= ANCHOR_COVERAGE_FLOOR = 0.50`,
>   live now. A catastrophe catch set safely below the observed 63.4%/82.6%
>   range; not a precision gate, and not to be tuned upward against the metric.
> - Unified work-coverage floor — `(exact ∪ content) >= UNIFIED_COVERAGE_FLOOR
>   = 0.80`, content measured **containment-aware** (the D1 fix above), pinned
>   by ATLAS-124. The gated signal is the union of the exact-anchor and
>   containment-aware content covered-key sets; **citation is excluded**.
>
> **Why citation is excluded from the gate.** Two durably-saved staged captures
> settled this. The deterministic part of coverage — `exact ∪ content` — was
> stable across both runs (~87% each). The volatile part was citation, which
> swung **+12 → +1** unique keys between the two runs on a pure styling whim:
> whether the planner happened to write `(ATLAS-NN)` into ticket titles. Gating
> on a signal that moves twelve keys on a formatting choice would import that
> volatility into the bar, so `citation_covered_keys` stays a **diagnostic
> lens only** (`scripts/at7_miss_analysis.py`, labelled "diagnostic, not
> gated") and is never part of the gated union.
>
> **Why 0.80.** The two captures put the stable `exact ∪ content` signal at
> ~87% both runs; the containment-aware fix recovers the length-asymmetry
> false negatives deterministically (the residual misses were overwhelmingly
> roadmap titles that are literal subsets of longer proposed titles). 0.80
> sits below both captures with margin for run-to-run jitter and above a real
> collapse. Set for correctness and recorded; never tuned against the metric.
>
> **Standing residual.** The unified floor is computed over all 113 roadmap
> keys. ATLAS-46 (roadmap synchronisation) is the documented Phase-4 operator
> deferral and is expected to remain uncovered; 0.80 has ample margin for that
> one accepted standing residual, which is not special-cased in code.
>
> This supersedes the `anchor_coverage >= 0.90` pass condition in §7.1.
>
> The metric is encoded into the acceptance suite by ATLAS-123 (the
> exact-anchor floor live, `content_coverage` reported-not-gated until
> pinned); ATLAS-107's staged-path acceptance reuses it. ATLAS-124 pins the
> unified floor and flips content's leg from reported to gating, via the
> containment-aware `exact ∪ content` union.

## 8. Non-goals for milestone 1

No Linear writes. No Symphony dispatch. No HTML roadmap (Mermaid render
only). No estimation logic: `estimated_effort` exists on the Ticket model
from Phase 1, is never computed or inferred, and the planner never emits
it — `atlas apply` inserts every ticket with the field null. An operator
supplies it out-of-band through `TicketRepo.set_estimated_effort` (Phase 3,
ATLAS-32), which accepts a positive integer (>= 1) or null and rejects
`<= 0`; the critical path weights a null as 1. That setter is the single
writer of the field, owning it per-field while apply owns the doc-sourced
definition fields — a partition apply must preserve across any future
MODIFY-apply path (see dependency-engine.md "Effort population"). No
automatic re-planning triggers; planning runs only when the operator
invokes it.

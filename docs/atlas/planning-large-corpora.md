# Large-corpus planning

Status: Active design (ATLAS-102). Resolves the output-capacity boundary named in
planning-engine-specification.md §2.1 and recorded by ADR-0010. Implements ADR-0007
and ADR-0010; the build is ATLAS-103..107, not this document.

## 1. The boundary

A proposal is **full-state**: one structured object that is the *complete* desired
backlog (data-model-and-schemas.md §3.11). The reconciler diffs it against the
current backlog and treats any omitted item as `PROPOSE_ARCHIVE` — so a proposal
that is merely *partial* is not a smaller plan, it is a plan to archive everything
it leaves out.

A single model response has a hard output ceiling: `claude-sonnet-4-6` caps at 64K
output tokens, and the call already streams (ATLAS-101). Against the committed
Atlas corpus the planner now emits a complete, well-formed proposal of **~247,100
characters (~60–62K output tokens)** — at the ceiling. Live AT-1/AT-7 evidence:

- one full run produced the complete proposal above (~95–97% of the limit);
- two other full runs **truncated** at 64K, recorded as `failed` runs with the
  ATLAS-101 truncation reason.

Natural length variation — present even at temperature 0 — tips a corpus this size
over the edge from one run to the next.

## 2. Why this is a design question, not a config bump

There is no higher `max_tokens` to set: 64K is the model's maximum, and the corpus
sits against it. The fix cannot be a number. It also cannot be "emit a partial
proposal", because partial is not safe under the full-state invariant. The genuine
question is **how to generate one complete full-state proposal across more than one
bounded model call** without weakening the full-state invariant (§3.11) or
ADR-0007's deterministic reconciliation. That is an architecture decision
(ADR-0010), and this document is its design.

## 3. Approaches considered

### A. Split generation by entity stage; assemble one full-state proposal — chosen

Generate in bounded stages (epics → tickets → dependencies) and assemble the slices
into one complete §3.11 proposal *before* the parser. The reconciler, gates, and
proposal contract are untouched; only generation is split. Preserves the full-state
invariant and ADR-0007 by construction (§5).

### B. Split the corpus by document domain; merge sub-proposals — rejected

A sub-corpus proposal is **not** full-state — each would `PROPOSE_ARCHIVE`
everything outside its domain. Merging requires de-duplicating overlaps, resolving
conflicts, and stitching cross-domain dependencies, and `new:<n>` indices are
per-sub-proposal so a dependency from one domain to another cannot even be
expressed. This fights §3.11 and ADR-0007 (the reconciler expects one full-state
proposal) and is non-deterministic in the merge.

### C. Compress the output contract at generation — rejected as primary

Have the model emit leaner per-ticket prose, enriched in a later pass. But §3.11
requires the full field set (`acceptance_criteria ≥ 1`, `non_goals ≥ 1`,
`test_requirements ≥ 1`, …) and gate 1 checks presence, so a compressed emission
either fails the gates or forces a §3.11 relaxation that erodes the "complete
desired backlog" guarantee — and the enrichment pass is another model call (cost,
determinism). It trades completeness for capacity. Retained only as an *orthogonal*
optimisation: leaner generated prose can widen approach A's per-call headroom, but
it is not the mechanism that resolves the boundary.

## 4. Recommended design: staged generation, single-proposal reconciliation

Three bounded generation stages, each well inside the 64K ceiling, assembled into
one §3.11 proposal:

1. **Epics.** The planner reads the corpus and emits the epics only (a projection
   of §3.11 ProposalEpic). The environment assembles them in emission order.
2. **Tickets, batched per epic.** For each epic, the planner emits that epic's
   tickets, seeded with the assembled epic list (and their indices). The
   environment appends each batch to the assembled tickets array.
3. **Dependencies.** The planner emits the `depends_on` edges, seeded with the
   assembled ticket list (and their indices). The environment appends them.

The environment then concatenates the slices — epics in stage-1 order, tickets in
stage-2 assembly order, dependencies in stage-3 order — into one §3.11 envelope,
which is parsed, gated, and reconciled exactly as a single-call proposal is today.

### 4.1 Environment-owned index assembly (worked example)

Identity is assigned by the environment, never the model (ADR-0007): the model
*references* indices it is given but never mints them. Two epics, tickets across
both, one cross-epic dependency:

**Stage 1 — epics.** The model emits two epics. The environment assigns
`new_epic:<n>` by emission order:

| index | epic |
| --- | --- |
| `new_epic:0` | Planning Engine |
| `new_epic:1` | Dependency Engine |

This indexed list is handed to stage 2.

**Stage 2 — tickets, one batch per epic.** Each batch is told which epic it is for
(its `new_epic:<n>`); the model emits ticket bodies carrying `epic_ref =
new_epic:<that epic>` but **does not** number the tickets. The environment assigns
`new:<n>` by the ticket's position in the assembled array:

| assembled index | ticket | `epic_ref` |
| --- | --- | --- |
| `new:0` | Document ingestion | `new_epic:0` |
| `new:1` | Deterministic reconciler | `new_epic:0` |
| `new:2` | Graph schema and build | `new_epic:1` |
| `new:3` | Readiness detection | `new_epic:1` |

(The batch for `new_epic:0` is assembled first, then the batch for `new_epic:1`, so
the indices are contiguous per epic — but the *only* rule that matters is "position
in the assembled tickets array", which the environment owns.)

**Stage 3 — dependencies.** The environment hands the model the assembled ticket
list *with* its `new:<n>` indices. The model emits edges referencing those indices.
A cross-epic dependency — "Readiness detection needs the reconciler first" — is
expressible precisely because every ticket now has a **global** index:

```text
{ "source": "new:3", "target": "new:1", "dependency_type": "depends_on", ... }
```

`new:3` (Dependency Engine) depends on `new:1` (Planning Engine) — an edge across
epics, which approach B could not express because its indices would be local to
each sub-proposal.

**Assembled proposal** (the single object the parser receives):

```text
epics:        [ E:Planning Engine,            E:Dependency Engine ]
                 ^new_epic:0                    ^new_epic:1
tickets:      [ T:Document ingestion (ne:0),  T:Reconciler (ne:0),
                 ^new:0                          ^new:1
               T:Graph schema (ne:1),         T:Readiness detection (ne:1) ]
                 ^new:2                          ^new:3
dependencies: [ { new:3 -> new:1 } ]
```

The index flow is fully traceable: `new_epic:<n>` fixed in stage 1, `new:<n>` fixed
at stage-2 assembly, both referenced (not invented) in stages 2 and 3. This is the
load-bearing mechanism ATLAS-104 implements. Reference integrity is still
gate-checked — the parser bounds-checks `new:<n>`/`new_epic:<n>`, gate 5 resolves
every `epic_ref`, gate 3 resolves every dependency target — the same checks a
single-call proposal faces.

## 5. Effects

### 5.1 The §3.11 proposal contract — preserved

The assembled proposal conforms to §3.11 unchanged. What is new is a
**generation-protocol contract** the per-stage prompts implement: the per-stage
emission schemas are *projections* of §3.11 (epics-only; tickets-for-one-epic, with
`epic_ref` an echoed key or `new_epic:<n>`; dependencies-only over `new:<n>`/echoed
keys). §3.11 itself does not change; ATLAS-103 defines these projections as
versioned prompt artifacts.

### 5.2 The reconciler, AT-2, AT-7 — unchanged

The reconciler receives one assembled full-state proposal and the current backlog;
its "current backlog" semantics, matching passes, and key assignment are untouched.
AT-2 stability (empty / `MODIFY`-only diff on unchanged docs) and AT-7 coverage are
computed exactly as today, over the assembled proposal.

### 5.3 Provenance across multiple calls

Each stage records its `(prompt_version, prompt_hash, raw_output_hash)`. The
`PlanRun` keeps a `raw_output_hash` over the **assembled** proposal JSON, so the
existing chain `input_doc_shas → prompt_hash → raw_output_hash → proposal → renders`
stays legible, with the per-stage hashes recorded alongside. This is a §3.10
`PlanRun` addition — a `generation_stages` field (a list of per-stage records) —
**specified here, built by ATLAS-105** (model field + migration + schema regen +
contract test). The single-call path is the degenerate one-stage list.

### 5.4 Truncation within a stage

ATLAS-101's `stop_reason == max_tokens` detection applies per stage. Per-epic ticket
batching keeps each call far inside 64K, so truncation becomes unlikely; a stage
that still exceeds its bound (a pathologically large epic) fails honestly, naming
the stage in the truncation reason, and the batch-sizing heuristic (§7) is the lever
that prevents it.

## 6. The determinism boundary

State precisely what is and is not deterministic, so the guarantee is not
hand-waved:

- **Deterministic (pure environment code).** Index assignment (`new_epic:<n>` by
  stage-1 order, `new:<n>` by assembled position); the assembly concatenation
  order; the parser, gates, and reconciler; key assignment (the ATLAS-25 counter).
  Given identical stage outputs, the assembled proposal and the reconciled diff are
  byte-deterministic.
- **Not deterministic (the model).** A model call's output is not guaranteed
  bit-identical even at temperature 0 — the live evidence shows the proposal length
  varies run to run. Staged generation does not change this; it only makes each call
  smaller.
- **Where the stability guarantee lives.** Per ADR-0007 the guarantee is *not*
  byte-identical regeneration — it is the **reconciled diff**: an empty or
  `MODIFY`-only diff (≥ 0.95 similarity on free-text fields) on unchanged
  documents, never key churn. The reconciler matches by key/anchor/similarity and
  keys are environment-assigned, so prose drift across runs is absorbed as
  `MODIFY`-only, exactly as for a single call. Staged generation **does not move
  this boundary**: it splits generation for capacity and leaves the guarantee where
  ADR-0007 put it. The one genuinely new risk is that staging could shift the
  *decomposition* across runs (the model assigning a ticket to a different epic,
  changing its `epic_ref`/anchor), which could surface as more than `MODIFY`-only —
  but this is a risk single-call planning already carries (the model can decompose
  differently run-to-run); staging does not add it. It is validated, not asserted,
  by AT-2 across the multi-call sequence (§7, ATLAS-107).

## 7. Open items (need runtime data, not hand-waving)

- **Per-epic ticket batch sizing.** Whether each epic is one batch, or small epics
  are packed together under a token budget, needs measurement against the real
  corpus; it is the lever that keeps stage 2 inside the ceiling (§5.4).
- **Cross-run assembled-proposal stability.** §6 argues the reconciler's matching
  absorbs model drift into `MODIFY`-only diffs; AT-2 across the staged sequence
  (ATLAS-107) is how that argument is confirmed against live output, not assumed.
- **Staged prompt-template versioning.** The three per-stage templates are new
  versioned artifacts (ADR-0007 versions prompts); their release/versioning follows
  the existing prompts README rules (ATLAS-103).

## 8. Proposed implementation tickets

This design is built by, not in, the following (proposed roadmap addition; keys
proposed, not assigned):

- **ATLAS-103** Staged planner prompt templates — versioned epics /
  tickets-per-epic / dependencies projections of §3.11.
- **ATLAS-104** Multi-call generation orchestration — the stage sequence,
  environment-owned index assembly (§4.1) into one §3.11 proposal, composing with
  the existing parser, gates, and reconciler.
- **ATLAS-105** PlanRun multi-call provenance — the `generation_stages` §3.10 field,
  migration, schema regen, and contract test (§5.3).
- **ATLAS-106** Per-stage truncation handling and batch sizing (§5.4, §7).
- **ATLAS-107** Acceptance coverage for staged generation — AT-1/AT-7 over the
  staged path and AT-2 stability across the multi-call sequence (§6).

## 9. Relationship to the ADRs

ADR-0007 establishes generative planning with deterministic reconciliation and
environment-owned identity. ADR-0010 extends it with the decision this document
designs: generation may span multiple bounded calls, but reconciliation always
receives exactly one complete full-state proposal, and the environment owns
positional identity across the calls.

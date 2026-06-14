# ADR-0010: Multi-call generation with single-proposal reconciliation

## Status

Accepted

## Context

ADR-0007 makes `atlas plan` generate a *full-state proposal* — the complete
desired backlog — which the deterministic reconciler diffs against the current
backlog, treating any omitted item as `PROPOSE_ARCHIVE`. The proposal is one
structured JSON object (data-model-and-schemas.md §3.11).

A single model response has a hard output ceiling (`claude-sonnet-4-6`: 64K
tokens). The committed Atlas corpus now produces a complete proposal of ~247K
characters (~60–62K output tokens) — at the ceiling. The live AT-1/AT-7 runs
recorded one full proposal that fit (~95–97% of the limit) and two that
truncated at it (ATLAS-101 detects and records this honestly). There is no
higher `max_tokens` to reach for, and a partial proposal is unsafe: it would
archive everything it omits. Single-call generation of a corpus this size is
past the boundary. The design analysis lives in
`docs/atlas/planning-large-corpora.md` (ATLAS-102).

## Decision

Generation may span **multiple bounded model calls**, but reconciliation always
receives **exactly one complete full-state proposal**. The split is in
*generation only*; the parser, validation gates, and reconciler are unchanged
and still operate on a single §3.11 proposal.

- **Staged generation.** The planner generates by entity stage — epics, then
  tickets (batched per epic), then dependencies — each call bounded well inside
  the output ceiling. Each later stage is seeded with the assembled output of
  the earlier stages.
- **The environment owns positional identity across calls.** As under ADR-0007
  the model never mints identity: the environment assigns `new_epic:<n>` by the
  stage-1 assembly order and `new:<n>` by the stage-2 assembly order, and hands
  the indexed lists to later stages, which *reference* those indices
  (`epic_ref`, dependency endpoints) but never invent them.
- **Assembly precedes reconciliation.** The stage outputs are concatenated, in a
  fixed deterministic order, into one §3.11 proposal envelope. That single
  proposal is parsed, gated, and reconciled exactly as a single-call proposal is
  today. A single-call plan is the degenerate one-stage case.
- **Provenance spans the calls.** The `PlanRun` records each stage's
  `(prompt_version, prompt_hash, raw_output_hash)` and keeps a `raw_output_hash`
  over the *assembled* proposal, so the chain `input_doc_shas → stage hashes →
  assembled raw_output_hash → proposal → renders` stays legible.

## Rationale

The full-state invariant and ADR-0007's deterministic reconciliation are exactly
what make identity stable; neither may be weakened to gain capacity. Confining
the split to generation preserves both by construction: the reconciler, the
gates, and the §3.11 contract never see anything but one complete proposal. The
stability guarantee remains ADR-0007's — an empty or `MODIFY`-only reconciled
diff on unchanged documents, not byte-identical generation — so residual model
non-determinism (which exists even at temperature 0) is absorbed by the
reconciler's key/anchor/similarity matching, the same way it is for a single
call. The environment owning cross-call indices keeps "policy decides,
environment bookkeeps" intact across the call sequence.

## Consequences

- The §3.11 proposal contract is unchanged; a new generation-protocol contract
  defines the per-stage emission schemas (projections of §3.11) and the assembly
  order (`docs/atlas/planning-large-corpora.md`).
- The `PlanRun` gains a `generation_stages` field (data-model §3.10) so
  multi-call provenance is recorded; the single-call path is one stage.
- Per-stage prompt templates become new versioned artifacts (ADR-0007 already
  versions prompts).
- Truncation detection (ATLAS-101) applies per stage; a stage that still exceeds
  its bound fails honestly, naming the stage.
- The implementation is a sequence of tickets (ATLAS-103..107) defined by the
  design document; this ADR records the architecture, not the build.

## Alternatives considered

- **Split the corpus by document domain and merge sub-proposals.** Rejected: a
  sub-corpus proposal is not full-state, so each would archive everything outside
  its domain; merging requires cross-domain dependency stitching and conflict
  resolution that fights §3.11 and is non-deterministic. Cross-domain
  dependencies cannot be expressed with per-sub-proposal indices.
- **Compress the output contract — leaner generated prose, enriched later.**
  Rejected as the primary fix: §3.11 requires the full field set and the gates
  check presence, so a compressed emission either fails the gates or forces a
  §3.11 relaxation that erodes the "complete desired backlog" guarantee, plus a
  second enrichment pass. Retained only as an orthogonal optimisation that can
  widen per-call headroom.
- **Raise `max_tokens`.** Not available — 64K is the model's ceiling; the corpus
  already sits against it.

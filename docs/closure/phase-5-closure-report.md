# Phase 5 Closure Report — Context Renderer

Status: **CLOSED** as of 2026-06-25. The Context Renderer is built and
CI-evidenced across twelve PRs (#85–#93, #105–#107), each under both evidence
tiers (agent completion reports corroborated by system-tier CI pinned to head
commits, per ADR-0008). For one ready ticket the renderer assembles the
minimum high-value context — the ticket's own fields, the ADRs and related
tickets it depends on, the documentation sections it anchors to (SHA-recorded),
and the ACTIVE lessons that match it — composes a fixed-order markdown pack,
compresses it down a deterministic ladder when it exceeds budget, validates it
against the five spec checks, and exposes the whole thing through
`atlas context render / validate / show`.

Unlike Phase 4, Phase 5's milestone is **deterministic**: it is a CI test, not
an operator-run live leg. There is no owed live evidence and nothing PENDING —
the phase closes the moment CI is green, which it is. Two items are **deliberately
deferred** rather than built, each to its own ticket with a real reader as the
trigger: context-pack **persistence** (the CLI is transient by decision) and the
**promotion gate** (wiring `validate_context_pack` into `promote_ready`). Both
are recorded in §4, not silently omitted.

---

## 1. Milestone evidence

The roadmap milestone (line 303): *a generated pack for a fixture ticket
contains every required section, only ACTIVE lessons, and a token estimate; a
doc edit after rendering is detectable from `input_doc_shas`.* Every clause is
checked by a single deterministic test — there is no live tier this phase.

| Claim | Asserted by | Status |
| --- | --- | --- |
| A generated pack contains **every required section** in fixed order | `build_context_pack` section-by-section render; milestone test asserts each header present and ordered (ATLAS-56/58) | **PASS** (deterministic, CI) |
| The pack includes **only ACTIVE lessons** | `select_lessons` is ACTIVE-only by construction (ADR-0009); milestone test asserts a DRAFT fixture lesson is absent (ATLAS-53) | **PASS** (deterministic, CI) |
| The pack carries a **token estimate** | `token_estimate = len(rendered_markdown) // 4`, recorded on the pack (ATLAS-56) | **PASS** (deterministic, CI) |
| A **doc edit after rendering is detectable** from `input_doc_shas` | milestone test mutates + recommits a corpus doc, re-renders from HEAD, and asserts the path's SHA changed — real staleness, not field-presence (ATLAS-58, #107) | **PASS** (deterministic, CI) |
| Over-budget packs **compress down a fixed ladder, then fail closed** | the four-rung ladder runs before the terminal `ContextBudgetExceededError`; `compression_applied` records which rungs fired (ATLAS-55, #105) | **PASS** (deterministic, CI) |
| A pack **validates against the five spec checks** | `validate_context_pack` returns a result (never raises); objective, ≥1 criterion, ≥1 test command, token ≤ budget, anchors resolve, no DRAFT lessons (ATLAS-60, #106) | **PASS** (deterministic, CI) |

All milestone claims are CI-proven. No live or operator-run evidence is owed.

---

## 2. Delivered

| Ticket | Delivered |
| --- | --- |
| ATLAS-127 (#85) | Free-form `tags`/`component` on the stored `Ticket` — the matching surface the lesson and ADR retrievers select against. Storage half. |
| ATLAS-128 (#86) | Planner emits `tags`/`component`: `ProposalTicket` gains the fields, the planner produces them, materialisation and validation carry them through. Writer half. |
| ATLAS-51 (#87/#88) | ADR retrieval — `select_adrs` over the projected graph; #88 carries the ADR UUID on `ADRMatch` so the structured reference list holds a typed identifier, the precedent applied to every later retriever. |
| ATLAS-129 (#90) | Relocated the anchor/slug primitive (slugify, heading parsing, `SourceDocument`, `AnchorIndex`, the anchor error hierarchy) from `atlas.planning.ingestion` to `atlas.core.anchors` — a pure move, no behaviour change, unblocking the `atlas.context` retrievers that sit below `atlas.planning` in the spine. |
| ATLAS-52 (#91) | Documentation retrieval recording doc SHAs — section-level extraction over the relocated primitive; an unmatched `relevant_docs` entry is skipped, never a wrong-doc match. |
| ATLAS-53 (#92) | Historical lesson retrieval — ACTIVE-only by construction (ADR-0009); tag/component/ticket_type matching, vector search deferred. The "only ACTIVE lessons" milestone half. |
| ATLAS-54 (#89) | Related-tickets retrieval — `select_related_tickets` over the dependency graph. |
| ATLAS-56 (#93) | Context pack generation — assembles the four retrievers and the verbatim ticket fields into one `ContextPack`, composes the fixed-order `rendered_markdown`, records `input_doc_shas` for staleness, and estimates tokens. Pure; fail-closed on budget. |
| ATLAS-55 (#105) | The four-rung compression ladder, inserted **before** the fail-closed raise: lesson bodies → titles, related objectives → key+title, doc sections → first paragraph + command blocks, ADR consequences dropped — cumulative, re-estimated after each rung, with `compression_applied` provenance on the pack (atomic schema + migration coupling). |
| ATLAS-60 (#106) | The five-check validator — `validate_context_pack(pack, *, documents, lessons, ticket=None, budget)` returning `ContextPackValidation` (never raises). Anchors degrade honestly (path-level always, slug-level when a ticket is supplied); DRAFT and dangling lesson references are distinct failures; all failures collected, not short-circuited. |
| ATLAS-58 (#107) | The CLI — `atlas context render / validate / show` over a shared `<KEY> → five inputs` loader (`build_dependency_graph`, `collect_input_documents` re-ingested from HEAD, accepted ADRs, lessons). Transient by decision; `validate` exits non-zero on an invalid pack. Carries the Phase-5 milestone test. |

Retired in scope: ATLAS-57 (Context API) and ATLAS-59 (quality scoring as a
separate ticket — minimal checks folded into ATLAS-60).

---

## 3. The harness ledger — what the phase taught and where it was encoded

- **Add a primitive beside the one it resembles; do not alter the shared one.**
  ATLAS-124's `containment` was built next to `reconciler.similarity`, reusing
  the single tokeniser, because `similarity` is also the reconciler's matching
  primitive — changing its semantics would change reconciliation, not just the
  metric. The same instinct governs the renderer: one similarity implementation,
  one tokeniser, extended by addition.
- **Compression is a render-level parameter, not string surgery.** Because
  `_render_markdown` was already section-by-section, the ladder threads a
  compression level through the per-section helpers and re-renders — it never
  regexes its own output. The rungs map one-to-one onto the section renderers.
- **Provenance lives on the model where the data it describes does.**
  `compression_applied` joins `input_doc_shas` as a recorded fact about how the
  pack was produced — silent compression would be the one place in the system
  where information vanishes, which the spec's "reported, not silently truncated"
  principle already forbids. Adding the field was an atomic coupling: model, JSON
  schema export, Alembic migration, table, and the storage-schema test moved in
  one commit.
- **A validator returns data; it does not raise.** Invalidity is an answer
  ("valid or not, and why"), not an exception — a pre-promotion gate must be able
  to report every failure at once without throwing. Anchor resolution still
  raises its typed errors internally; the validator catches `IngestionError` and
  converts to failure strings. It mirrors `dependencies/validation.py`'s
  collect-all idiom but inverts its raise into a return.
- **A check is only honest at the depth its inputs allow.** The pack stores
  paths, not the original `path#slug` anchors, and lesson UUIDs, not status. So
  the anchor check degrades — path-level from the pack alone, slug-level only when
  a ticket is supplied — and records `anchor_check_depth` so a reader knows which
  ran. Claiming the spec-true check without the input would be a pass-by-omission.
- **Defer the write until something reads it.** The CLI is transient: `render`
  prints, it does not persist. The only future reader of a stored pack is the
  promotion gate, its own ticket — so persistence is designed there, against a
  real requirement, rather than guessed at render time. The question "what is
  `show` distinct from `render`" dissolved once persistence was deferred.

---

## 4. Carry-forwards (owners and homes)

| Item | Owner / home | Status |
| --- | --- | --- |
| `relevant_docs` path format (ATLAS-52) | Phase-5 planner-prompt follow-up | **Open** — the renderer half landed (rung 3 owns `_render_docs`); the upstream fix remains: a planner-prompt sentence requiring repo-relative paths, plus the data-model ContextPack example. Until reconciled, a bare-filename `relevant_docs` exact-misses the corpus path and contributes nothing live. |
| Lesson match facets (ATLAS-53) | Context renderer | **Open** — spec rule 4 (tags + ticket_type) versus the roadmap line (tags + component); ATLAS-53 implements the union. Revisit only if facet ranking is needed. |
| Context-pack persistence | Its own ticket (likely Phase 6) | **Deferred (operator decision)** — the CLI is transient; persist when a real reader needs a stored pack. `ContextPackRepo` already exists; the trigger does not yet. |
| Promotion gate — `validate_context_pack` into `promote_ready` | Its own ticket | **Deferred (operator decision)** — keeps `promote_ready`'s "graph used unvalidated by design" contract intact until the gate is designed deliberately. The validator is built and ready to call. |
| ContextPack constraints/risks/context source (ATLAS-56) | Context renderer | **Open, accepted v1 compromise** — `constraints` is `[]` in v1, `risks` is a derived risk-level line, `context` folds into the rendered Objective; documented, not a blocker. |
| ATLAS-46 roadmap synchronisation | Phase 4 → its own design pass | **Deferred (Phase-4 carry)** — cross-reference only; needs a `roadmap.mmd` ⇄ Linear field-ownership ruling. Not a Phase-5 item. |

---

## 5. Next phase

Phase 6 (Evidence System) and Phase 7 (Verification Engine) are the
viability-defining phases per Atlas's own success criteria: they decide whether
"agents execute, evidence gates, humans decide" holds in practice. Phase 6 makes
evidence first-class — commit-pinned CI runs ingested as records with trust tiers
under ADR-0008 — and Phase 7 makes that evidence load-bearing, refusing to treat
a ticket as done on agent-claimed passing alone. The Context Renderer feeds them:
the pack is the context an agent receives, and the two deferred items (persistence
and the promotion gate) are most naturally designed where the evidence and
agent-run paths that read a pack actually live. The import-linter spine —
`cli > planning > pm > context > dependencies > storage > linear > core` — keeps
the additions layered: the evidence system can import the renderer, never the
inverse.

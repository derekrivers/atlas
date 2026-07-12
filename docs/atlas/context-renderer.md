# Context Renderer Design (Phase 5)

Status: Active design document for Phase 5. The ContextPack schema lives in
`data-model-and-schemas.md` §3.9; this document owns retrieval, budgeting,
and validation decisions.

## Boundary

The renderer assembles the minimum high-value context for one ticket. It is
deterministic given the ticket and the doc tree at specific SHAs — no LLM in
the render path (summarisation by model is deferred; see Open items). This
is the Harness-1 division applied to context: the environment curates and
budgets; the execution agent reasons.

## Retrieval rules (v1)

Sources and selection, in order:

1. **Docs:** the ticket's `relevant_docs` plus the doc containing its
   `source_anchor`. **Section-level extraction**: include only the
   anchored heading's section (heading to next same-level heading) plus
   its parent heading chain for orientation — never whole files. Cap: 5
   doc sections. Anchor resolution and reference matching run over the
   planner input corpus plus the committed
   `docs/planning/inbox/processed/` stubs (durable stub anchors,
   ATLAS-159), ingested under the same committed-only, fail-closed
   contract — so a stub-minted ticket's pack resolves exactly what
   gate 4 resolved when it minted (ATLAS-162).
2. **ADRs:** ADR dependency targets of the ticket, plus any accepted ADR
   whose title tokens intersect the ticket's component/tags. Rendered as
   decision + consequences only (context and alternatives omitted).
   Cap: 5.
3. **Related tickets:** direct `depends_on` targets and dependents —
   key, title, status, one-line objective each. Cap: 8.
4. **Lessons:** `status: ACTIVE` only (ADR-0009), matched by tag
   intersection with the ticket's tags and ticket_type, ranked by
   confidence then recency. Cap: 3.
5. **Verbatim from the ticket:** objective, context, constraints,
   acceptance criteria, non-goals, test requirements, definition of done,
   risks.

## Token budget and compression ladder

- Default budget: 12,000 tokens (config). Estimator: chars/4 — recorded as
  `token_estimate`; precision is not required, monotonicity is.
- If over budget, compress in this order until under: (1) drop lesson
  bodies to titles; (2) drop related-ticket objectives to key+title;
  (3) trim doc sections to their first paragraph plus any code blocks
  containing commands; (4) drop ADR consequences. If still over budget,
  rendering **fails** — an over-budget pack is a planning smell
  (oversized ticket), reported as such rather than silently truncated.

## Rendered structure

`rendered_markdown` uses a fixed section order matching the Context Pack
JSON contract (data-model §8): Objective, Constraints, Acceptance
Criteria, Non-goals, Relevant Docs (extracted sections), ADRs, Related
Tickets, Lessons, Risks, Test Commands, Definition of Done. Fixed order
keeps packs scannable by humans and stable for prompt caching.

## Staleness and provenance

Every pack records `input_doc_shas` for each extracted doc at render time.
A pack is stale when any recorded SHA differs from the current tree; the
PM Engine re-renders stale packs only while the ticket is in
`Ready for Agent` (frozen afterwards — `symphony-integration.md#context-pack-delivery`).

## Validation

`atlas context validate <KEY>` (and the same checks inside the PM Engine
before promotion): all anchors resolve at the recorded SHAs; objective and
≥1 acceptance criterion present; ≥1 test command; token_estimate ≤ budget;
no DRAFT lessons included.

## CLI

`atlas context render <KEY> [--budget N] [--json]`,
`atlas context validate <KEY>`, `atlas context show <KEY>`.

## Open items

- LLM-assisted section summarisation as a compression rung — deferred
  until packs measurably exceed budget in practice.
- Component-tag taxonomy for lesson/ADR matching: seed manually; revisit
  when Phase 9 produces enough lessons to need ranking beyond
  confidence+recency.

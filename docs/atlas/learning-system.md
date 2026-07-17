# Learning System Design (Phase 9)

Status: Active design document for Phase 9. The Lesson schema lives in
`data-model-and-schemas.md` §3.6; promotion governance is ADR-0009.

## Boundary

The Learning System converts delivery outcomes into retrievable
organisational memory. It is the only component that writes Lessons, and
everything it writes is DRAFT until the operator promotes it. Knowledge
that belongs in canonical documents (playbooks, principle changes) reaches
them only via PR — never direct writes (ADR-0006).

## Extraction triggers

A lesson-extraction run fires on:

1. Ticket reaching `done` — success-pattern candidate (only when the
   delivery was notable: first-attempt verification pass after prior
   failures of the same ticket_type/tag, or an unusually fast cycle).
2. Ticket reaching `rejected`, or a PM failure-analysis event
   (review-cycle breach, dwell breach) — failure-pattern candidate.
3. Operator request: `atlas lessons extract <KEY>`.

`atlas lessons schedule` is the recurring loop for the automatic triggers. It
polls tickets in `done` or `rejected` status with no extraction attempt cursor,
and tickets with a `DWELL_BREACH` or `REVIEW_CYCLE` DebtItem recorded after the
last cursor. The extractor stamps `Ticket.lesson_extraction_attempted_at` on
each attempt, so scheduler and sync-triggered extraction share one retry cursor;
extractor failures are logged and do not stop the rest of the poll cycle.

The extractor is an LLM call over a bounded evidence bundle (ticket, agent
runs, PR review history, verification verdicts — not raw diffs beyond a
size cap), producing a schema-valid Lesson with `status: DRAFT`,
`confidence` left null, `source_ticket_id` set to the source ticket, and
`related_ticket_ids` left empty because citations are recorded only from later
pack usage. Lesson tags are anchored to the source ticket vocabulary: the prompt
shows the ticket's `tags` and `component`, asks the model to draw lesson `tags`
primarily from those facets, and permits at most two novel tags when the lesson
genuinely concerns something the ticket vocabulary does not name. This keeps
promoted lessons reachable through the existing tag/ticket_type retrieval rule
and keeps failure-pattern tags dense enough for the deterministic pattern
detector to cross its recurrence threshold. Extraction failures are logged,
never retried into noise.

## Promotion workflow

`atlas lessons review` lists DRAFTs; `atlas lessons review --stale` lists
ACTIVE lessons due for review. Per lesson the operator can **promote** with
`atlas lessons promote <LESSON_ID> --confidence <0.0-1.0>` (sets ACTIVE,
operator assigns confidence at promotion — confidence is an operator
judgement, not a model output), **edit then promote** by manually editing the
stored lesson before promotion, **reject** with `atlas lessons reject
<LESSON_ID>` (ARCHIVED, retained for audit; v1 pattern-candidate detection
counts DRAFT and ACTIVE lessons only), **merge** with
`atlas lessons merge <DRAFT_ID> --into <ACTIVE_ID>`, or **archive** an obsolete
ACTIVE lesson with `atlas lessons archive <LESSON_ID>`. Promotion is the
single human gate between agent experience and future agent context.

## Retrieval interplay

Only ACTIVE lessons are retrievable (context-renderer.md, cap 3, ranked by
confidence then recency). Two feedback signals are recorded to keep memory
honest:

- **Citation:** when a ticket whose pack included lesson L completes
  successfully, L's `related_ticket_ids` gains the completing ticket as usage
  evidence. The lesson's immutable `source_ticket_id` remains separate
  provenance.
- **Staleness review:** lessons included in ≥10 packs with zero
  subsequent operator re-confirmation surface in `atlas lessons review
  --stale` for re-promotion or archive. (Automatic confidence decay is
  deliberately not v1 — see Open items.)

## Pattern detection

Deterministic heuristics over lessons and the code-quality debt register
(once ATLAS-117 exists; no LLM): recurrence of the same tag across ≥3
DRAFT or ACTIVE failure-pattern lessons, or the same code-quality
debt-register category recurring across tickets, raises a pattern-candidate
flag in `atlas lessons report`. Delivery-anomaly `DebtItem.anomaly_type`
is not a code-quality debt category (ADR-0011). Patterns are inputs to
playbooks and to planning (a recurring failure tag is a strong signal a
doc or lint rule is missing — the harness-engineering response).

## Playbooks

`atlas lessons playbook <tag>` drafts a playbook from the ACTIVE lessons
under a tag into `docs/atlas/playbooks/<tag>.md` **as a PR branch**, which
the operator reviews and merges. Merged playbooks are canonical docs: the
planner and context renderer pick them up through normal doc ingestion,
closing the loop Docs → Delivery → Lessons → Docs.

## Delivery analytics

`atlas lessons report`: lessons by category/status and tag, ACTIVE citation
counts from citations-only `related_ticket_ids`, deterministic pattern
candidates, promotion backlog age, and dwell-breach rows. Markdown is the
default CLI output; `--json` emits the same data for machine consumers. The
command is a pure reader with no writes and no LLM calls.

## Open items

- Confidence decay function: design only after citation data exists;
  premature decay formulas are guesses.
- Embedding-based lesson retrieval: deferred with the rest of vector
  search (data-model §14).
- Whether playbook generation should also propose doc-linter rules
  (promote-the-rule-into-code) — promising, revisit after the first three
  playbooks.

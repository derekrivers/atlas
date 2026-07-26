---
title: "Gate rejected-trigger lesson extraction on whether work was attempted"
objective: "Stop a bookkeeping status change into `rejected` from manufacturing a DRAFT lesson and spending a model call. Extraction on the rejected trigger fires only when the ticket was actually worked — mirroring the notability gate the done trigger already has."
context: "Observed live on 2026-07-26. A triage retired 46 tickets stranded in `needs_human_decision` by the ATLAS-203 create-path default-state accident; the reconciling ticks pulled them to `rejected` and the extractor produced 45 DRAFT lessons, each an invented causal story about a delivery that never happened — `ATLAS-40`: 'CLI tickets rejected without agent runs or review cycles indicate missing pre-implementation validation gate'; `ATLAS-39`: 'Feature ticket rejected without agent execution or review — scope or priority misalignment at planning stage'. Both false: those tickets were retired because of a tracker default-state bug. All 45 were archived by raw SQL because `atlas lessons review` is read-only. The asymmetry is real and deliberate in the current design: `extract_lesson_for_ticket` (atlas/learning/extractor.py:428) gates `ExtractionTrigger.DONE` behind `notable_done_ticket`, and `learning-system.md` 'Extraction triggers' item 1 carries the qualifier '(only when the delivery was notable: ...)' while item 2 — rejected and PM failure analysis — carries none. So the code faithfully implements the doc, and this ticket changes BOTH. That is why it is a design change, not a defect repair. Pre-ruled decisions (operator-ratified in reviewer session 2026-07-26; land them, do not relitigate): D-1 the gate lives INSIDE `extract_lesson_for_ticket`, beside the existing done gate, NOT at the call site — `atlas/pm/sync.py`'s `_pull` and `atlas lessons schedule` both route through the extractor, and a call-site gate would cover one path and silently miss the other. D-2 the predicate is `notable_rejected_ticket(ticket, *, agent_runs, verification_checks)`, returning True iff the ticket has at least one AgentRun OR at least one VerificationCheck row: a rejection carries a failure lesson only when something was attempted against the ticket, and a ticket rejected before any dispatch is a planning event with no delivery history to learn from. Deterministic, storage-free, and shaped like its `notable_done_ticket` sibling. D-3 `force=True` bypasses the gate exactly as it does for done, so `atlas lessons extract <KEY>` remains the operator's unconditional escape hatch. D-4 a gated non-extraction still stamps `Ticket.lesson_extraction_attempted_at`, matching the done gate's existing behaviour, so the scheduler does not re-poll the same ticket every cycle. D-5 the `PM_FAILURE_ANALYSIS` and `OPERATOR_REQUEST` triggers are untouched — a dwell or review-cycle breach is evidence of attempted work by construction. D-6 `learning-system.md` item 2 gains the notability qualifier in the same change, by rewriting the sentence rather than appending a caveat (deletion over annotation). D-7 no retroactive archival or re-extraction of the 45 lessons already archived on 2026-07-26; they stay archived and out of scope."
ticket_type: tech_debt
epic_ref: "ATLAS-E11"
risk_level: medium
component: learning
acceptance_criteria:
- "A ticket pulled to `rejected` with zero AgentRuns and zero VerificationCheck rows produces NO Lesson row and makes NO model call, asserted against a fake lesson client that records its call count (count must be 0)."
- "A ticket pulled to `rejected` with at least one AgentRun extracts exactly as today: one DRAFT Lesson persisted, one model call."
- "The OR boundary is covered: a ticket with zero AgentRuns but at least one VerificationCheck row extracts."
- "`force=True` extracts for a rejected ticket that fails the predicate, proving the operator path is unconditional."
- "A gated non-extraction still stamps `lesson_extraction_attempted_at`, asserted by reading the ticket back after the call."
- "The done, PM-failure-analysis, and operator-request paths are behaviour-identical: the existing extractor test suite passes unmodified."
- "`docs/atlas/learning-system.md` 'Extraction triggers' item 2 states the notability condition and its rationale; the doc linter passes."
non_goals:
- "No change to `notable_done_ticket`, the done gate, or the fast-cycle/first-attempt predicates."
- "No change to the extractor's prompt, bundle assembly, schema validation, or persistence."
- "No CLI for archiving or rejecting DRAFT lessons — that is a separate stub."
- "No retroactive archival, re-extraction, or repair of existing lessons, including the 45 archived on 2026-07-26."
- "No change to `atlas lessons schedule`'s polling query; the gate is inside the extractor so the scheduler inherits it without edit."
- "No new `LessonStatus` value and no change to the promotion workflow."
test_requirements:
- "Fixture-driven with the existing fake lesson client; `ATLAS_LIVE_TESTS=0` at CI parity; seeded defects use `assert 1 == 2`, never `assert False` (ruff B011)."
- "The call-count assertion is the milestone anchor: the seeded defect that removes the gate must make it fail, proving the criterion bites."
- "One negative per branch of the OR predicate, so neither half can be dropped silently."
definition_of_done:
- "Every acceptance criterion evidenced by a named test; full gate sweep green with `ATLAS_LIVE_TESTS=0`; enumeration pins in `tests/test_acceptance.py` and `tests/test_schemas_export.py` confirmed unchanged; `learning-system.md` updated in the same change; PR title carries the minted key."
---

# A rejection is not a lesson

Forty-five invented lessons from one bookkeeping sweep. The done trigger already
knows to ask whether anything happened; the rejected trigger never learned to.

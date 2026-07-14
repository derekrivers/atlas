---
title: "Degraded definition-only push must not advance the sync cursor; repair pack-absent pushed tickets"
objective: "A push degraded to definition-only by an enumerated pack-render failure leaves the sync cursor unstamped so the embed retries on the next tick once the condition clears; a one-shot repair path re-embeds already-stamped tickets whose Linear description lacks the pack header — converting transient render failures back into transient states."
context: "Live bite, 2026-07-13, PlanRun 5fd330e1: `atlas apply` retired an inbox stub to processed/ uncommitted; the tick's pack loader correctly refused (DirtyInputError, ADR-0006 committed-only) and degraded ATLAS-168's push to definition-only — but the degraded push still stamped the cursor via mark_definition_pushed, and inbound status changes deliberately never bump updated_at (repositories.py cursor-directionality rule), so no future tick re-embeds: a one-tick dirty tree became a permanently pack-less ticket. This closes the deferred pack-freshness carry-forward (Phase 8 closure §5, pointer at the ATLAS-164 gate record). Pre-ruled decisions: D-1 on an enumerated degradation (the D-2 render-failure classes in sync.py) the definition IS still pushed (Linear must not show a stale definition) but mark_definition_pushed is NOT called — updated_at stays ahead of linear_synced_at and the next tick retries the full embed; the retry is naturally bounded because it fires only while the cursor is unstamped. D-2 non-enumerated failures keep their existing semantics unchanged. D-3 repair path: a sweep over pushable tickets with external_linear_id set whose current Linear description lacks the `ATLAS CONTEXT PACK v1` header re-renders and re-pushes the full embed, stamping normally on success — exposed as `atlas pm sync --repair-packs` (or the house-preferred flag spelling), idempotent, zero writes when every description carries the header. D-4 detection reads the description already fetched by the batched pull where possible; the repair must not blow the per-tick request budget (ATLAS-148) — if a dedicated fetch is unavoidable it is repair-mode-only, never on the plain tick. D-5 the degradation log line additionally names the ticket as cursor-unstamped, so `atlas pm report`'s anomaly section can count still-degraded tickets. D-6 no changes to the fail-closed loader, the DirtyInputError contract, or the D-2 enumeration itself — this ticket changes only what happens AFTER a correct refusal."
ticket_type: "bug"
epic_ref: "ATLAS-E10"
acceptance_criteria:
  - "Fixture tick with a dirty pack input pushes definition-only and does NOT stamp linear_synced_at; a second tick after the input is committed pushes the full embed and stamps; the same-state third tick pushes nothing (no-op bound unchanged)."
  - "Negative: a successful embed push stamps exactly as today (existing sync tests pass unmodified); a non-enumerated failure path is byte-identical to current behaviour."
  - "Repair sweep over a fixture board with one pack-absent description re-embeds exactly that ticket and stamps it; re-running the sweep is a zero-write no-op."
  - "The plain tick's Linear request count for the no-op board is unchanged (ATLAS-148 budget assertion holds)."
  - "The degradation log/report names cursor-unstamped tickets; pm-engine-and-linear-sync.md documents the retry-until-embedded rule and the repair flag in the same change."
non_goals:
  - "No changes to pack rendering, the compression ladder, DirtyInputError, or the enumerated-failure list; no automatic periodic repair (operator-invoked only, v1); no Linear description hand-edits; no cursor changes for the status direction."
test_requirements:
  - "Fixture-driven, ATLAS_LIVE_TESTS=0; seeded defects use assert 1 == 2 (B011); enumeration pins hold."
definition_of_done:
  - "All acceptance criteria evidenced by named tests; full gate sweep green; ATLAS-168's live repair is performed by the operator post-merge via the new flag and recorded on the ticket; PR title carries the minted key."
---

# Degraded embeds retry; stamped victims get repaired

A correct refusal must stay transient. Stamping on degradation made it permanent.

---
title: "Stamp Ticket.completed_at when a ticket transitions to done"
objective: "Populate `Ticket.completed_at`, which no code path currently writes, so the field the read API and the delivery report already expose stops being permanently null. Stamped by the sole post-creation status writer, on the transition into `done` only, without bumping `updated_at`."
context: "Found during the 2026-07-26 Needs Human triage. `Ticket.completed_at` is declared on the model (atlas/core/models/ticket.py), rendered into `docs/planning/tickets.yaml`, and served over HTTP by `atlas/api/presenters.py:97` — but nothing assigns it after creation. `atlas/planning/apply.py:323` sets it to `None` at mint; `TicketRepo` contains no `completed_at` write at all (the hits in `atlas/pm/agent_runs.py` and `atlas/pm/report.py` are `AgentRun.completed_at`, a different model, and `atlas/verification/reports.py:151` writes a VerificationCheck field). Live evidence: of 102 tickets in `done`, exactly six carry a value — ATLAS-187..192, set by hand with raw SQL during a namespace incident. Every other done ticket reads null, including the four closed through the verification path on 2026-07-26. Scope note that keeps this small: the learning system does NOT depend on the fix. `_cycle_seconds` (atlas/learning/extractor.py:280) reads `ticket.completed_at or ticket.status_entered_at`, so `_unusually_fast_cycle` already works off the dwell clock; an earlier reading of this defect claimed the fast-cycle trigger could never fire, and that was wrong. What is actually broken is narrower and still worth closing: a documented model field and a v1 read-API field that are structurally always null. Pre-ruled decisions (operator-ratified in reviewer session 2026-07-26; land them, do not relitigate): D-1 the writer is `TicketRepo.apply_linear_status`, the sole post-creation status writer, so the stamp shares the transaction and the injected `now` with the status change and no second writer appears. D-2 `done` ONLY, never `rejected`: `completed_at` denotes delivery, and a rejection is a closure. Rejected tickets keep their cycle measurement through the existing `status_entered_at` fallback. D-3 the write must NOT bump `updated_at`, exactly like `status_entered_at`, `linear_synced_at`, and `last_observed_linear_state_id` — an inbound observation that bumped the definition cursor would trigger a spurious Linear re-push. D-4 stamped only on a REAL transition into `done`, mirroring the `status_entered_at` rule: a set-to-same re-observation is a no-op and must not restamp, so the value marks first arrival at done and stays stable across ticks. D-5 no backfill of the existing done rows and no migration — the column already exists in the baseline. Backfilling ~96 historical rows would invent timestamps Atlas never observed; if wanted it is a separate operator-gated decision, exactly as ATLAS-203 D-6 held for the mis-stated tickets. D-6 completion ownership is unchanged: `complete_verified` still performs no Atlas-side status write, so the stamp lands on the pull that reconciles Done, not in the Linear write."
ticket_type: tech_debt
epic_ref: "ATLAS-E6"
risk_level: low
component: atlas.storage
acceptance_criteria:
- "A pull that transitions a ticket from any non-terminal status to `done` sets `completed_at` to the call's injected `now`, asserted by reading the ticket back."
- "A second pull observing `done` again leaves `completed_at` byte-identical to the first value, proving the set-to-same path does not restamp."
- "A transition to `rejected` leaves `completed_at` NULL."
- "The stamp does not bump `updated_at`: a test captures `updated_at` before the transition and asserts it is unchanged after. This is the regression that would otherwise cause a spurious definition re-push."
- "`GET /api/v1/tickets/{key}` returns a non-null `completed_at` for a ticket completed after this change, asserted through the existing API test client."
- "Pre-existing `done` rows are untouched: a fixture row seeded with `completed_at` NULL and status `done` still reads NULL after a tick that observes it as `done` (no retroactive stamping)."
non_goals:
- "No backfill, migration, or repair of the ~96 existing `done` rows with a null value, and no change to the six hand-set ATLAS-187..192 rows."
- "No `completed_at` on `rejected`, and no new terminal-timestamp field."
- "No change to `status_entered_at`, `linear_synced_at`, `last_observed_linear_state_id`, `review_cycle_count`, or the `updated_at` discipline they share."
- "No change to `complete_verified`, the Done gate, the required-check matrix, or completion ownership."
- "No change to `_cycle_seconds`, `_unusually_fast_cycle`, or any learning-system predicate — the existing fallback stays as the behaviour for rows without a value."
- "No change to the `atlas pm report` cycle-time computation, which reads the TicketStatusTransition log rather than this field."
test_requirements:
- "Repository-level tests against an in-memory database, plus one API-level test through the existing test client; `ATLAS_LIVE_TESTS=0`; seeded defects use `assert 1 == 2` (ruff B011)."
- "The `updated_at`-unchanged assertion and the no-restamp assertion are both required: each has its own seeded defect that must make it fail."
definition_of_done:
- "Every acceptance criterion evidenced by a named test; full gate sweep green with `ATLAS_LIVE_TESTS=0`; enumeration pins in `tests/test_acceptance.py` and `tests/test_schemas_export.py` confirmed unchanged; the `Ticket` model docstring records the writer and the no-`updated_at`-bump rule alongside its sibling cursor fields; PR title carries the minted key."
---

# A field nobody writes

Declared on the model, rendered into the planning snapshot, served over HTTP,
and null in 96 of 102 rows because no code has ever assigned it.

---
title: "atlas lessons show: the promotion gate cannot read what it rules on"
objective: "`atlas lessons show <LESSON_ID>` prints a lesson's full stored record — title, category, status, confidence, tags, problem, solution, outcome, provenance and citations, timestamps — so the operator can read a lesson before promoting it, with `--json` for machine consumers."
context: "Live finding, 2026-07-17, during the first promotion session in Atlas history. `atlas lessons review` and `atlas lessons review --json` emit only `id`, `title`, `source_ticket`, `created_at`, `status` (`_lesson_review_row`, atlas/cli.py). There is no other read surface. ADR-0009 makes promotion the single human gate between agent-authored experience and future agent context — and the CLI asks that gate to rule on a title. Reading a lesson body during the closure session required `sqlite3 -line .atlas/atlas.db \"SELECT problem, solution, outcome FROM lessons WHERE id=...\"` — raw SQL against the store, with dashes stripped from the UUID. Two consequences observed live: (1) the operator cannot exercise the design doc's documented `edit then promote` path without hand-writing UPDATE statements; (2) an operator who promotes without reading is promoting a model's unreviewed text into every future agent's context pack, which is precisely the leak ADR-0009 exists to prevent — the gate is nominal if it cannot see. This is a governance gap, not a UX nit. Pre-ruled decisions: D-1 `show` is the detail view; `review` stays a scannable list — do NOT bloat `review` with bodies. D-2 `show` prints every stored field an operator needs to rule: title, category, status, confidence (or `-` when null), tags, problem, solution, outcome, related_adr_ids, provenance/citation ticket keys resolved to ATLAS-NN where resolvable, created_by, created_at, updated_at. D-3 `--json` emits the same record machine-readably. D-4 house error contract: a non-UUID id, an unknown id, and a cold/never-migrated database are each a clean one-line `EXIT_PRECONDITION`, never a traceback (the ATLAS-108 lesson, and the pattern `atlas evidence show` already follows). D-5 pure reader: no writes, no LLM calls, no Linear calls. D-6 accepts the UUID in canonical dashed form as printed by `review` — the operator must never have to strip dashes."
ticket_type: "feature"
epic_ref: "ATLAS-E11"
acceptance_criteria:
  - "`atlas lessons show <id>` prints title, category, status, confidence, tags, problem, solution, outcome, related_adr_ids, ticket keys, created_by, created_at, updated_at for a stored lesson; the dashed UUID printed by `atlas lessons review` is accepted verbatim."
  - "`--json` emits the same record as machine-readable JSON with the same field coverage."
  - "Negative: a non-UUID id, an unknown-but-valid UUID, and a cold database each produce a clean one-line error on stderr and EXIT_PRECONDITION — no traceback (seed the probe with `assert 1 == 2`, B011)."
  - "A lesson with `confidence` null renders a placeholder rather than `None`; a lesson with empty tags/adr ids renders cleanly."
  - "The command performs no writes: a fixture asserts the store is byte-identical before and after."
non_goals:
  - "No change to `atlas lessons review`'s row shape. No edit/update subcommand (editing before promotion stays operator-owned; see the separate provenance ticket for schema work). No new fields. No LLM calls. No pager or formatting framework."
test_requirements:
  - "Fixture-driven, `ATLAS_LIVE_TESTS=0`; seeded defects use `assert 1 == 2` (B011); enumeration pins hold; full gate sweep green."
definition_of_done:
  - "All acceptance criteria evidenced by named tests; learning-system.md's promotion-workflow section names `show` as the read surface the gate uses; full gate sweep green; PR title carries the minted key."
---

# The gate must see its cargo

A human gate ruling on a title is not a gate.

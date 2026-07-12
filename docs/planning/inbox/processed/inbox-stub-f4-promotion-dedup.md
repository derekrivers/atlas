---
title: "Promotion dedup: collapse model re-emission of committed inbox stubs (F-4)"
objective: "A planner draw that re-emits a committed inbox stub alongside its deterministic promotion produces exactly one ticket ADD, not two."
context: "Two live reproductions: the declined 2026-07-08 double-emission (E6/E8 pairs) and the applied duplicate mints ATLAS-149/150 (exact-title copies of 147/148, cancelled same day — debt register: 'Duplicate mints'). Root cause: promote_inbox_stubs injects a ticket per stub while the model, which sees the stubs in its corpus, may independently emit tickets for the same work; the reconciler treats them as distinct ADDs. Critical finding baked in: the 149/150 duplicates carried no dependency edges, so the diff's dependency section could not surface them — dedup must act at ticket identity, not edges. The stub's source_anchor is the identity key: a promotion-injected ticket and a model-emitted ticket citing the same stub anchor are the same work."
ticket_type: "bug"
epic_ref: "ATLAS-E3"
acceptance_criteria:
  - "During reconciliation, a model-emitted proposal ticket whose source_anchor matches a promotion-injected ticket's anchor is collapsed into the promotion ticket (deterministic content wins over generated); the diff records one line noting the collapse and which model ticket was absorbed."
  - "Seeded regression (the 149/150 shape): a fixture with two committed stubs and a model proposal re-emitting both yields a diff with exactly two ticket ADDs; pre-fix this fixture yields four."
  - "The edgeless case is covered explicitly: the re-emitted duplicates in the fixture carry no dependency edges and are still collapsed."
  - "Negative: model tickets citing distinct anchors (or no stub anchor) are not collapsed."
  - "Dependency edges the model attached to its duplicate are re-pointed to the surviving promotion ticket, deduplicated against existing edges."
non_goals:
  - "Dedup of model-vs-model duplicates with no stub anchor (title-similarity heuristics) — out of scope."
  - "Any change to promotion content derivation or to add-only semantics."
test_requirements:
  - "Reconciler-level unit tests with fixture proposals (no planner calls, ATLAS_LIVE_TESTS=0); seeds use `assert 1 == 2` (ruff B011); the two-reproduction shapes above are the named fixtures."
definition_of_done:
  - "All acceptance criteria evidenced by named tests; full gate sweep green; the reconciliation section of the planning spec documents the collapse rule in the same change."
---

# Promotion dedup (F-4)

One stub, one ticket — regardless of what the model re-emits.

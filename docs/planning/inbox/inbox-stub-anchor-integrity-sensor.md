---
title: 'Anchor-integrity sensor: store and render source_anchors must resolve'
objective: 'Make dangling source_anchors detectable before a plan run fails on them: every anchor recorded in the store and the planning renders must resolve to a heading in the corpus at HEAD.'
context: 'ATLAS-031M repaired four store anchors that had silently died - the Phase 9 closure PR renamed a roadmap heading, and ATLAS-192''s record anchored to README.md, which is not in the section 2.1 planner corpus (_ROOT_DOCS in atlas/planning/ingestion.py). Neither was detectable until `atlas plan --stubs-only` failed gate 4 on the backlog echo, blocking all minting until repaired. Gate 4 is the enforcement point but it fires only at plan time, so a heading rename between plan runs snaps pins invisibly. Pre-ruled decisions (operator-ratified in reviewer session 2026-07-24; land them, do not relitigate): D-1 the check reuses the existing anchor machinery (atlas.core.anchors parse_headings/slugify and the corpus definition in atlas/planning/ingestion.py) and never reimplements slugging or corpus rules. D-2 an anchor naming a document outside the indexed input set is a distinct finding from an anchor naming an indexed document with no matching heading - both fail, with different codes. D-3 the sensor''s home (doc-linter check family versus a standalone CI step) is proposed at the plan gate, with the deciding constraint that it must run without a database when reading renders.'
ticket_type: tech_debt
epic_ref: ATLAS-E1
risk_level: low
component: tooling
depends_on:
- inbox-stub-doc-linter-v3-integrity.md
acceptance_criteria:
- The sensor passes on the current tree.
- 'Seeded fixtures prove it bites: an anchor to a renamed heading fires; an anchor to a document outside the indexed input set fires with the distinct code.'
- The exact four anchors ATLAS-031M repaired are reproduced as a regression fixture and fire before repair.
non_goals:
- No repairs of live data - the sensor reports. No changes to gate 4, the corpus definition (_ROOT_DOCS) or the slug algorithm. No new anchor syntax.
test_requirements:
- Fixture-driven; ATLAS_LIVE_TESTS=0; seeded defects use assert 1 == 2 (B011).
definition_of_done:
- All acceptance criteria evidenced by named tests; full gate sweep green; PR title carries the ticket key.
---

# Anchor-integrity sensor: store and render source_anchors must resolve

Minted from the reviewer session of 2026-07-24; decisions in `context` are operator-ratified.

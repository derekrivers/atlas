---
title: "Add a delivery-loop marker to the README"
objective: "README states that changes to this repository flow through the Atlas delivery loop."
context: "Smoke B operator runbook, Phase 1 — a deliberately small, inert docs change whose point is to exercise the plan -> apply -> promotion loop, not the work itself."
ticket_type: "documentation"
epic_ref: "ATLAS-E1"
acceptance_criteria:
  - "README.md contains a \"Delivery loop\" heading with exactly one paragraph under it."
  - "The paragraph describes the loop as plan -> pack -> dispatch -> PR -> evidence -> verification -> Done."
  - "The paragraph names the acceptance gate as operator-owned (ADR-0008)."
non_goals:
  - "No file other than README.md is modified."
test_requirements:
  - "A doc-level check confirms the Delivery loop heading and its single paragraph exist."
definition_of_done:
  - "README.md carries the Delivery loop subsection and the doc check passes."
---

# Smoke B fixture: add a delivery-loop smoke marker to the README

Source: Smoke B operator runbook, Phase 1 (hand-authored fixture stub; the
smoke tests the LOOP, not the work). The front-matter above is the machine
contract deterministic promotion reads (ATLAS-146); this prose is the
human-readable companion.

Objective: add a single short "Delivery loop" subsection to README.md stating
that changes to this repository flow through the Atlas delivery loop
(plan -> pack -> dispatch -> PR -> evidence -> verification -> Done). One
paragraph, no other files touched.

Acceptance criteria:
- README.md contains a "Delivery loop" heading with exactly one paragraph
  under it describing the loop as above.
- No file other than README.md is modified.
- The paragraph names the acceptance gate as operator-owned (ADR-0008).

Deliberately small and inert: a docs-only change with falsifiable ACs, sized
so the interesting part of the smoke is the machinery around it.

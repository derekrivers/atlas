# Smoke B fixture: add a delivery-loop smoke marker to the README

Source: Smoke B operator runbook, Phase 1 (hand-authored fixture stub; the
smoke tests the LOOP, not the work).

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

---
title: "Failure-analysis remainder: detected anomaly patterns file DRAFT lessons"
objective: "Recurring delivery anomalies (review cycling, dwell breaches) produce DRAFT lesson records an operator can promote or discard — closing Phase 8 seed 86's undelivered half."
context: "Detection shipped in July (review-cycle counting, dwell horizons, anomaly DebtItems — ATLAS-120/121/126); the remainder is the filing: when a pattern crosses its threshold, write a DRAFT lesson row capturing the pattern, the tickets exhibiting it, and the evidence pointers — for the operator, not for automation. Pre-ruled decisions: DRAFT lessons are store rows (the lessons table the E11 schema already carries; if absent, this ticket adds the minimal model+migration and E11 inherits it), never documents (no docs/ writes — ADR-0007); filing runs in the sync tick's anomaly step, idempotent per pattern instance (keyed on pattern type + ticket set, no duplicate drafts on re-tick); thresholds are the existing detection thresholds — this ticket adds NO new detection, only filing of what already fires; drafts are surfaced via `atlas pm report` (a drafts section) and a `lessons list --draft` read path if the lessons CLI exists, else the report section alone; promotion/discard of drafts is explicitly E11 territory and OUT of scope."
ticket_type: "feature"
epic_ref: "ATLAS-E10"
acceptance_criteria:
  - "A fixture tick over a board exhibiting a review-cycling breach files exactly one DRAFT lesson naming the pattern, the ticket keys, and the DebtItem evidence ids; a second tick over unchanged state files zero more."
  - "A dwell-breach fixture files its own DRAFT with the same idempotence."
  - "Negative: anomalies below threshold file nothing; the detection thresholds are byte-identical (existing detection tests pass unmodified)."
  - "`atlas pm report` renders a drafts section from the rows; the no-op tick request bound is unchanged."
non_goals:
  - "No new detection, no threshold changes, no lesson promotion/discard workflow, no document writes, no E11 scheduler work."
test_requirements:
  - "Fixture-driven, ATLAS_LIVE_TESTS=0, seeds `assert 1 == 2` (B011)."
definition_of_done:
  - "All acceptance criteria evidenced by named tests; full gate sweep green; pm-engine-and-linear-sync.md's anomaly section documents the filing step in the same change."
---

# DRAFT lessons from anomalies

Detection already speaks; this makes it write.

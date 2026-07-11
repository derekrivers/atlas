---
title: "Stubs-only plan mode: mint committed inbox stubs without a model call"
objective: "atlas plan --stubs-only produces an apply-ready PlanRun from committed inbox stubs alone — zero model calls, zero generation cost, no double-emission surface."
context: "Minting hand-authored stubs today requires a full planner draw whose only planning contribution is to echo the backlog so promotion can inject the stubs — every such run costs a full generation (£5 at current corpus size) and re-exposes the F-4 double-emission surface, since the model sees the stubs in its corpus and may re-emit them alongside their deterministic promotion. A stubs-only mode skips generation entirely: the proposal is the verbatim keyed backlog echo plus the promoted stubs (promotion already re-states A-1 epics from the backlog on exactly this pattern), flowing through the ordinary gates -> reconcile -> PlanRun path. Deterministic by construction (ADR-0005: pure code, no model), so the diff is exactly the stub ADDs. Complements the F-4 reconciler dedup rather than replacing it — dedup still guards full generative runs; this mode makes the common mint-a-batch case free and duplicate-proof."
ticket_type: "feature"
epic_ref: "ATLAS-E3"
acceptance_criteria:
  - "atlas plan --stubs-only with N committed stubs yields a PlanRun whose diff is exactly N ticket ADDs (plus no-op A-1 epic re-statements); proven by a pipeline-level test whose model client is a fake asserting zero generation calls."
  - "An empty inbox under --stubs-only is a clean-exit precondition failure with a message naming the empty inbox (nothing to mint), never an empty-diff PlanRun."
  - "PlanRun provenance records the mode: a stubs-only run is distinguishable from a generative run in the stored record."
  - "--stubs-only and --staged are mutually exclusive: passing both is an argparse error, exercised by a CLI test."
  - "With the flag absent, the generative path is byte-identical to today (existing pipeline tests pass unmodified)."
  - "atlas apply consumes a stubs-only PlanRun unchanged, including the stub retirement lifecycle."
non_goals:
  - "No dedup logic — the promotion-dedup ticket (F-4) owns reconciler-level collapse."
  - "No prompt or staged-generation changes; the generative path is untouched."
  - "No stub subset selection or interactive stub picking — the committed inbox is the batch."
test_requirements:
  - "Pipeline- and CLI-level tests with fake clients and tmp-path repos (no live model calls, ATLAS_LIVE_TESTS=0); the zero-generation-calls assertion is the milestone anchor."
definition_of_done:
  - "All acceptance criteria evidenced by named tests; full gate sweep green; running-atlas-plan.md and the planning spec document the mode, its provenance marker, and the cost rationale in the same change."
---

# Stubs-only plan mode

Minting operator-authored stubs is deterministic work; it should not
cost a generative draw or expose a double-emission surface to get keys
assigned.

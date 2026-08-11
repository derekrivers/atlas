# ROADMAP.md

The canonical roadmap lives at
`docs/atlas/implementation-roadmap.md`. Phase closure is recorded
in `docs/closure/` — Phases 1 through 13 are closed (Phase 13 closed
2026-08-11).

The original bootstrap milestone — a dependency-aware backlog
generated through the plan/apply loop with stable ticket identity
(AT-1..AT-7, `docs/atlas/planning-engine-specification.md`) — is
proven and closed; see the closure reports.

Current work: Phase 14 — Review Acceptance Console. Phase 13's authenticated,
audited loopback write boundary is closed by
`docs/closure/phase-13-closure-report.md`; Phase 14 consumes that boundary but
does not expand it to merge, GitHub or Linear writes. The architectural
direction through Phase 20 is recorded in
`docs/atlas/phase-13-20-programme-horizon.md`; Phases 14–15 remain the active
Wave A delivery graph, while Phases 16–20 remain gated programme horizons. The
hand-delivered Planning Batch Integrity Guard continues to validate exact
paths, dependency identity/order/cycles and exact batch-manifest coverage in
both plan and apply. Atlas retains no automatic conflict-resolution,
plan-approval, review, merge, permission-expansion or deployment authority.

Committed `main` retains the repository-owned Symphony ceiling of three while
Phase 15 is delivered. The operator performs the controlled 3 → 5 → 7 → 10
ramp by changing `WORKFLOW.md` on the dedicated milestone branch only after
each preceding gate passes. A failed gate restores or retains the last proven
branch value, records the failure, leaves Phase 15 open and merges nothing to
`main`. Once ten passes, the milestone/closure change lands
`max_concurrent_agents: 10` on `main`; closure below ten is prohibited.

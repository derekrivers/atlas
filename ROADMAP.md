# ROADMAP.md

The canonical roadmap lives at
`docs/atlas/implementation-roadmap.md`. Phase closure is recorded
in `docs/closure/` — Phases 1 through 14 are closed (Phase 14 closed
2026-08-12).

The original bootstrap milestone — a dependency-aware backlog
generated through the plan/apply loop with stable ticket identity
(AT-1..AT-7, `docs/atlas/planning-engine-specification.md`) — is
proven and closed; see the closure reports.

Current work: Phase 15 — Multi-Agent Delivery Control. Phase 14's authenticated,
exact-head review-acceptance console is closed by
`docs/closure/phase-14-closure-report.md`; its live milestone proves the
browser/API workflow stops at advice for a manual GitHub merge and performs no
GitHub, Git, Linear, Symphony, schema or PM-sync mutation. The architectural
direction through Phase 20 is recorded in
`docs/atlas/phase-13-20-programme-horizon.md`; Phase 15 remains the active Wave
A delivery graph, while Phases 16–20 remain gated programme horizons. The
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

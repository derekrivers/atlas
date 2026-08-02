# ROADMAP.md

The canonical roadmap lives at
`docs/atlas/implementation-roadmap.md`. Phase closure is recorded
in `docs/closure/` — Phases 1 through 12 are closed (Phase 12 closed
2026-07-31).

The original bootstrap milestone — a dependency-aware backlog
generated through the plan/apply loop with stable ticket identity
(AT-1..AT-7, `docs/atlas/planning-engine-specification.md`) — is
proven and closed; see the closure reports.

Current work: Phase 13 — Governed Operator Actions is the next delivery phase.
The architectural direction through Phase 20 is recorded in
`docs/atlas/phase-13-20-programme-horizon.md`; Phases 13–15 form the next
rolling-wave planning batch, while Phases 16–20 remain gated programme
horizons. The detailed designs and governed ordered ticket inputs for Phases
13–15 are prepared as one Wave A dependency graph. They remain planning inputs
only until this change is reviewed, merged, proposed by `atlas plan
--stubs-only` and explicitly accepted through `atlas apply`. The hand-delivered
Planning Batch Integrity Guard satisfies pre-Wave-A Gate 0 by validating exact
paths, dependency identity/order/cycles and exact batch-manifest coverage in
both plan and apply. Atlas retains no
automatic conflict-resolution, plan-approval, review, merge, permission-
expansion or deployment authority.

Committed `main` retains the repository-owned Symphony ceiling of three while
Phase 15 is delivered. The operator performs the controlled 3 → 5 → 7 → 10
ramp by changing `WORKFLOW.md` on the dedicated milestone branch only after
each preceding gate passes. A failed gate restores or retains the last proven
branch value, records the failure, leaves Phase 15 open and merges nothing to
`main`. Once ten passes, the milestone/closure change lands
`max_concurrent_agents: 10` on `main`; closure below ten is prohibited.

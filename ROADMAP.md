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
horizons. Phase 13 still requires its detailed design and governed ticket-
planning change before implementation begins. Design preparation may proceed,
and the hand-delivered Planning Batch Integrity Guard satisfies the pre-Wave-A
Gate 0 by validating exact paths, dependency identity/order/cycles and exact
batch-manifest coverage in both plan and apply. Phase 13–15 detailed planning
may proceed through that repaired path. Atlas retains no
automatic conflict-resolution, plan-approval, review, merge, permission-
expansion or deployment authority.

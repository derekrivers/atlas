# ROADMAP.md

The canonical roadmap lives at
`docs/atlas/implementation-roadmap.md`. Phase closure is recorded
in `docs/closure/` — Phases 1 through 14 are closed (Phase 14 closed
2026-08-12). Phase 15.5 has a controlled PASS but remains
`PENDING_LIVE_AUTHORITY`. ATL-437's first published head exposed that the
trusted reconciler had no production PM-cadence caller; that sample is retained
as failed reachability evidence. The live window restarts on the remediated
final head and closure occurs only after its genuine system-tier handoff is
accepted and that exact head merges.

The original bootstrap milestone — a dependency-aware backlog
generated through the plan/apply loop with stable ticket identity
(AT-1..AT-7, `docs/atlas/planning-engine-specification.md`) — is
proven and closed; see the closure reports.

Current work: Phase 15 — Multi-Agent Delivery Control, with an interstitial
Phase 15.5 — Parallel Delivery Efficiency and Integration Control. Phase 14's
authenticated, exact-head review-acceptance console is closed by
`docs/closure/phase-14-closure-report.md`; its live milestone proves the
browser/API workflow stops at advice for a manual GitHub merge and performs no
GitHub, Git, Linear, Symphony, schema or PM-sync mutation. The architectural
direction through Phase 20 is recorded in
`docs/atlas/phase-13-20-programme-horizon.md`; Phase 15 remains the active Wave
A delivery graph. Phase 15.5 reduces duplicated local validation, releases
Symphony slots while CI is authoritative, protects conflict-prone integration
lanes and retains exact-head/current-main acceptance with operator-owned
mechanical rebase recovery before ATLAS-253 may begin the live ceiling ramp.
The ATLAS-259/260 synthetic no-rewrite route is FAIL and retired. Phases 16–20
remain gated programme horizons. The
hand-delivered Planning Batch Integrity Guard continues to validate exact
paths, dependency identity/order/cycles and exact batch-manifest coverage in
both plan and apply. Atlas retains no automatic conflict-resolution,
plan-approval, review, merge, permission-expansion or deployment authority.

Committed `main` retains the repository-owned Symphony ceiling of one while
Phase 15 remains open and Phase 15.5 awaits live closure. Phase 15.5 changes no
ceiling. Its closure is
the explicit operator release gate for ATLAS-253. The operator then performs
the controlled 1 → 3 → 5 → 7 → 10 ramp by changing `WORKFLOW.md` on the
dedicated milestone branch only after each preceding gate passes. A failed gate
restores or retains the last proven branch value, records the failure, leaves
Phase 15 open and merges nothing to `main`. Once ten passes, the
milestone/closure change lands
`max_concurrent_agents: 10` on `main`; closure below ten is prohibited.

ATLAS-263 is the Phase 15.5 closure milestone. Its fixed `IND-1..IND-4`
comparison, protected `LANE-A/LANE-B` collision and fault matrix must pass
without changing the ceiling. The first ATL-437 head
`dad520cf46c2c6ee2f51b95e0fa6e20660751a96` completed CI but remained in `CI
Pending` because `reconcile_ci_handoff()` was not reachable from `atlas pm sync`.
The remediation wires one deterministic CI-pending candidate per PM tick. A
complete board pull may safely catch the local mirror up from a Symphony-active
predecessor when the polling interval missed `In Progress` or `PR Open`; the
append-only transition records the actual observed edge with poll-compression
provenance and invents no intermediate state. Exact repository/PR/head identity
comes only from a complete issue-bound Linear GitHub attachment plus the
canonical product-scoped evidence pull that the supported PM tick performs for
that exact publication, never a title, branch, rollup, manual seed or required
reconstructed AgentRun. The exact observed evidence identities scope the
trusted reconciler and a successful workflow write ends the tick. The live
window restarts at the next final ATL-437 head: the agent enters `CI Pending`
and stops, the system-tier reconciler owns the determinate exit, and the
disabled Linear `PR opened -> In Progress` automation must show zero recurrence.
ATLAS-253 remains `Needs Human` until that exact-head window is accepted and
this closure change is merged.

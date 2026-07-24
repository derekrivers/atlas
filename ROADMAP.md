# ROADMAP.md

The canonical roadmap lives at
`docs/atlas/implementation-roadmap.md`. Phase closure is recorded
in `docs/closure/` — Phases 1 through 9 are closed (Phase 9 closed
2026-07-18).

The original bootstrap milestone — a dependency-aware backlog
generated through the plan/apply loop with stable ticket identity
(AT-1..AT-7, `docs/atlas/planning-engine-specification.md`) — is
proven and closed; see the closure reports.

Current work: the operator API phase — a read-only HTTP projection
surface (`atlas.api`) over the review queue and ticket board.
Writeable API actions are deferred to a later phase.

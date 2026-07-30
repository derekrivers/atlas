# ROADMAP.md

The canonical roadmap lives at
`docs/atlas/implementation-roadmap.md`. Phase closure is recorded
in `docs/closure/` — Phases 1 through 11 are closed (Phase 11 closed
2026-07-30).

The original bootstrap milestone — a dependency-aware backlog
generated through the plan/apply loop with stable ticket identity
(AT-1..AT-7, `docs/atlas/planning-engine-specification.md`) — is
proven and closed; see the closure reports.

Current work: Phase 12 — Mainline Integration Control. This phase adds the
governed operator-owned path for assessing and rebasing a stale post-handoff
PR while preserving exact-head evidence, protected publication and human
acceptance. It does not add automatic conflict resolution or merge authority.

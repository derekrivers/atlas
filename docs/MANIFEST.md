# Atlas Documentation Manifest

Consolidated set: Revision 1 (2026-06-10) merged into the canonical
documents. If anything conflicts, this manifest's canonical list wins, then
the master plan.

## Canonical active documents

Root control documents:

- `README.md`, `PRODUCT.md`, `ARCHITECTURE.md`, `ROADMAP.md`,
  `WORKFLOW.md`, `AGENTS.md`
- `CLAUDE.md` — pointer file for Claude Code; defers entirely to
  `AGENTS.md` and contains no rules of its own

Strategy and specification (`docs/atlas/`):

- `atlas-master-plan.md` — single strategic master plan
- `system-specification.md` — concise platform specification
- `planning-engine-specification.md` — plan/apply, reconciler, validation
  gates, PlanRun, acceptance tests AT-1..AT-7
- `planning-large-corpora.md` — design for generating a complete full-state
  proposal across multiple bounded model calls (the output-capacity boundary;
  ADR-0010)
- `implementation-roadmap.md` — executable delivery programme (Revision 1)
- `phase-13-20-programme-horizon.md` — rolling-wave architectural direction
  after Phase 12: bounded authority progression, dependencies, planning gates
  and milestone outcomes through the Atlas-managing-Atlas capstone
- `bootstrap-guide.md` — day-one bootstrap guide
- `symphony-integration.md` — delivered Phase 8 and Phase 12 integration
  design (state mapping, pack delivery, exact-head assessment,
  operator-owned rebase lane, acceptance freshness and retry seam)

Phase design documents (one per engine; phase-readiness rule below):

- `docs/architecture/knowledge-core.md` — Phase 1: storage, render format,
  schema generation, append-only and trust enforcement
- `docs/atlas/dependency-engine.md` — Phase 3: graph semantics, readiness,
  critical path, validation
- `docs/atlas/pm-engine-and-linear-sync.md` — Phase 4: sync loop, field
  ownership, follow-up inbox, anomaly detection
- `docs/atlas/context-renderer.md` — Phase 5: retrieval rules, token
  budget, compression ladder, staleness
- `docs/atlas/evidence-pipeline.md` — Phase 6: poller, job-name
  convention, status normalisation, retention
- `docs/atlas/verification-engine.md` — Phase 7: required-check matrix,
  evaluation semantics, verdicts
- `docs/atlas/learning-system.md` — Phase 9: extraction triggers,
  promotion workflow, playbooks
- `docs/atlas/operator-api.md` — Phase 10: read-only HTTP contract, route
  boundaries, binding constraints, deferred capabilities
- `docs/atlas/operator-ui.md` — Phase 11: operator UI views, framework
  adoption boundary, additive v1 read routes, testing contract
- `docs/atlas/governed-operator-actions.md` — Phase 13: loopback operator
  authentication, server-owned actor context, idempotent commands, append-only
  receipts and lesson disposition writes
- `docs/atlas/review-acceptance-console.md` — Phase 14: immutable exact-head
  acceptance sessions, evidence/confirmation/verification ordering and the
  browser advisory boundary before manual merge
- `docs/atlas/multi-agent-delivery-control.md` — Phase 15: operator-owned
  capacity policy, coherent occupancy, deterministic admission, fail-closed
  Linear promotion and the controlled agent-ceiling ramp
- `docs/atlas/parallel-delivery-efficiency-and-integration-control.md` — Phase
  15.5: scoped local confidence with CI authority, CI-pending slot release,
  protected integration lanes and no-rewrite exact-base acceptance

Playbooks (generated canonical docs):

- `docs/atlas/playbooks/linear-sync.md` — generated playbook for linear-sync lessons

Architecture (`docs/architecture/`):

- `technical-architecture.md` — detailed engineering architecture
- `data-model-and-schemas.md` — models, tables, and contracts (includes
  PlanRun, evidence trust fields, lesson status)

Decisions (`docs/decisions/`):

- ADR-0001 Python platform foundation
- ADR-0002 PostgreSQL for operational state
- ADR-0003 Single repository for harness documentation
- ADR-0005 Code calculates, agents interpret

(ADR-0004 was retired along with the Atlas Research product line; the
number is not reused.)
- ADR-0006 Source-of-truth hierarchy
- ADR-0007 Generative planning with deterministic reconciliation
- ADR-0008 CI-sourced evidence with trust tiers
- ADR-0009 Single-operator governance
- ADR-0010 Multi-call generation with single-proposal reconciliation
- ADR-0011 DebtItem denotes delivery anomalies; code-quality debt register
  deferred

Prompts and tools:

- `atlas/planning/prompts/planner-v1.0.0.md.j2` and
  `atlas/planning/prompts/planner-v1.1.0.md.j2` — versioned planner
  prompt templates (released artifacts; never edited in place)
- `atlas/planning/prompts/planner-stage-epics-v1.0.0.md.j2`,
  `atlas/planning/prompts/planner-stage-tickets-v1.0.0.md.j2`, and
  `atlas/planning/prompts/planner-stage-dependencies-v1.0.0.md.j2` —
  staged planner templates: per-stage projections of the §3.11 proposal
  contract, assembled into one full-state proposal by ATLAS-104
  (ADR-0010; ATLAS-103)
- `atlas/planning/prompts/CURRENT` — explicit current-release pointer
  (the renderer never infers the release; ATLAS-22)
- `atlas/planning/prompts/README.md` — prompt versioning and release rules
- `atlas/cli.py` — the `atlas` CLI; `atlas plan` composes ingestion →
  render → model → parse → gates → reconcile and persists a PlanRun
  (ATLAS-26; never writes docs/planning/). Replaces the retired dry-run
  harness.
- `atlas/dependencies/graph.py` — the Phase 3 dependency-graph projection:
  `build_dependency_graph(db)` / `project_graph(...)` build a NetworkX
  `DiGraph` on demand from the relational tables, never persisted
  (ATLAS-31; dependency-engine.md "Graph projection").
- `atlas/dependencies/validation.py` and `atlas/dependencies/errors.py` —
  `validate_graph(graph)` and the typed `GraphValidationError` hierarchy:
  the four dependency-engine.md "Validation rules" run over the projected
  graph (reading ATLAS-31's `present`/`status`/`node_type` attributes, never
  storage); `atlas apply` refuses an invalid graph before its commit seam
  (ATLAS-40).
- `atlas/dependencies/readiness.py` — the dependency-engine.md "Readiness
  predicate": `is_ready(graph, key)` and `ready_tickets(graph)` over the
  validated projection, returning a typed `ReadinessResult` with the failing
  reason(s) (reading ATLAS-31's `status`/`node_type`/`present`/
  `acceptance_criteria_count`, never storage); the PM Engine consumes it for
  promotion to Ready for Agent (ATLAS-34).
- `atlas/dependencies/critical_path.py` — the dependency-engine.md "Critical
  path": `critical_path(graph)` over the non-terminal ticket subgraph,
  returning the longest effort-weighted execution chain as a typed
  `CriticalPath` (ordered keys + cumulative effort), with the three-level
  tie-break in spec order. Reuses ATLAS-40's `TERMINAL_STATUSES`, reads
  ATLAS-31's node attributes only (null `estimated_effort` weighted as 1 at
  compute, node never mutated); advisory, never gates dispatch (ATLAS-35).

Runbooks (`docs/runbooks/`):

- `docs/runbooks/agent-ticket-prompt.md` — reusable agent ticket prompt
  (required reading, plan gate, scope and definition-of-done rules)
- `docs/runbooks/review-doctrine.md` — review contract for gate
  presentations and completion reports (gate and close checklists,
  verdict forms, reviewer conduct)
- `docs/runbooks/reviewer-session.md` — how a reviewer session runs end
  to end (the *how*; companion to review-doctrine.md, which defines the
  *what*)
- `docs/runbooks/running-atlas-plan.md` — operator runbook for the
  plan/apply workflow: prerequisites, the commands and their exit
  codes, every failure mode, the capacity boundary, and provenance
- `docs/runbooks/planning-phases-and-ticket-stubs.md` — governed phase-planning
  packages, ordered stub and exact-path contracts, committed batch manifests,
  Atlas-owned integrity validation and the local-agent/operator handoff
- `docs/runbooks/local-development.md` — toolchain, running the test
  suite, reproducing the CI gates, pre-commit, the shape of the suite,
  and the operator-run live tests
- Stubs awaiting content: `docs/runbooks/troubleshooting.md`
- `docs/runbooks/pr-acceptance.md` — operator acceptance protocol from
  agent PR to board Done: the spine (evidence/confirm/verify/merge), Done
  is a hand motion, migration parity, silence discipline, planning-side
  ordering.
- `docs/runbooks/operator-environment.md` — local-environment facts Atlas
  depends on but does not control: the two GitHub credential channels, the
  Codex runtime and version-pinned connector patch, the database path and
  edit-then-promote SQL hazards, board-operation rules.

Phase closure reports (`docs/closure/`):

- `docs/closure/phase-1-closure-report.md` — Phase 1 (Knowledge Core)
  closure: milestone evidence, harness ledger, carry-forwards
- `docs/closure/phase-2-closure-report.md` — Phase 2 (Planning Engine)
  closure: milestone evidence, harness ledger, carry-forwards
- `docs/closure/phase-2.5-closure-report.md` — Phase 2.5 (out-of-band
  hardening) closure: milestone evidence, harness ledger, carry-forwards
- `docs/closure/phase-3-closure-report.md` — Phase 3 (Dependency Engine)
  closure: milestone evidence, harness ledger, carry-forwards
- `docs/closure/phase-3.5-closure-report.md` — Phase 3.5 (Layer
  Consolidation) closure: milestone evidence, harness ledger, carry-forwards
- `docs/closure/phase-4-closure-report.md` — Phase 4 (PM Engine) closure:
  milestone evidence, harness ledger, carry-forwards
- `docs/closure/phase-5-closure-report.md` — Phase 5 (Context Renderer)
  closure: milestone evidence, harness ledger, carry-forwards
- `docs/closure/phase-6-closure-report.md` — Phase 6 (Evidence System)
  closure: milestone evidence, harness ledger, carry-forwards
- `docs/closure/phase-7-closure-report.md` — Phase 7 (Verification Engine)
  closure: milestone evidence, harness ledger, carry-forwards
- `docs/closure/smoke-b-closure-report.md` — Smoke B (Phase 8 milestone,
  base case) closure: milestone evidence, harness ledger, carry-forwards
- `docs/closure/smoke-b-closeout.md` — Smoke B Phase 7 machine capture
  (evidence appendix to the closure report)
- `docs/closure/phase-9-closure-report.md` — Phase 9 (Learning System)
  closure: milestone evidence, seed disposition, findings batch, incident
  ledger, carry-forwards, criteria self-assessment.
- `docs/closure/phase-10-closure-report.md` — Phase 10 (Operator API Read
  Surface) closure: live HTTP milestone evidence, delivery and meta ledgers,
  incidents, carry-forwards and criteria self-assessment
- `docs/closure/phase-11-closure-report.md` — Phase 11 (Operator UI Read
  Surface) closure: live-API browser evidence, delivered ticket ledger,
  integration incidents, carry-forwards and criteria self-assessment
- `docs/closure/phase-12-closure-report.md` — Phase 12 (Mainline Integration
  Control) closure: exact-head and rebase-lane evidence, delivered ticket
  ledger, review corrections, incidents, carry-forwards and criteria
  self-assessment
- `docs/closure/phase-13-closure-report.md` — Phase 13 (Governed Operator
  Actions) closure: live writable-surface evidence, threat and race matrix,
  route inventory, residual risks and criteria self-assessment
- `docs/closure/phase-14-closure-report.md` — Phase 14 (Review Acceptance
  Console) closure: exact-head live-API/browser evidence, movement and failure
  matrix, no-external-mutation proof, residual risks and criteria self-assessment
- `docs/closure/phase-15.5-closure-report.md` — Phase 15.5 controlled
  efficiency/integration PASS and the exact ATL-437 live-authority gate whose
  accepted merge records closure without changing the Symphony ceiling

Stubs awaiting content: `docs/product/`. `docs/tech-debt/` holds the
debt register (`docs/tech-debt/debt-register.md`), a hand-maintained,
append-oriented record of findings, corrections, and pattern-closure
pointers.

## Phase readiness rule

Each phase needs its design documentation written one phase ahead; a phase
does not begin until its tickets are anchorable to canonical headings.

## Design history

Older drafts live under `docs/archive/design-history/` for reference only.

## Source-of-truth rule

Documents are the source of truth for intent; the Atlas database for
operational state; `docs/planning/` files are renders written only by
`atlas apply` (ADR-0006). If any archived document conflicts with an
active canonical document, the active canonical document wins.

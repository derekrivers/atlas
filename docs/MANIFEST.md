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
- `implementation-roadmap.md` — executable delivery programme (Revision 1)
- `bootstrap-guide.md` — day-one bootstrap guide
- `symphony-integration.md` — Phase 8 integration design (state mapping,
  pack delivery, transition ownership, retry seam)

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

Prompts and tools:

- `atlas/planning/prompts/planner-v1.0.0.md.j2` and
  `atlas/planning/prompts/planner-v1.1.0.md.j2` — versioned planner
  prompt templates (released artifacts; never edited in place)
- `atlas/planning/prompts/CURRENT` — explicit current-release pointer
  (the renderer never infers the release; ATLAS-22)
- `atlas/planning/prompts/README.md` — prompt versioning and release rules
- `tools/run_planner.py` — dry-run harness (renders, calls, saves; never
  writes docs/planning/)

Runbooks (`docs/runbooks/`):

- `docs/runbooks/agent-ticket-prompt.md` — reusable agent ticket prompt
  (required reading, plan gate, scope and definition-of-done rules)
- Stubs awaiting content: `docs/runbooks/local-development.md`,
  `docs/runbooks/running-atlas-plan.md`, `docs/runbooks/troubleshooting.md`

Stubs awaiting content: `docs/product/`, `docs/tech-debt/`.

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

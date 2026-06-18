# Atlas Implementation Roadmap

Canonical delivery programme. Incorporates Revision 1 (ADR-0006..0009 and
the Planning Engine Specification). Ticket numbers retired in Revision 1
are listed per phase and are never reused.

Revision principles:

- The CLI plus YAML/JSON output is the only interface until Phase 8.
  Dashboards and standalone per-engine APIs are deferred until after the
  core loop is proven.
- Mechanical doc validation and CI live in Phase 0: the doc set must be
  internally consistent before anything is built on it, and CI is the
  system-tier evidence producer (ADR-0008).
- Every phase milestone is a falsifiable acceptance test.
- Roadmap ticket numbers are illustrative seeds; real keys are assigned by
  `atlas apply` (ADR-0007).

---

# Phase 0 — Foundation and Mechanical Trust

## Epic: Bootstrap Repository

ATLAS-1  Repository structure per bootstrap guide
ATLAS-2  Python project setup (uv, pytest, ruff, mypy, pre-commit)
ATLAS-3  CI pipeline: tests, lint, type-check on every PR
ATLAS-4  Doc linter v1: validate ADR files against the ADR model; check
         MANIFEST cross-links and intra-doc links; ban legacy v1/v2/v3
         document names in active docs; flag hand-edits to docs/planning/
         outside `atlas apply`
ATLAS-5  Repair documentation drift surfaced by the linter
ATLAS-6  Land ADR-0006..0009 and the Planning Engine Specification as
         canonical; update root control documents

Milestone test: CI is green; the doc linter passes on the whole repository
and fails on a seeded bad fixture (an ADR missing rationale, a stale
MANIFEST link, a hand-edited planning file).

---

# Phase 1 — Knowledge Core (Models Are the Single Contract)

Design doc: docs/architecture/knowledge-core.md (with data-model-and-schemas.md).

## Epic: Knowledge Core

ATLAS-11 Shared types and enums (ActorType, EntityStatus, RiskLevel,
         EvidenceStatus, trust-tier derivation per ADR-0008)
ATLAS-12 Product, ADR, Epic, Ticket, TicketDependency models
         (depends_on is the single stored direction; blocks is derived)
ATLAS-13 Lesson model with status field, DRAFT default for agent-authored
         lessons (ADR-0009)
ATLAS-14 Evidence model with commit_sha, external_run_id, payload_hash and
         append-only semantics (ADR-0008)
ATLAS-15 PlanRun, ContextPack, and AgentRun models (ContextPack carries
         input_doc_shas for staleness detection)
ATLAS-16 JSON Schema generation from the Pydantic models; doc linter v2
         validates JSON examples in canonical docs against generated
         schemas
ATLAS-17 YAML serialisation layer for docs/planning renders
ATLAS-18 Storage layer (SQLAlchemy + Alembic; SQLite locally,
         PostgreSQL-compatible) for operational state per ADR-0006
ATLAS-19 Model and storage unit tests, including dangling polymorphic
         dependency-target detection

Retired: ATLAS-20 (Knowledge CLI) plus the separate Knowledge API, query
service, and persistence-layer tickets — folded into later consumers.

Milestone test: every entity round-trips through YAML and the database;
the schema-drift linter fails on a seeded mismatched JSON example.

---

# Phase 2 — Planning Engine (Milestone 1)

Design doc: docs/atlas/planning-engine-specification.md.

## Epic: Generative Planning with Deterministic Reconciliation

ATLAS-21 Document ingestion and heading-anchor index with git blob SHAs
ATLAS-22 Versioned planner prompt renderer (Jinja2 StrictUndefined,
         front-matter variable validation, prompt-hash recording) over
         the versioned templates in atlas/planning/prompts/
         (version-agnostic, defaulting to the current release)
ATLAS-23 Proposal parsing and validation gates 1–7 (spec §5)
ATLAS-24 Deterministic reconciler: key / anchor / similarity matching;
         ADD / MODIFY / PROPOSE_ARCHIVE / CONFLICT diffing; immutability
         of in-flight tickets
ATLAS-25 Key authority: monotonic key counter, assignment on apply only
ATLAS-26 `atlas plan` CLI (propose; never writes planning renders)
ATLAS-27 `atlas apply` CLI (diff review, confirmation, render writes,
         Mermaid DAG render, PlanRun finalisation, staleness refusal)
ATLAS-28 PlanRun persistence and provenance recording
ATLAS-29 Acceptance test suite AT-1..AT-7 (spec §7), with this roadmap as
         the AT-7 reference corpus
ATLAS-30 Runbook: docs/runbooks/running-atlas-plan.md written for real

Retired: roadmap.html (replaced by Mermaid render); standalone Planning
API (the CLI is the interface).

Post-milestone hardening (out-of-band finding numbers, discovered after the
sequence above; design in docs/atlas/planning-large-corpora.md, ADR-0010):

ATLAS-101 Planner output-truncation: max_tokens to the model ceiling, honest
         truncation detection (done)
ATLAS-102 Large-corpus planning design (done): staged generation, single-proposal
         reconciliation
ATLAS-103 Staged planner prompt templates (epics / tickets-per-epic /
         dependencies projections of §3.11)
ATLAS-104 Multi-call generation orchestration: environment-owned index assembly
         into one full-state proposal
ATLAS-105 PlanRun multi-call provenance (generation_stages field; §3.10 +
         migration + schema regen)
ATLAS-106 Per-stage truncation handling and batch sizing
ATLAS-107 Acceptance coverage for staged generation (AT-1/AT-7 staged path;
         AT-2 across the multi-call sequence)

Phase 2.5 live-discovered fixes (found running the staged path against the real
model; Phase 2.5 closure report §3):

ATLAS-108 Fence-tolerant parsing: a shared string/escape-aware brace-scan
         extractor at both parse sites, raw-output hash invariant preserved
ATLAS-109 Bounded directed retry on projection-validation failure (3 attempts,
         the model told what it violated; truncation and json-decode do not retry)
ATLAS-110 Staged-tickets template null-key example: corrected to null with an
         anti-copy instruction (the model was copying roadmap keys)
ATLAS-111 Anchor selection from the heading index, not slug construction: the
         planner selects from valid anchors; CURRENT bumped to planner-v1.2.0

ATLAS-112 AT-7 measures anchoring-convention agreement, not work coverage;
         define a content-coverage variant and evaluate both (operator gate
         on the bar; ATLAS-107 encodes the chosen metric)

Milestone test: AT-1 through AT-7 pass in CI against the seeded Atlas
documents.

---

# Phase 3 — Dependency Engine

Design doc: docs/atlas/dependency-engine.md.

## Epic: Dependency Graph

ATLAS-31 Graph schema and build from storage
ATLAS-32 Dependency model integration; add estimated_effort population to
         the Ticket model (resolves the critical-path field gap)
ATLAS-34 Readiness detection
ATLAS-35 Critical path analysis
ATLAS-36 Blocker detection
ATLAS-37 Graph visualisation (Mermaid)
ATLAS-39 Dependency CLI
ATLAS-40 Graph validation, including dangling polymorphic targets and
         acyclicity on every mutation

Retired: ATLAS-38 (Dependency API); ATLAS-33 (storage projection —
delivered by ATLAS-31's build_dependency_graph; see
dependency-engine.md "Graph projection (build)").

Milestone test: readiness, blockers, and critical path computed correctly
on fixture graphs including cycle and dangling-target failures.

---

# Phase 3.5 — Layer Consolidation

Architecture-fitness consolidation surfaced by the Phase 3 closure report
(§7/§8): collapse the duplicated natural-key helpers, break the
dependencies→planning import inversion, and install mechanical guards so the
layering cannot silently regress.

## Epic: Layer Spine

ATLAS-113 Consolidate the natural-key sort into a single core primitive and
         break the dependencies→planning import cycle
ATLAS-114 import-linter layers contract enforcing the layer spine (first
         architecture-fitness sensor)
ATLAS-115 Roadmap-coverage sensor: every ticket referenced in a closure report
         must appear in this roadmap

---

# Phase 4 — PM Engine

Design doc: docs/atlas/pm-engine-and-linear-sync.md.

## Epic: Delivery Coordination

ATLAS-41 Linear integration with ADR-0006 field ownership (definitions
         Atlas → Linear; status Linear → Atlas; nothing else syncs)
ATLAS-42 Ticket synchronisation
ATLAS-43 Ready state detection
ATLAS-44 Blocked state detection
ATLAS-45 Follow-up ticket generation (as plan proposals, not direct
         writes)
ATLAS-46 Roadmap synchronisation
ATLAS-47 Delivery metrics (CLI report)
ATLAS-50 PM scheduler

Retired: ATLAS-48 (PM dashboard), ATLAS-49 (PM API).

Milestone test: a status change in Linear is reflected in Atlas within one
sync cycle, and a definition change in Atlas is reflected in Linear, with
no other field crossing.

---

# Phase 5 — Context Renderer

Design doc: docs/atlas/context-renderer.md.

## Epic: Execution Context

ATLAS-51 ADR retrieval
ATLAS-52 Documentation retrieval recording doc SHAs
ATLAS-53 Historical lesson retrieval (status ACTIVE only; tag/component
         matching — vector search deferred)
ATLAS-54 Dependency retrieval
ATLAS-55 Context compression
ATLAS-56 Context pack generation with input_doc_shas
ATLAS-58 Context CLI
ATLAS-60 Context validation (required fields, token estimate, anchor
         resolution)

Retired: ATLAS-57 (Context API), ATLAS-59 (quality scoring as a separate
ticket — minimal checks folded into ATLAS-60).

Milestone test: a generated pack for a fixture ticket contains every
required section, only ACTIVE lessons, and a token estimate; a doc edit
after rendering is detectable from input_doc_shas.

---

# Phase 6 — Evidence System

Design doc: docs/atlas/evidence-pipeline.md (implements ADR-0008).

## Epic: Evidence-Driven Delivery

ATLAS-61 Evidence schema with trust fields and append-only enforcement
ATLAS-62 GitHub Checks/Workflow-Runs polling client (transport-agnostic
         normaliser so an HMAC webhook receiver can replace polling with
         no schema change — ADR-0008)
ATLAS-63 Test evidence mapping
ATLAS-64 Build, lint, and coverage evidence mapping
ATLAS-65 Review evidence ingestion
ATLAS-66 Documentation evidence ingestion
ATLAS-67 Evidence CLI
ATLAS-69 Evidence retention policy
ATLAS-70 Evidence validation (tier rules: agent-created capped at PENDING)

Retired: ATLAS-68 (Evidence dashboard).

Milestone test: a CI run on a fixture PR is ingested as commit-pinned
system-tier evidence; an agent-submitted PASSED record is stored as
PENDING.

---

# Phase 7 — Verification Engine

Design doc: docs/atlas/verification-engine.md.

## Epic: Completion Validation

ATLAS-71 Verification rules
ATLAS-72 Acceptance criteria verification
ATLAS-73 Scope verification
ATLAS-74 Documentation verification
ATLAS-75 Evidence verification: TESTS/LINT/BUILD require system-tier
         evidence pinned to the PR head commit; agent claims alone can
         never satisfy a required check
ATLAS-76 Ticket completion validator
ATLAS-77 PR completion validator
ATLAS-80 Verification reports (CLI/markdown)

Retired: ATLAS-78 (Verification API), ATLAS-79 (Verification dashboard).

Milestone test: a ticket with passing agent-claimed evidence but no
system-tier evidence cannot reach done; the same ticket completes once CI
evidence lands for the head commit.

---

# Phase 8 — Symphony Integration

Design doc: docs/atlas/symphony-integration.md. Phase 8 tickets anchor to
its headings; the seeds below are intent only (ADR-0007).

## Epic: Autonomous Delivery

ATLAS-81 WORKFLOW.md and tracker configuration per
         symphony-integration.md#workflow-contract and #state-mapping
ATLAS-82 Context pack embedding in Linear descriptions, with size
         fallback (#context-pack-delivery)
ATLAS-83 Ready-state sync: PM Engine as sole writer into Ready for Agent
         (#ticket-transitions-one-writer-per-state-edge)
ATLAS-84 PR ingestion and AgentRun reconstruction from observation
         (#retry-and-failure-seam)
ATLAS-85 Handoff-state handling: Review Required / Needs Human stop work
         without workspace cleanup (#state-mapping)
ATLAS-86 Ticket-level failure analysis: cycle and dwell detection,
         split proposals, DRAFT lessons (#retry-and-failure-seam)
ATLAS-87 Follow-up comment ingestion (atlas:proposed-follow-up) into plan
         proposals (#workflow-contract)
ATLAS-88 Agent metrics (CLI report)
ATLAS-90 End-to-end delivery automation test (#boundary)

Retired: ATLAS-89 (Agent dashboard). Intra-ticket retry orchestration is
Symphony's responsibility, not an Atlas ticket (#retry-and-failure-seam).

Milestone test: a ready, context-rich fixture ticket flows
pack → Symphony → PR → evidence → verification without manual steps other
than the defined human gates.

---

# Phase 9 — Learning System

Design doc: docs/atlas/learning-system.md (governance: ADR-0009).

## Epic: Organisational Learning

ATLAS-91 Lesson extraction (DRAFT status, ADR-0009)
ATLAS-92 Failure pattern detection
ATLAS-93 Success pattern detection
ATLAS-94 Playbook generation
ATLAS-95 Knowledge enrichment
ATLAS-96 Delivery analytics
ATLAS-97 Lesson promotion CLI (operator gate)
ATLAS-99 Organisational memory search
ATLAS-100 Continuous learning scheduler

Retired: ATLAS-98 (Learning dashboard), Learning API.

Milestone test: a completed fixture ticket produces a DRAFT lesson; the
lesson appears in context packs only after operator promotion.

---

# Critical Success Criteria

1. Atlas generates its own backlog through plan/apply with stable identity
   (AT-1..AT-7).
2. Atlas refuses unverifiable completion: no system-tier evidence, no done.
3. Every operational record is traceable to intent (doc anchor + SHA).
4. The doc linter keeps the canonical document set internally consistent.
5. Product work begins only after criteria 1–4 hold.

---

# North Star

The goal is not to build any single product.

The goal is to build a stateful organisational operating system capable of
repeatedly creating and improving software products through knowledge,
evidence, planning, verification, and learning.

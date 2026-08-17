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
         on the bar — RESOLVED §7.2: a pair, exact-anchor floor + content bar;
         ATLAS-123 encodes it, ATLAS-107 reuses it)
ATLAS-123 Encode the resolved AT-7 pair metric (§7.2): ANCHOR_COVERAGE_FLOOR =
         0.50 as a live exact-anchor floor, content_coverage computed and
         reported but not gated; the live AT-7 leg gates on the floor. Realises
         ATLAS-112's chosen-metric decision — the clause ATLAS-107's staged-path
         acceptance reuses, not re-derives
ATLAS-124 AT-7 content-coverage bar pinning: after a second durably-saved staged
         capture, set the content_coverage bar a recorded margin below the lower
         of the two captures and flip the content leg from reported to gating.
         Prerequisite: the second capture from the next staged run (ATLAS-107)

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
         writes) — the PRODUCER, step 4 of the sync loop: scan a synced
         ticket's comments for the atlas:proposed-follow-up tag (read-only
         LinearClient.fetch_comments) and write one inbox stub per tagged
         comment to docs/planning/inbox/<ticket-key>-<n>.md, deduped by the
         source comment id. Creates no ticket, writes no Atlas/Linear state,
         does not commit the stubs. The consumer is ATLAS-122
ATLAS-46 Roadmap synchronisation
ATLAS-116 Delivery-anomaly model (DebtItem, append-only, one row per
         observation) and recurrence predicate
ATLAS-118 Out-of-ownership transition logging: step 1's "log anomalies"
         clause — an unmapped Linear state appends one
         OUT_OF_OWNERSHIP_TRANSITION DebtItem per transition (per-transition
         dedup via Ticket.last_observed_linear_state_id); the first writer of
         the ATLAS-116 model. Never changes ticket state. Split out of
         ATLAS-42 (steps 1+2 only); the other anomaly mechanisms are
         ATLAS-119/-120
ATLAS-119 Dwell-breach logging: per-state dwell horizons (in_progress 24h,
         pr_open 48h, review_required 7d) append a DWELL_BREACH DebtItem and
         surface in the delivery report; needs a per-state entry timestamp the
         data model does not yet carry (do not add it in ATLAS-118)
ATLAS-120 Review-cycling detection: more than 3 changes_requested → pr_open
         round trips routes the ticket to Needs Human via set_state with a
         failure-analysis note (the one anomaly that changes ticket state)
ATLAS-47 Delivery metrics (CLI report)
ATLAS-121 State-transition history for true cycle time: an append-only
         TicketStatusTransition model (data-model-and-schemas.md) recording
         every status change with its timestamp, written by
         apply_linear_status (the sole status writer). Prerequisite for
         historical per-state cycle time; until it lands, ATLAS-47 reports
         only the current-dwell proxy, never historical cycle time. Owner:
         PM Engine (Phase 4).
ATLAS-122 Follow-up consumer integration: the CONSUMER half of follow-up
         ingestion, paired with the ATLAS-45 producer. atlas plan reads the
         committed docs/planning/inbox/ as a separate plan input source (its
         own input document set, distinct from the operator's hand-authored
         input docs), and atlas apply moves applied or rejected stubs to
         docs/planning/inbox/processed/. The operator commits the inbox (the
         human-steered gate); follow-ups enter the backlog only through
         plan/apply (ADR-0007), never as direct ticket creation.
ATLAS-125 Tick-failure record (TickFailure, append-only, system-attributed,
         tick-level — no ticket, so a separate model from DebtItem) and the
         query-time dedup predicate (recorded_since); surfaces a tick-failure
         count in atlas pm report. The record half of create-on-crash; the
         writer (the scheduler that catches a crashing sync_tick, records a
         TickFailure, and continues) is ATLAS-50, for which this is a
         prerequisite. Sets no dedup window — recorded_since takes the window
         boundary from the caller (ATLAS-50)
ATLAS-50 PM scheduler — the recurring loop that calls sync_tick on a cadence,
         with create-on-crash (catch a crashing tick, record a TickFailure via
         ATLAS-125's repo, continue) and the dedup window policy. Depends on
         ATLAS-125 (the tick-failure record + recorded_since predicate)
ATLAS-126 Historical cycle time from the transition log: upgrade atlas pm
         report / build_delivery_report to compute true per-state cycle time
         from TicketStatusTransition (the ATLAS-121 log), replacing the
         current-dwell proxy ATLAS-47 reports today. Depends on ATLAS-121.

Retired: ATLAS-48 (PM dashboard), ATLAS-49 (PM API).

Milestone test: a status change in Linear is reflected in Atlas within one
sync cycle, and a definition change in Atlas is reflected in Linear, with
no other field crossing.

---

# Phase 5 — Context Renderer

Design doc: docs/atlas/context-renderer.md.

## Epic: Execution Context

ATLAS-127 Ticket tags and component fields: free-form tags/component on the
         stored Ticket (storage half).
ATLAS-128 Planner emits ticket tags and component: ProposalTicket gains the
         fields, the planner produces them, materialisation and validation
         carry them through (writer half). Depends on ATLAS-127.
ATLAS-51 ADR retrieval
ATLAS-129 Relocate the anchor/slug primitive (slugify, heading parsing,
         SourceDocument, ResolvedAnchor, AnchorIndex, and the anchor error
         hierarchy) from atlas.planning.ingestion to atlas.core.anchors,
         re-pointing ingestion and all importers; a pure move, no behaviour
         change. Unblocks the atlas.context retrievers, which sit below
         atlas.planning in the spine and so cannot import the primitive from
         its current home. Precursor to ATLAS-52.
ATLAS-52 Documentation retrieval recording doc SHAs: section-level
         extraction over the relocated primitive. Depends on ATLAS-129.
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

Phase 6 live-discovered fixes (found running the evidence CLI against a real
repository; Phase 6 closure report §3):

ATLAS-130 Evidence CLI fails cleanly on a cold/never-migrated database: a
         missing-schema OperationalError maps to EXIT_PRECONDITION across
         pull/list/show instead of tracebacking

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
ATLAS-84 PR ingestion and AgentRun reconstruction from observation
         (#retry-and-failure-seam)
ATLAS-85 Handoff-state handling: Review Required / Needs Human stop work
         without workspace cleanup (#state-mapping)
ATLAS-86 Ticket-level failure analysis: cycle and dwell detection,
         split proposals, DRAFT lessons (#retry-and-failure-seam)
ATLAS-88 Agent metrics (CLI report)
ATLAS-90 End-to-end delivery automation test (#boundary)

Retired: ATLAS-89 (Agent dashboard); ATLAS-83 (subsumed — PM Engine
sole-writer promotion shipped as atlas/pm/promotion.py, ATLAS-34/42);
and ATLAS-87 (subsumed — follow-up producer ATLAS-45 + consumer ATLAS-122).
Intra-ticket retry orchestration is Symphony's responsibility, not an
Atlas ticket (#retry-and-failure-seam).

Phase 8 live-discovered fixes (found running the Smoke B milestone against the
real store; Smoke B closure report §5):

The title-embedding fix (label ATLAS-143 — an out-of-band dispatch label,
never a store mint: the key counter had not reached 111 when PR #144
merged, and the 111..146 label range was later reserved-and-discarded;
see the debt register entry "Key-namespace burn") was delivered live: it
embeds the Atlas key in the pushed Linear title so the render round-trips
byte-exact, closing the identifier homonym seam between the tracker title
and the store key. It carries no standalone seed line — the retirements
above already net the roadmap count.

The Linear client hardening (key ATLAS-147, a real store mint from the OP-8
inbox stub) closed the smoke's `_execute` no-timeout carry-forward: HTTP
error bodies now cross into `LinearAPIError` messages (truncated), every
Linear call carries an explicit timeout, and a RATELIMITED rejection raises
a typed error the scheduler backs off on until the parsed reset. Minted by
plan/apply from the operator inbox, not a Phase-8 seed — it carries no
counted seed line here.

Milestone test: a ready, context-rich fixture ticket flows
pack → Symphony → PR → evidence → verification without manual steps other
than the defined human gates.

Phase-tail deliveries, live-discovered and stub-minted (non-seed keys,
not counted against the roadmap totals): the stubs-only plan mode
(ATLAS-153), the meta-label title gate (ATLAS-160), collapse spelling
normalization (ATLAS-161), retirement collision fail-closed handling
(ATLAS-163), pack embedding — the milestone leg (ATLAS-164), the
relevant_docs repair (ATLAS-165), AgentRun reconstruction (ATLAS-166),
and DRAFT-lesson filing (ATLAS-167), with the verified-completion gate
(ATLAS-131) load-bearing throughout acceptance.

---

# Phase 9 — Learning System

Design doc: docs/atlas/learning-system.md (governance: ADR-0009).
Status: CLOSED 2026-07-18 — see docs/closure/phase-9-closure-report.md.

## Epic: Organisational Learning (E11)

Delivered E11 tickets (store keys; these collide in the roadmap namespace
with the Phase 2.5 planner tickets of the same numbers — a known
identity-contract issue recorded as a Phase 10 carry-forward, closure
report §6). Listed fenced so the enumeration parser does not double-count
them against their planner-ticket namesakes; the coverage sensor still
resolves every key:

```
ATLAS-99  Lesson extraction on ticket completion/failure     — #195
ATLAS-65  Historical lesson retrieval (ACTIVE-only)          — #196
ATLAS-100 Lesson promotion CLI (operator gate)               — #199
ATLAS-101 Lesson retrieval integration + citation feedback   — #201
ATLAS-102 Failure/success pattern detection                  — #200
ATLAS-103 Playbook generation                                — #203
ATLAS-104 Delivery analytics (atlas lessons report)          — #198
ATLAS-105 Organisational memory search                       — #202
ATLAS-106 Continuous learning scheduler                      — #197

Phase 9 closure findings (delivered; closure report §4):
ATLAS-170 One-shot CLI result surfacing                       — #205
ATLAS-171 GitHub write-access preflight probe                 — #207
ATLAS-172 Lesson provenance/citation split                    — #206
ATLAS-173 atlas lessons show detail view                      — #210
ATLAS-174 Schema-drift precondition guard                     — #209
ATLAS-175 Playbook MANIFEST registration                      — #208
ATLAS-176 Live rejected-type acceptance (ATLAS-155 lesson)    — #211
ATLAS-177 Extractor tag anchoring                             — #204
ATLAS-178 Routine sync-skip aggregation                       — #212

Incident references (closure report §5): ATLAS-155, ATLAS-169,
and the reconciled seed-collision keys ATLAS-91, ATLAS-97.
```

Deferred (fenced: the store's Phase 9 ATLAS-107 collides in the roadmap
namespace with the Phase 2.5 planner ATLAS-107 — Phase 10 carry-forward,
closure §6):

```
ATLAS-107 Code-quality debt register — distinct entity, NOT DebtItem and
         NOT table debt_items (ADR-0011 D2); gated on the first
         debt-RECORDING sensor (one that persists code-quality debt rows,
         e.g. mutation/coverage, duplication or large-file, KB-freshness),
         not a pass/fail CI gate — ATLAS-114's import-linter already
         shipped and does NOT count, as it gates rather than records;
         consumed by debt-pattern detection.
```

Carried forward (closure report §6):
- Success pattern detection — extend detect_pattern_candidates to
  SUCCESS_PATTERN; explicit low priority (success recurrence is a weaker
  signal than failure recurrence).

Retired: Knowledge enrichment (never scoped into E11); Learning
dashboard; Learning API.

Milestone test: a completed fixture ticket produces a DRAFT lesson; the
lesson appears in context packs only after operator promotion. PASSED
live 2026-07-17 (closure report §2) — proven as a controlled experiment
on ATLAS-63, historical_lessons [] → ['30cec9d0-…'] across a single
operator promotion, with the full Docs→Delivery→Lessons→Docs loop closed
by the linear-sync playbook (#213).

---

# Phase 10 — Operator API (Read Surface)

Design doc: docs/atlas/operator-api.md.
Status: CLOSED.

## Epic: Operator API (Read Surface) (E12)

Delivered E12 tickets (store keys):

```
ATLAS-187 atlas.api skeleton and base infrastructure                        — #223
ATLAS-188 review-queue coordinating service                                 — #224
ATLAS-189 GET /api/reviews endpoint                                         — #225
ATLAS-190 GET /api/tickets board endpoint                                   — #226
ATLAS-191 extract HTTP presenters                                           — #227
ATLAS-192 reconcile root documentation pointers                             — #228
ATLAS-194 API contract: /api/v1 prefix and canonical StrEnum response schemas — #236
ATLAS-197 API contains-no-logic architecture sensor                          — #241
ATLAS-199 GET /api/v1/tickets/{key}/dependencies and critical path           — #251
ATLAS-200 GET /api/v1/tickets/{key}/evidence                                 — #249
ATLAS-201 GET /api/v1/lessons                                                — #252
ATLAS-202 GET /api/v1/status                                                 — #254
```

Phase 10 cross-epic deliveries (delivered):

```
ATLAS-193 Forbidden import contracts: storage must not import the Linear
          or GitHub adapters — E5, Layer Spine — #233
ATLAS-195 CLI disposition path for a stale proposed PlanRun
          — E3, Generative Planning with Deterministic Reconciliation — #235
ATLAS-196 Source-anchor integrity sensor — E1, Harness Foundation — #245
ATLAS-198 Doc-linter PATH and PHASE checks — E1, Harness Foundation — #242
ATLAS-203 Assert mapped Linear state on issue creation — E6, PM Engine — #248
```

Phase 10 hand-delivered meta work (not store tickets):

```
ATLAS-029M claimed-key namespace reconciliation — #229
ATLAS-030M forbidden-import stub — #230
ATLAS-031M dangling-anchor repair — #231
ATLAS-032M API-v1 and stale-PlanRun stubs — #234
ATLAS-033M Operator API design — #237
ATLAS-034M Operator API roadmap phase — #238
ATLAS-035M second sensor-wave seed and stub retirement — #240
ATLAS-036M render catch-up and stub retirement — #243
ATLAS-037M ticket detail projection — #244
ATLAS-038M final API-wave and Linear-state stubs — #246
ATLAS-039M ATLAS-199..203 mint — #247
ATLAS-040M acceptance-chain driver — #250
ATLAS-041M Symphony workflow amendments — #253
ATLAS-042M operator-environment incident record — #255
```

Carried in from Phase 9 (closure §6):

```
Roadmap/store ticket-identity collision — operational half delivered via
ATLAS-029M (#229): the key counter was reconciled through ATLAS-192,
delivered records were backfilled, and the binding key-authority rule landed
in WORKFLOW.md under "Ticket key identity". The roadmap-namespace ruling for
the ATLAS-91/97/107 collision class remains open — not in scope of the
read-surface epic.

ATLAS-107 Code-quality debt register — deferred — not in scope of the
read-surface epic.
```

Milestone test: an operator can read the review queue and ticket board over
HTTP at /api/v1 with no direct database or CLI query, with the API
contains-no-logic rule mechanically enforced.

# Phase 11 — Operator UI (Read Surface)

Design doc: docs/atlas/operator-ui.md.
Status: CLOSED.

A read-only browser instrument over the Phase 10 Operator API. The application
lives at `apps/operator-ui/`, couples to Atlas through the `/api/v1` OpenAPI
contract, and keeps cross-projection presentation assembly in the browser.
Generated TypeScript types and runtime enum metadata are committed and
regenerated in CI; drift fails the build.

Phase 11 added exactly two v1 read routes:
`GET /api/v1/dependencies/graph` and `GET /api/v1/epics`, with `epic_key`
added to the ticket-board item. It added no writes, authentication, remote
deployment contract or third read route.

## Epic: Operator UI (Read Surface) (E13)

Delivered E13 tickets (store keys):

```
ATLAS-209 Accessibility and responsive pass                              — #282
ATLAS-210 Application shell: navigation, theme toggle, command palette   — #269
ATLAS-211 Ticket board view                                               — #272
ATLAS-212 Operator UI CI pipeline                                         — #271
ATLAS-213 Critical path view                                              — #274
ATLAS-214 Dependency graph view                                           — #276
ATLAS-215 Playwright end-to-end harness over a seeded live API            — #270
ATLAS-216 Epic grouping on the ticket board                               — #278
ATLAS-217 Lessons view with draft triage                                  — #277
ATLAS-218 Open-source readiness for the Operator UI                       — #283
ATLAS-219 Generated OpenAPI TypeScript client with a CI drift guard       — #264
ATLAS-220 Overview dashboard                                              — #281
ATLAS-221 Query layer, dev proxy, and API-unreachable primitives          — #267
ATLAS-222 Review queue view                                               — #275
ATLAS-223 Scaffold apps/operator-ui and strip the template demo domains   — #263
ATLAS-224 Theme token contract from the vendored theme.css                — #265
ATLAS-225 Ticket detail: dependencies and readiness tab                   — #280
ATLAS-226 Ticket detail view: definition and metadata                     — #273
ATLAS-227 Ticket detail: evidence tab                                     — #279
```

Phase 11 cross-epic deliveries (delivered):

```
ATLAS-207 GET /api/v1/dependencies/graph
          — E12, Operator API (Read Surface) — #266
ATLAS-208 GET /api/v1/epics and epic_key on the ticket board item
          — E12, Operator API (Read Surface) — #262
```

Phase 11 hand-delivered meta work:

```
ATLAS-044M Phase 11 design, governed ticket batch and planning renders — #261
ATLAS-045M Backlog audit and verification-gate hardening               — #268
```

Milestone test: an operator can open the UI against a running
`atlas api serve` and reach the Overview, ticket board, grouped epics,
complete ticket definition, evidence and dependency readiness, review queue,
critical path, dependency graph and lessons draft queue without a CLI or
database read. The end-to-end suite proves every view against a seeded live
API, generated-client drift is mechanically rejected, and accessibility and
responsive behaviour are required CI gates.

Closure: docs/closure/phase-11-closure-report.md.

Carried forward:

- The browser surface is delivered; whether agent-authored review requires a
  browser step remains an operator decision.
- `last_linear_sync_at` currently reflects a ticket definition cursor rather
  than the last successful sync tick. A successful no-op or status-only sync
  can therefore render stale; the PM/API owner must persist and expose the
  actual successful-sync timestamp.
- Writeable UI/API behaviour still enters only with authentication, actor
  context and a threat model designed together.
- Production serving and remote deployment remain undesigned.

---

# Phase 12 — Mainline Integration Control

Design authority: docs/atlas/symphony-integration.md, section
“Mainline freshness discipline”.
Status: CLOSED.

Phase 8 established agent-owned rebasing before a ticket reaches
`Review Required`. Phase 12 closes the remaining post-handoff integration
seam: when a sibling merge makes a reviewed PR stale, the operator can assess,
rebase and republish the branch without returning mechanical work to Symphony,
while preserving exact-head evidence and human acceptance.

## Epic: Autonomous Delivery (E10)

Delivered E10 tickets (store keys):

```
ATLAS-228 Exact-head PR mainline integration assessment                 — #286
ATLAS-229 Operator-owned lease-guarded PR rebase lane                   — #287
ATLAS-230 Mainline freshness gate and exact-head acceptance restart     — #288
```

Phase 12 hand-delivered meta work:

```
ATLAS-046M Close Phase 11 and open Phase 12                             — #284
ATLAS-047M Create the governed Phase 12 ticket batch                    — #285
```

Scope boundary:

- The lane may assess ancestry, create an isolated local workspace, perform a
  Git rebase, preserve conflicts for the operator, and publish with an exact
  force-with-lease.
- It never resolves conflicts automatically, merges a PR, changes Linear,
  bypasses CI, carries evidence or confirmations across a head change, or
  changes the operator's primary checkout.
- Fork PRs, a dashboard action, merge queues and automatic branch updates are
  outside the first version.
- `Changes Requested` remains the route for semantic remediation or work that
  must return to Symphony. A purely mechanical operator rebase leaves the
  ticket in `Review Required` and restarts exact-head acceptance.

Milestone test: merge one of two sibling PRs, then take the trailing PR from a
stale `Review Required` head through the operator-owned rebase lane. The
published head must contain exact current `main`, the remote update must be
protected by a lease pinned to the original head, CI must rerun, and old-head
evidence and confirmations must not authorise the new head. A seeded PR-head
race, main-head race, unresolved conflict or lease rejection must produce zero
unintended remote mutation.

The deterministic Phase 12 suite proves the complete lifecycle over real local
Git repositories and injected GitHub/ticket boundaries, including every named
race and refusal. The accepted delivery head reported 2,209 passing Python
tests and all 14 required CI jobs green. The controlled live sibling-PR drill
then took PR #291 from old head `8e5bc892` through the lease-guarded lane to
republished head `8af6c33f` on exact current `main` `495fffaf`, reran all 14
required CI jobs, rejected old-head evidence and confirmation, and reached a
fresh passed exact-head verdict. The drill found historical-`base.sha`
assumptions in assessment and publication; PRs #293 and #294 corrected both
before the successful remote write.

Closure: docs/closure/phase-12-closure-report.md.

Carried forward:

- The residual interval between the close driver's final live assessment and
  the operator's manual GitHub merge remains governed by the one-PR freeze.
  Merge queues and automatic merge authority remain deferred.
- The hand-delivered Planning Batch Integrity Guard closes the Phase 12
  planning-integrity carry-forward. Before a PlanRun exists, Atlas rejects
  prose, globs, traversal, missing paths, invalid dependency identities,
  forward sibling references and cycles. Ordered phase batches require one
  committed manifest whose exact base-to-HEAD overlay and ordered stub list
  match the active inbox; apply re-runs the guard before confirmation and
  retires the manifest with the stubs. No ticket was minted through the
  defective path to authorise its own repair.
- Verification and confirmation commands need clearer zero-action and pending
  diagnostics; their current output can conceal the one check that blocks
  closure even though the underlying verdict remains fail-closed.

---

# Phase 13 — Governed Operator Actions

Programme direction: `docs/atlas/phase-13-20-programme-horizon.md`.
Design authority: `docs/atlas/governed-operator-actions.md`.
Status: CLOSED 2026-08-11 — see
`docs/closure/phase-13-closure-report.md`.

Phase 13 introduces Atlas's first authenticated browser write. The single
operator can promote or reject a DRAFT lesson through server-owned identity,
idempotent commands, compare-and-set domain behaviour and append-only action
receipts. The supported topology remains loopback-only and single-operator.
No GitHub, Linear, plan-approval or merge write enters the phase.

Delivered contracts:

1. ATLAS-231 Loopback operator session security and server-owned actor context
   — #300.
2. ATLAS-232 Append-only operator action ledger and idempotent command gateway
   — #301.
3. ATLAS-233 Governed lesson disposition service with atomic stale-state
   protection — #305.
4. ATLAS-234 Authenticated lesson promote and reject API commands — #309.
5. ATLAS-235 Lessons UI promote and reject workflow — #312.
6. ATLAS-236 Writable-surface security, accessibility and live-API acceptance
   — closure change.

Milestone test: through a seeded live UI and API, promote one DRAFT lesson and
reject another, then prove final states, server attribution, durable receipts
and the ACTIVE-only retrieval effect. Hostile origin, missing CSRF, replay,
expired session, stale-state race and receipt failure must produce no
unintended lesson mutation. PASSED by the built-UI/live-FastAPI milestone,
hostile HTTP, browser/CLI concurrency, atomic failure, secret-canary,
accessibility, responsive and executable route-inventory suites recorded in
the Phase 13 closure report.

---

# Phase 14 — Review Acceptance Console

Programme direction: `docs/atlas/phase-13-20-programme-horizon.md`.
Design authority: `docs/atlas/review-acceptance-console.md`.
Planning state: CLOSED 2026-08-12; closure evidence is recorded in
`docs/closure/phase-14-closure-report.md`.

Phase 14 turns the delivered review queue into an authenticated exact-head
acceptance workflow. One immutable session pins the repository, PR, close-set,
head, base and live acceptance-criteria fingerprint while the operator pulls
evidence, confirms criteria, runs verification and receives an advisory
ready-for-manual-merge result. Phase 12 remains the freshness authority and
Phase 13 supplies the authentication and action-receipt boundary. Atlas does
not merge, rebase or change Linear from the console.

Delivered contracts:

1. ATLAS-237 Acceptance and confirmation zero-action diagnostics — #299.
2. ATLAS-238 Durable exact-head acceptance session and status projection —
   #304.
3. ATLAS-239 Exact-head acceptance-session evidence pull action — #308.
4. ATLAS-240 Acceptance-session criteria confirmation and manual approval
   action — #307.
5. ATLAS-241 Exact-head verification and manual-merge readiness evaluator —
   #314.
6. ATLAS-242 Authenticated acceptance-session workflow API — #319.
7. ATLAS-243 Review queue acceptance console UI — #320.
8. ATLAS-244 Acceptance console security, concurrency and live-API milestone
   — closure change.

Milestone result: a seeded exact-main Review Required PR passes through the
built UI and live FastAPI/store from preflight through exact-head evidence,
every human confirmation and explicit PASSED verification to current
`merge_ready: true`. Head and live-main movement at every seam, criteria drift,
old-head records, missing gates, every non-PASSED verdict, replay, cross-tab
concurrency, timeout/malformed reads and receipt/store failures fail closed.
Repository and external-call spies prove the workflow performs no merge,
branch operation, Linear/Symphony action, schema upgrade or PM sync. The
result remains synchronous, single-process and advisory; the one-PR freeze is
still required across the residual final-GET-to-manual-merge race.

---

# Phase 15 — Multi-Agent Delivery Control

Programme direction: `docs/atlas/phase-13-20-programme-horizon.md`.
Design authority: `docs/atlas/multi-agent-delivery-control.md`.
Planning state: WAVE A INPUTS PREPARED; the Phase 14 closure prerequisite is
satisfied, but the live ten-agent milestone remains gated by Phase 15's own
ordered delivery contracts. Committed `main` keeps `WORKFLOW.md` at one during
delivery. The controlled milestone branch proves one and advances that file
through 3, 5, 7 and 10 only after each preceding gate passes. Phase 15 cannot
close until the ten-agent gate passes and the milestone/closure change lands
`max_concurrent_agents: 10` on `main`.

Phase 15 replaces promote-everything readiness with deterministic,
capacity-aware admission. Operator-owned policy defines separate working and
review budgets, risk/component lanes and running, paused or draining mode.
Atlas decides whether dependency-ready work may enter `Ready for Agent`;
Symphony remains the scheduler and runner. Policy changes use Phase 13's
governed action framework, and the ten-agent closure milestone depends on Phase
14 proving adequate review throughput.

Planned delivery contracts (keys assigned only by `atlas apply`):

1. Successful PM-sync receipt and truthful status timestamp.
2. Versioned operator-owned delivery admission policy.
3. Coherent delivery occupancy and review-pressure snapshot.
4. Deterministic capacity-aware admission decision engine.
5. Fail-closed single-write admission integration in the PM sync tick.
6. Authenticated delivery-control policy and status API.
7. Operator delivery-control and admission-explanation UI.
8. Symphony ceiling contract and controlled-ramp runbook.
9. One-to-three-to-five-to-seven-to-ten delivery-control milestone.

Milestone test: with more than ten independent seeded tickets and a controlled
one-to-three-to-five-to-seven-to-ten live ramp, prove Atlas never exceeds working,
review or lane limits; review pressure stops new admission; Changes Requested
work is not starved; and pause/drain preserve active agents. Stale sync,
partial Linear failure, concurrent ticks and duplicate commands must produce
zero unintended promotion. A failed gate restores or retains the last proven
milestone-branch ceiling, records the failure, leaves Phase 15 open and merges
nothing to `main`; closure below ten is prohibited. After the ten-agent gate
passes, the Phase 15 milestone/closure change must commit and merge
`WORKFLOW.md` at `max_concurrent_agents: 10`.

---

# Phase 15.5 — Parallel Delivery Efficiency and Integration Control

Programme direction: `docs/atlas/phase-13-20-programme-horizon.md`.
Design authority:
`docs/atlas/parallel-delivery-efficiency-and-integration-control.md`.
Status: CLOSED AT ACCEPTED REMEDIATED ATL-437 MERGE;
PENDING_LIVE_AUTHORITY UNTIL THAT FINAL HEAD PRODUCES A GENUINE PRODUCTION
HANDOFF AND MERGES.
Planning state: DELIVERY COMPLETE THROUGH ATLAS-262; ATLAS-263 is the fixed
comparison, production-reachability remediation and live-authority closure
milestone. Ticket identities were
assigned only through the governed `atlas plan --stubs-only` and `atlas apply`
boundary. The existing API ticket ATLAS-250 and UI ticket ATLAS-251 remain
prerequisites for their delivered extensions.
This phase changes no Symphony ceiling and must close before the operator
releases ATLAS-253 from `Needs Human` for the live Phase 15 ramp.

Phase 15.5 makes parallel delivery cheaper and safer before increasing
capacity. Agents run deterministic ticket-required and affected checks for
local confidence; complete CI remains the system-tier authority. Published
work enters the distinct `CI Pending` state that does not consume Symphony
working capacity. Protected repository surfaces are serialized through explicit
integration lanes. ATLAS-259 and ATLAS-260 recorded FAIL: required GitHub CI is
pinned to the contributor head and no independent trusted candidate
attestation closed that identity gap. The synthetic no-rewrite route is
retired. Exact-head/current-main acceptance and the existing operator-owned
rebase lane remain authoritative.

Delivered contracts and closure milestone (keys assigned only by `atlas apply`):

1. Tiered local-validation contract and deterministic validation-plan CLI.
2. CI-pending delivery state and separate integration capacity.
3. System-tier CI reconciliation and ticket-state handoff.
4. Symphony publish-once, release-slot workflow.
5. Protected integration lanes for conflict-prone repository surfaces.
6. Exact-base synthetic-merge feasibility spike.
7. No-rewrite exact-base assessment: FAIL; route retired and exact-head rebase
   fallback retained.
8. Delivery-pressure API extensions.
9. Integration-pressure operator console.
10. Parallel-delivery efficiency and integration milestone.

Milestone test: run a predeclared controlled workload and prove more accepted
flow without repeated complete local sweeps, agent-side CI polling, stranded
Symphony turns, unbounded CI/review/integration queues or unsafe freshness
shortcuts. Seed protected-surface collisions, implementation and infrastructure
CI failures, head/base movement, provider ambiguity and true merge conflicts.
Every case must route deterministically without automatic merge, rebase,
conflict resolution or ceiling change. The live ATL-437 window additionally
requires zero recurrence of the externally caused ATLAS-261/262 `CI Pending ->
In Progress` reactivation after the conflicting Linear `PR opened -> In
Progress` automation was disabled. A failed, pending or ambiguous result leaves
Phase 15.5 open and ATLAS-253 in `Needs Human`.

ATL-437's first published head completed exact-head CI but proved that the
trusted `reconcile_ci_handoff()` service had no production caller: ordinary
`atlas pm sync` reached only `sync_tick()`, and no genuine
`ci_handoff_reconciliations` row or authorised Linear exit was produced. The
failed head remains historical reachability evidence. The next head showed that
a 60-second PM poll can miss both short-lived `In Progress`/`PR Open` states and
observe the board already at `CI Pending`. ATLAS-263 therefore also permits a
complete trusted board pull to catch the local mirror up from a Symphony-active
predecessor only. The append-only row records the actual direct observation
with poll-compression provenance and invents no intermediate transition. The
remediation wires one deterministically ordered local `CI Pending` candidate
into every supported PM tick, resolves one exact repository/PR publication
only from a complete issue-bound Linear GitHub attachment, invokes canonical
product-scoped system-tier evidence ingestion itself, and scopes the trusted
reconciler to that pull's full contributor head and complete observed evidence
identities without requiring a reconstructed AgentRun. It closes that tick's
workflow write window after a confirmed handoff. The live authority window
restarts at the remediated final head. Production reachability passes only when
that head creates a genuine reconciliation row and corresponding authorised
Linear transition without an agent poll, manual evidence seed or manual state
repair.

---

# Phase 16 — Delivery Intelligence and Agent Evaluation

Programme direction: `docs/atlas/phase-13-20-programme-horizon.md`.
Planning state: HORIZON; detailed design and ticketing gated on Phase 15 and
Phase 15.5 closure.

Phase 16 establishes reproducible ticket, PR, CI, review, rebase, acceptance,
completion, queue and cost observations. It evaluates agent/model performance
by work type and risk, exposes missing data and sample size, and consumes the
truthful successful-sync receipt established in Phase 15. It observes and
recommends; no opaque
score may automatically route work, select a model or change capacity.

Milestone test: replay a seeded delivery corpus to identical metrics and
compare a controlled delivery wave by lead time, review burden, rework,
failure and cost. Missing, duplicated or out-of-order events must be visible
and never silently interpreted as success.

---

# Phase 17 — Technical Debt and Reliability Steward

Programme direction: `docs/atlas/phase-13-20-programme-horizon.md`.
Planning state: HORIZON; detailed design and ticketing gated on Phase 15 and
Phase 15.5 closure.

Phase 17 adds evidence-backed code-quality debt and reliability stewardship.
The code-quality register remains distinct from delivery-anomaly `DebtItem`
records under ADR-0011. Versioned sensors record commit-pinned observations,
age, recurrence and ownership, and may draft bounded remediation proposals.
They cannot edit code, create Linear tickets, change priorities or waive
quality gates.

Milestone test: seed several debt classes, repeated scans, a resolution and a
recurrence; prove deterministic deduplication, preserved evidence, correct
ageing and bounded proposals. Partial sensors and untrusted claims must not
close debt or create work.

---

# Phase 18 — Governed Adaptive Planning

Programme direction: `docs/atlas/phase-13-20-programme-horizon.md`.
Planning state: HORIZON; detailed design and ticketing gated on Phase 15 and
Phase 15.5 closure.

Phase 18 converts delivery intelligence, accepted lessons and recorded debt
into durable, evidence-anchored planning recommendations. Atlas may assemble a
bounded proposal, but deterministic reconciliation, in-flight immutability,
key authority, diff review and operator-controlled `atlas apply` remain
binding. Atlas gains initiative, not unilateral roadmap or strategy authority.

Milestone test: take a measured recurring weakness to a bounded plan amendment,
operator decision and governed apply. Rejection, stale evidence, changed source
documents and a concurrent PlanRun must yield no unintended store or planning-
render mutation.

---

# Phase 19 — Multi-Product Control Plane

Programme direction: `docs/atlas/phase-13-20-programme-horizon.md`.
Planning state: HORIZON; detailed design and ticketing gated on Phase 18
closure and a real second-product need.

Phase 19 introduces explicit product scoping for repositories, trackers,
credentials, planning, events, evidence, receipts, policy and capacity. The
Symphony workflow body remains invariant while declared per-product settings
are rendered around it. Cross-product knowledge is deny-by-default and global
capacity cannot be oversubscribed.

Milestone test: operate two seeded products with intentionally colliding
tracker keys and branch names, proving isolated planning, admission, evidence,
acceptance, receipts and credentials plus governed global capacity allocation.
A wrong product, repository or tracker identity must fail before external
write, with no cross-product context leakage.

---

# Phase 20 — Atlas Managing Atlas

Programme direction: `docs/atlas/phase-13-20-programme-horizon.md`.
Planning state: HORIZON CAPSTONE; detailed design and ticketing gated on Phase
18 closure and the Phase 19 isolation design.

Phase 20 composes the preceding capabilities so Atlas can identify and
evidence a weakness in its own delivery system, propose a bounded improvement,
execute operator-approved tickets within capacity and review policy, follow
exact-head acceptance and measure the outcome. It adds no self-approval,
self-review, merge, permission-expansion or deployment authority.

Milestone test: Atlas detects a recurring Atlas delivery weakness, assembles
exact evidence, proposes bounded governed work, receives operator approval,
admits the minted tickets, follows their PRs through exact-head acceptance and
records the measured lesson/debt outcome. The operator must approve the plan,
review and merge. Seeded self-approval, policy weakening, stale evidence,
permission expansion and cross-product confusion must fail closed.

---

# Critical Success Criteria

1. Atlas generates its own backlog through plan/apply with stable identity
   (AT-1..AT-7).
2. Atlas refuses unverifiable completion: no system-tier evidence, no done.
3. Every operational record is traceable to intent (doc anchor + SHA).
4. The doc linter keeps the canonical document set internally consistent.
5. Product work begins only after criteria 1–4 hold.

# North Star

The goal is not to build any single product.

The goal is to build a stateful organisational operating system capable of
repeatedly creating and improving software products through knowledge,
evidence, planning, verification, and learning.

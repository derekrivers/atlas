# Phase 1 Closure Report — Knowledge Core

Status: CLOSED, 2026-06-12. All nine roadmap tickets (ATLAS-11 through
ATLAS-19) and two chore sessions executed and merged with both evidence
tiers (agent-tier completion reports corroborated by system-tier CI runs
pinned to head commits, per ADR-0008). The Phase 1 milestone test is
evidenced, not asserted.

---

## 1. Milestone evidence

Phase 1 milestone (implementation-roadmap.md): "every entity round-trips
through YAML and the database; the schema-drift linter fails on a seeded
mismatched JSON example."

| Milestone leg | Evidence |
| --- | --- |
| Every entity round-trips through YAML | `test_milestone_every_entity_round_trips_through_yaml` — derandomised Hypothesis property over all ten canonical models — plus per-model `test_yaml_round_trip_is_identity` ×10 (ATLAS-19, PR #17) |
| Every entity round-trips through the database | `test_milestone_every_entity_round_trips_through_database` — all ten models against an Alembic-migrated SQLite database — plus per-model `test_db_round_trip_is_identity` ×10 (ATLAS-19, PR #17) |
| Schema-drift linter fails on a seeded mismatch | `test_milestone_linter_fails_on_seeded_mismatched_example` — JSN005 and exit 1 attributable to the seed alone, clean once repaired — backed by the 22 linter-v2 fixture negatives (ATLAS-16, PR #13) |

Documented boundary of the round-trip claim (stated, per the
falsifiability ethos, so the claim's limits are explicit): the property
strategies exclude NaN (identity-breaking by definition), infinities
(JSON forbids), NUL in text (PostgreSQL forbids), surrogate code points
(invalid Unicode), integers outside ±2³¹, floats outside ±1e9, datetimes
outside 1900–9000, and agent-tier Evidence above PENDING (unstorable by
design per ADR-0008). Within the boundary, identity holds
property-tested; outside it, behaviour is loud failure, never silent
corruption. Whether these exclusions become model-level constraints is
carry-forward 5.

Known qualification: round-trip identity is proven on SQLite. A real
PostgreSQL would round `confidence` and `similarity_threshold` to three
decimals (`NUMERIC(4,3)` scale, which SQLite ignores). This is recorded
in the debt register and is a named precondition to any PostgreSQL
deployment (carry-forward 6). The ATLAS-18 honesty limit stands:
PostgreSQL compatibility is type-discipline plus dialect-compile-tested,
not server-tested.

---

## 2. Delivered

| Ticket | Delivered | Evidence record |
| --- | --- | --- |
| ATLAS-11 | Shared enums (ActorType, EntityStatus, RiskLevel, EvidenceStatus) and `evidence_tier`, the single home of trust-tier logic, guarded by an AST scan | merged PR, CI green |
| ATLAS-12 | Product, ADR, Epic, Ticket, TicketDependency models with model-local enums; contract tests transcribed from doc literals; duplicate-shared-enum AST scan | merged PR, CI green |
| ATLAS-13 | Lesson model, DRAFT default for all lessons; non-policing pinned by test (ACTIVE construction succeeds; enforcement is ATLAS-97/-53) | PR #10, CI green |
| ATLAS-14 | Evidence model; append-only expressed as field shape (created_at only); agent-PASSED constructible at model layer, cap pinned to ATLAS-18 | merged PR, CI green |
| ATLAS-15 | PlanRun, ContextPack, AgentRun models; UUID reference lists pinned; spec §6 diff_summary conflict caught at the gate and ruled (data-model authoritative) | merged PR, CI green |
| ATLAS-16 | Deterministic schema export for all ten models; doc linter v2 with explicit fence mapping (`model=` / `partial` / `no-schema`), fail-closed JSN007, regeneration check GEN001; CANONICAL_MODELS completeness test | PR #13, run 27410922360, commit 0fabf6b |
| ATLAS-17 | Generic deterministic YAML layer for all ten models; planning-render document format with parameterised `atlas apply` header; fail-closed deserialisation; numeric-aware key collation; keyed/keyless split | PR #14, run 27411492535, commit cf20c7a |
| ATLAS-18 | Storage layer: SQLAlchemy 2.0 + Alembic, ten tables per the documented SQL contracts (DDL contract tests incl. DEFaults); EvidenceRepo PENDING cap, PlanRunRepo finalise-once; UTC datetime contract; Pydantic-only public surface pinned | PR #16, run 27413103913, commit 1d26c4a |
| ATLAS-19 | Property-based round-trip suites (Hypothesis, derandomised by construction); dangling polymorphic target helper (all table-backed types, component storeless, unknown fail-closed); milestone module; two property-found repairs | PR #17, CI green |

Chore sessions:

1. **Phase 1 readiness repairs** (pre-ATLAS-11): PlanRun
   insert-plus-single-finalisation replacing blanket append-only;
   `estimated_effort` exists-from-Phase-1 ruling; AgentRun joined
   ATLAS-15; the partial-example validation convention; data-model §11
   storage-overlap trim.
2. **Pre-ATLAS-18 contract repairs**: `Lesson.confidence` bounds
   (ge=0, le=1, doc+model+SQL CHECK+schema together);
   TicketDependency attribution (`created_by_type`/`created_by_id`);
   spec §6 PlanRun block replaced by a pointer to data-model §3.10;
   roadmap ATLAS-89 duplicate deleted. Forced consequence, correctly
   surfaced at the gate: linter v2 learned `minimum`/`maximum`
   (JSN007's first live catch).

---

## 3. Harness ledger — what the phase taught and where it was encoded

- **Maintained copies are drift on a timer — proven on schedule.** The
  spec §6 PlanRun copy diverged from data-model §3.10 on a default and
  was caught at an agent's gate (ATLAS-15). Encoded by deletion: §6 is
  now a pointer. The pre-existing audit-variant rule predicted exactly
  this failure.
- **Ratified decisions don't exist until they're diffs.** The §7
  UUID-placeholder repair was ratified twice and applied zero times,
  surviving on memory until it blocked ATLAS-16. Mechanical fix where
  visible: linter v2 now guards every mapped example. Process fix:
  decisions ride in the next session's diff, never in a side note.
- **Fail-closed pays for itself.** JSN007 caught its first real
  construct (`minimum`/`maximum`) one session after it was added; the
  property suites found silent YAML break-character corruption
  (NEL/U+2028/U+2029) and a storage JSON crash in already-merged work;
  the milestone draw was refused by the EvidenceRepo cap — ADR-0008
  passing an unplanned falsifiability test.
- **The specification-gap-at-the-gate pattern works.** Named gaps with
  constrained proposal spaces (ATLAS-16 fence mapping, ATLAS-17 scalar
  representation, ATLAS-18 four gaps, ATLAS-19 three gaps) produced
  decisions better than the prompts could have pre-specified, at the
  cost of one reply each. Promotion to the runbook is carry-forward 1.
- **Single-gate autonomy held.** From ATLAS-11 onward every session ran
  plan-gate-then-autonomous with zero mid-execution prompts and zero
  plan-gate violations; agents stopped correctly on a genuine doc
  conflict (ATLAS-15) and corrected a prompt error against the
  repository's actual layout (ATLAS-18, `atlas.core.trust`). Promotion
  is carry-forward 2.
- **Plan elements need a binding/indicative distinction.** ATLAS-18's
  conversion-site centralisation was a correct simplification of an
  approved plan detail, transparently reported — but the boundary it
  walked (which plan elements are contract, which are sketch) is
  currently judgment, not rule. Carry-forward 4.

---

## 4. Carry-forwards (nothing vague survives the boundary)

| # | Item | Owner / home | When |
| --- | --- | --- | --- |
| 1 | Runbook: add the **specification-gap variant** (name the gap in the prompt, constrain the proposal space, decide at the gate) | Runbook-update chore session | Before ATLAS-21 |
| 2 | Runbook: add the **single-gate autonomy block** as a named variant (one plan gate, then command-free execution; enumerated stop conditions) | Same chore session | Before ATLAS-21 |
| 3 | AGENTS.md candidate: **exact-match-or-stop** rule for all doc-repair edits (a failed verbatim match is a stop-and-report, never an approximation) — operator to ratify wording | Same chore session | Before ATLAS-21 |
| 4 | Runbook plan-gate section: one sentence distinguishing **binding plan elements from indicative ones** | Same chore session | Before ATLAS-21 |
| 5 | **Exclusions → model constraints** decision batch: per-field operator rulings on 32-bit int bounds, NUL rejection in text, float bounds, datetime range (doc + model + schema together, per the established pattern) | Operator rulings, then a contract chore session | Before Phase 2 execution generates content (pre-ATLAS-22 at the latest) |
| 6 | **NUMERIC scale rule**: round-at-the-boundary vs widen-the-column for `confidence` / `similarity_threshold` on real PostgreSQL | Debt register entry (recorded); storage chore | Before any PostgreSQL deployment |

---

## 5. Phase 2 readiness

- Design doc: `docs/atlas/planning-engine-specification.md` is
  canonical, internally consistent after the §6 repair, and its PlanRun
  definition is single-sourced. The phase-readiness rule is satisfied
  (Phase 3's `dependency-engine.md` is also canonical).
- Recommended before ATLAS-21: a pre-Phase-2 design-doc review in the
  Phase 1 pattern — with the question shifted from "is the spec
  internally consistent" to "is the doc set ready to be machine-read",
  since Phase 2 tickets consume documents as input (ATLAS-21 ingestion;
  ATLAS-29's AT-7 uses the roadmap as reference corpus).
- Sequencing: carry-forwards 1–4 (one runbook/AGENTS chore) and the
  pre-Phase-2 review can run in either order; carry-forward 5 must land
  before planner-generated content exists.

Phase 1 establishes what Phase 2 consumes: ten contract-tested models,
deterministic YAML and schema surfaces, an enforcing storage layer, and
a linter that makes the document set machine-trustworthy. The next stop
is the planning engine — where the system begins generating its own
backlog, and these contracts get their first real workload.

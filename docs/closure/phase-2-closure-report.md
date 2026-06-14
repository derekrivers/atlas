# Phase 2 Closure Report — Planning Engine (Milestone 1)

Status: CLOSED on the deterministic milestone, 2026-06-14. The Planning
Engine Epic (ATLAS-21 through ATLAS-30) is complete, with every ticket
merged under both evidence tiers (agent-tier completion reports
corroborated by system-tier CI runs pinned to head commits, per
ADR-0008). The deterministic acceptance legs (AT-2..AT-6) pass; the
model-dependent legs (AT-1, AT-7) are live-gated and blocked by a
discovered capacity boundary whose resolution is designed and owned as
Phase 2.5 (ATLAS-103..107).

This report closes Phase 2 on the engine being **built and
deterministically proven**, and tracks reliable full-corpus planning as
a designed follow-on. The reasoning for closing here rather than holding
open is stated in §6.

---

## 1. Milestone evidence

The Phase 2 milestone (spec §7) is met when AT-1..AT-7 pass against the
seeded Atlas documents. Status by leg:

| AT | Asserts | Status |
| --- | --- | --- |
| AT-1 Validity | a real proposal passes all gates; projected backlog is an acyclic DAG; every ticket traceable to an anchor | Live-gated; blocked by capacity (see §5) |
| AT-2 Stability | two plan runs on unchanged docs yield empty / MODIFY-only (≥0.95) diff, no key churn | **PASS** (deterministic, CI) |
| AT-3 Locality | editing one doc section localises the diff to that section | **PASS** (deterministic, CI) |
| AT-4 Immutability | a diff touching in_progress/done surfaces CONFLICT; apply refuses | **PASS** (deterministic, CI) |
| AT-5 Provenance | every apply produces a PlanRun whose input_doc_shas match the tree; stale plans refused | **PASS** (deterministic, CI) |
| AT-6 Key authority | no applied key originates from the model; counter monotonic across archives | **PASS** (deterministic, CI) |
| AT-7 Reference corpus | planner covers ≥90% of hand-written roadmap tickets by anchor match | Live-gated; blocked by capacity (see §5) |

The engine's **logic** is proven: every behavioural guarantee the spec
makes about reconciliation, locality, immutability, provenance, and key
authority is asserted end-to-end and green in CI. What remains unproven
is **coverage and gate-validity against the full real corpus** — because
that corpus exceeds a single model call's output capacity, a boundary
discovered only by running the live legs (§5).

---

## 2. Delivered — the Planning Engine Epic

| Ticket | Delivered |
| --- | --- |
| ATLAS-21 | Document ingestion + heading-anchor index, HEAD-atomic with git blob SHAs; the single slug-algorithm implementation, pinned against the repo's own anchors |
| ATLAS-22 | Versioned prompt renderer (StrictUndefined, front-matter validation, prompt_hash); CURRENT pointer for the current release |
| ATLAS-23 | Proposal models (§3.11), parser with index-bounds validation, gates 1–7 with aggregate machine-readable failures |
| ATLAS-24 | Deterministic reconciler: key/anchor/similarity matching, ADD/MODIFY/PROPOSE_ARCHIVE/CONFLICT, frozen-ticket immutability; hand-rolled Sørensen–Dice similarity |
| ATLAS-25 | Key authority: prefix-keyed monotonic counter, structural no-reuse, assignment on apply only |
| ATLAS-26 | `atlas plan` CLI: ingest→render→model→parse→gates→reconcile→PlanRun; injectable PlannerClient, API-key default, live legs opt-in |
| ATLAS-27 | `atlas apply` CLI: staleness refusal, confirmation, atomic key-assign + render-write + finalise (status-as-commit-witness recovery) |
| ATLAS-28 | PlanRun persistence/provenance audit; latest_applied; the audit proved AT-5/AT-6 provenance complete |
| ATLAS-29 | Acceptance suite AT-1..AT-7; AT-7 metric (§7.1) with conservative-floor and dual-pinned denominator |
| ATLAS-30 | Operator runbook, code-verified, capacity limitation stated first |

Out-of-band hardening discovered during the live runs:

| Ticket | Delivered |
| --- | --- |
| ATLAS-101 | Output-truncation fix: max_tokens to the 64K model ceiling, streaming call, honest stop_reason-based truncation detection (done) |
| ATLAS-102 | Large-corpus planning design: staged generation, single-proposal reconciliation; ADR-0010 (done) |

Retired during the phase: roadmap.html (→ Mermaid render); the
standalone Planning API (→ the CLI is the interface); the
run_planner.py dry-run harness (→ the real `atlas plan` pipeline).

---

## 3. The harness ledger — what Phase 2 taught and where it was encoded

- **The named runbook variants became load-bearing.** The
  specification-gap variant (operator names the gap, agent proposes at
  the gate) and the single-gate-autonomy variant — both promoted to the
  runbook at the Phase 1/2 boundary — ran on every ticket of this phase
  with zero gate violations and produced gate decisions better than the
  prompts could have pre-specified (the fence mapping, the scalar
  representation, the reconciler's three-tier ambiguity policy, the
  staged-generation design).
- **Gate artefacts moved onto the PR record.** `review-doctrine.md` plus
  the gate-presentation-as-PR-description rule made the review loop
  repo-resident: gates and completion reports live on the PR, a remote
  reviewer works from the PR number, the operator relays verdicts. The
  review contract now exists independently of any single conversation.
- **Status-as-commit-witness — a pattern worth promoting.** ATLAS-27's
  apply spans a transactional store (DB) and a non-transactional one
  (filesystem renders). The resolution: the DB commit is the single
  linearisation point, and the PlanRun's `applied` status is the witness
  — a crash mid-move is repaired by re-running, which finishes the move
  from committed state idempotently. **Recommended carry-forward:**
  encode this as a canonical pattern, because ATLAS-105 (multi-call
  provenance) and any future Linear-sync write hit the same DB+external
  boundary.
- **Ratified premises get verified, not obeyed.** Twice this phase an
  agent caught an operator-stated premise that was wrong — the
  registry-equality claim (ATLAS-23) and the enums.py path (ATLAS-18) —
  and flagged-with-correction rather than complying. The review doctrine
  now names this explicitly ("a reviewer's own claims are agent-tier;
  verify premises against the repository").

---

## 4. The phase's defining lesson

**The deterministic milestone proved the engine's logic was correct; the
live legs revealed its capacity assumptions were wrong.**

AT-2..AT-6 passed against hand-clean fixture proposals delivered through
a fake client — correct for testing pipeline logic, but the fake always
returned perfect bare JSON, so the model's real output behaviour went
unexercised until the live AT-1/AT-7 runs. Those runs, done by hand with
a real key against the real corpus, produced three findings in
succession that no fixture could have:

1. The 16K max_tokens ceiling truncated the proposal mid-JSON (ATLAS-101).
2. At 64K — the model's ceiling — the corpus produced a complete
   ~247K-character proposal **once**, and truncated **twice**: it sits at
   ~95–97% of a single call's maximum output (ATLAS-102).
3. The honest truncation reporting built in ATLAS-101 turned each of
   these from a confusing parse error into a precise, recorded failure.

The engine was never wrong about *what it does* — it correctly refused to
fabricate a valid proposal from truncated output, and recorded each
failure with full provenance. What the live legs found is that **the
Atlas corpus is large enough to need multi-call planning** — a genuine
architectural finding, surfaced only because the suite grades reality
rather than a fixture that flatters it. This is the strongest possible
argument for keeping live acceptance tests, gated and hand-run, in the
suite.

---

## 5. The capacity boundary and its designed resolution

Single-call planning of the committed corpus is past the model's output
ceiling. There is no higher `max_tokens` to reach for (64K is the
ceiling), so the resolution is architectural, not configuration:
**staged generation with single-proposal reconciliation** (ADR-0010,
`docs/atlas/planning-large-corpora.md`). Generation splits into bounded
stages (epics → tickets-per-epic → dependencies); the environment
assembles one complete §3.11 full-state proposal before the parser, so
the gates and reconciler are untouched and the full-state invariant plus
ADR-0007 determinism are preserved by construction. The implementation
is scoped as Phase 2.5:

- **ATLAS-103** Staged planner prompt templates (§3.11 projections)
- **ATLAS-104** Multi-call generation orchestration (environment-owned
  index assembly into one full-state proposal)
- **ATLAS-105** PlanRun multi-call provenance (generation_stages; §3.10 +
  migration + schema regen)
- **ATLAS-106** Per-stage truncation handling and batch sizing
- **ATLAS-107** Acceptance coverage for staged generation (AT-1/AT-7
  staged path; AT-2 across the multi-call sequence — must measure the
  decomposition-cascade stability, not just assert AT-2)

The payoff of Phase 2.5 is the first live-green AT-7: a real coverage
number for whether the planner reconstructs the hand-written roadmap.

---

## 6. Why close here

Phase 2's scope was to **build the planning engine**. That engine is
built, deterministically proven, hardened, and operable (the runbook).
The capacity boundary is a *discovered scope-extension* — the Atlas
corpus turning out to need multi-call planning — not a failure to
complete the planned work. Holding the phase open through ATLAS-103..107
would conflate "the engine works" (proven today) with "the engine scales
to this corpus" (designed, pending). Closing here keeps that distinction
honest and gives Phase 2.5 a clean, single-purpose charter: make the
live legs green.

The alternative — holding Phase 2 open until AT-1/AT-7 pass live — is a
legitimate stricter reading of the §7 milestone (which names all seven
ATs). It was considered and set aside for the reason above: the engine is
usable today, with one documented limitation and a documented path
around it.

---

## 7. Carry-forwards (owners and homes)

| Item | Owner / home | Priority |
| --- | --- | --- |
| Staged generation (the path to live-green AT-7) | Phase 2.5: ATLAS-103..107 (designed, ADR-0010) | High — the one real capability gap |
| Status-as-commit-witness pattern | Canonical encoding (knowledge-core or a patterns doc); consumed by ATLAS-105 and future external-write sync | Before ATLAS-105 |
| `atlas init` product-bootstrap command | New ticket (runbook documents the manual ProductRepo snippet today) | Ergonomic; before broader operator use |
| Planner template stale `max_output_tokens: 16000` advisory | Template bump to v1.2.0 (non-functional comment only) | Low |
| `atlas plan show` / history inspection CLI | Deferred from ATLAS-28 (latest_applied exists; CLI surface deferred) | Low |
| MODIFY-apply semantics | Deferred from ATLAS-27 (a short spec session pins them, then a ticket) | Before MODIFY-heavy use |
| NUMERIC scale on PostgreSQL (from Phase 1) | Debt register; precondition to any PostgreSQL deployment | Before PG deployment |

---

## 8. Phase 3 readiness

Phase 3 (Dependency Engine — graph schema, build, readiness, acyclicity)
is governed by `docs/atlas/dependency-engine.md`, which is canonical per
the phase-readiness rule. The reconciler's dependency handling and the
`depends_on`-single-direction model land in Phase 1/2; Phase 3 builds the
graph projection and traversal on top.

Two sequencing notes:
- Phase 2.5 (ATLAS-103..107) and Phase 3 are independent — staged
  generation is about *producing* a large proposal, the dependency engine
  about *analysing* the applied backlog. Either can proceed first; doing
  Phase 2.5 first buys the live AT-7 number that validates the whole
  planning premise before building further on it.
- Recommended: do Phase 2.5 first. The live-green AT-7 is the empirical
  verdict on Atlas's central thesis — that an AI planner, given canonical
  intent documents, can reconstruct a human's backlog — and it is worth
  having that number in hand before extending the system that depends on
  it.

The Planning Engine is built and proven. What Phase 2.5 adds is not a fix
to a broken engine but the capacity for the engine to plan a corpus as
large as Atlas's own — discovered, designed, and owned.

# Phase 3 Closure Report — Dependency Engine

Status: CLOSED on the milestone, 2026-06-18. The Dependency Graph Epic is
complete, with every ticket merged under both evidence tiers (agent-tier
completion reports corroborated by system-tier CI runs pinned to head
commits, per ADR-0008). The phase milestone — readiness, blockers, and
critical path computed correctly on fixture graphs including cycle and
dangling-target failures — is met and CI-evidenced.

This report closes Phase 3 on the engine being **built, validated,
operable, and visualisable**: the graph projects deterministically from
storage, every mutation is validated before it commits, the three
analyses (readiness, blockers, critical path) compute over the validated
graph, effort is an operator input that drives the critical path, and the
whole engine is reachable from the `atlas deps` CLI plus an advisory
Mermaid lens. The reasoning for closing here is stated in §6.

---

## 1. Milestone evidence

The Phase 3 milestone (dependency-engine.md) is met when readiness,
blockers, and critical path compute correctly on fixture graphs,
*including* cycle and dangling-target failures. Status:

| Claim | Asserted by | Status |
| --- | --- | --- |
| Readiness correct on fixtures | test_readiness (five conditions, each a seeded-defect test naming the wrong answer) | **PASS** (deterministic, CI) |
| Blockers correct on fixtures | test_blockers (blocked / unlocks / high-risk, derived from is_ready) | **PASS** (deterministic, CI) |
| Critical path correct on fixtures | test_critical_path (effort-weighted longest chain, execution order, tie-break) | **PASS** (deterministic, CI) |
| Cycle failure detected | test_dependency_validation (CycleError with full path) | **PASS** (deterministic, CI) |
| Dangling-target failure detected | test_dependency_validation (DanglingTargetError) + test_readiness (defensive DANGLING_TARGET reason) | **PASS** (deterministic, CI) |

The milestone is satisfied by these falsifiable tests. It lacked a
*single named guard* — unlike planning's AT-1..AT-7, the coverage was
distributed across the per-rule files. `tests/test_phase3_milestone.py`
(landing with this closure) closes that: one valid fixture asserting all
three computations together, plus the cycle and dangling failure modes,
so the phase claim is verifiable as a unit rather than inferred.

The engine is entirely deterministic — zero model/API calls anywhere in
the dependency engine — which is why it was the right work to build under
the live API-billing constraint.

---

## 2. Delivered — the Dependency Graph Epic

| Ticket | Delivered |
| --- | --- |
| ATLAS-31 | Graph schema and build from storage: a NetworkX DiGraph projected on demand from relational tables (`project_graph` over entities, `build_dependency_graph(db)` over storage), edge A→B = A depends_on B, absent targets as `present=False` nodes (honest data for validation), epic membership and all analysis-needed attributes carried at build time |
| ATLAS-40 | Graph validation: typed-error aggregate (`GraphValidationFailed`, collect-all), acyclicity (full cycle path), no self/duplicate edges, no dangling polymorphic targets, no terminal→non-terminal `depends_on`; `atlas apply` refuses before the commit seam so a bad graph writes nothing |
| ATLAS-34 | Readiness predicate: five conditions, typed `ReadinessResult` whose `ready` derives from the reasons (cannot contradict them), per-target ticket→done / ADR→accepted selection, dangling never silently ready |
| ATLAS-35 | Critical path: longest effort-weighted chain over the non-terminal ticket subgraph via a constructed execution DAG, null effort weighted 1 at compute time, three-level strict tie-break; advisory — wired into no gate |
| ATLAS-36 | Blocker analysis: `blocked` (derived from `is_ready` — one definition of dependency-satisfaction), `unlocks` (direct dependents that flip ready when a ticket completes, on a graph copy), `high_risk_blockers` (aggregated over `blocked`); all advisory |
| ATLAS-32 | `estimated_effort` population: operator-driven `TicketRepo.set_estimated_effort` setter (positive-or-null, never inferred), per-field single-writer ownership with apply, no `updated_at` bump (unsynced field); the field now drives the critical path |
| ATLAS-39 | Dependency CLI: `atlas deps ready \| blocked \| critical-path \| unlocks \| validate \| effort`, each with `--db`/`--json`; computation commands validate-first and refuse an invalid graph (EXIT_PRECONDITION + typed violations) |
| ATLAS-37 | Graph visualisation (Mermaid), redefined: an advisory `atlas deps graph` analysis lens rendered to stdout — the dependency DAG overlaid with readiness/blocker/critical-path state — writing no file and explicitly distinct from apply's canonical `roadmap.mmd`; reuses planning/mermaid's primitives (one implementation) |

Retired during the phase:

| Ticket | Reason |
| --- | --- |
| ATLAS-33 (Graph storage projection) | Subsumed by ATLAS-31's `build_dependency_graph` — the storage→DiGraph projection was delivered there. Retired in the roadmap with the enumeration pin updated 93→92 (PR #54) |
| ATLAS-38 (Dependency API) | Retired at design time (the CLI is the interface) |

---

## 3. The harness ledger — what Phase 3 taught and where it was encoded

- **The roadmap is intent; the code is truth.** Two of the eight planned
  tickets were not what their one-line roadmap entries claimed.
  ATLAS-33's storage projection was already built inside ATLAS-31;
  ATLAS-37's "regenerate roadmap.mmd" was both redundant (apply's
  ATLAS-27 render already does it) and an ADR-0007 violation (a second
  writer into `docs/planning/`). Both were caught by re-reading the
  repository at the planning gate rather than trusting the roadmap line —
  one retired, one redefined as the advisory stdout lens. This extends
  Phase 2's "ratified premises get verified" lesson from operator claims
  to roadmap entries: **verify scope against the code before scheduling
  the ticket.**
- **The enumeration pin couples to retirement.** Removing a ticket line
  drops the hand-verified roadmap count, so the retirement and the pin
  update (93→92) must land in the same change. Discovered when the
  ATLAS-39 agent hit the operator's uncommitted roadmap edit breaking the
  pin, and correctly reverted it to keep its PR in scope rather than
  silently absorbing it — the "operator uncommitted edit + dependent pin"
  harness rule (origin: ATLAS-112) held on its second test.
- **Single source by deletion, applied to the computations.** Nothing
  re-defined "a dependency is satisfied": `blocked` consumes `is_ready`'s
  dependency reasons, `high_risk_blockers` aggregates over `blocked`,
  critical path reuses ATLAS-40's `TERMINAL_STATUSES`, and the Mermaid
  lens imports planning/mermaid's `_natural`/`_escape`. Each ticket's
  gate was where this was enforced.
- **Advisory-vs-gate discipline.** Critical path, blocker analysis, and
  the Mermaid lens are advisory and wired into no gate; readiness is the
  single gate. When the design doc falsely implied high-risk blockers
  gate readiness, the fix was a corrected sentence in the doc (ATLAS-36),
  not a new gate — code calculates, the operator/PM interprets (ADR-0005).

---

## 4. The phase's defining lesson

**Grounding every ticket in the repository, not the roadmap line or
memory, is what kept dead work and an ownership breach out of the build.**

A roadmap is a plan written before the code existed; by Phase 3 the code
had outrun two of its lines. Had ATLAS-33 and ATLAS-37 been scheduled as
written, the first would have produced a no-op duplicate of
`build_dependency_graph` and the second a second writer into apply's
`docs/planning/` monopoly — a silent ADR-0007 violation. Neither was
caught by the roadmap, the design doc, or the agent's memory; both were
caught by cloning the branch and reading what was actually there. The
durable practice the phase confirms is the review doctrine's mechanical
core: re-read the repository to correct the plan, every time, because the
cheapest place to delete dead work is before the ticket is written.

---

## 5. Advisory by construction — why the analyses gate nothing

Phase 3 builds four analyses over the validated graph, and exactly one of
them — readiness — is permitted to decide anything. Critical path,
blocker analysis, and the Mermaid lens are advisory: they inform the
operator and (in Phase 4) the PM Engine's sequencing, but they wire into
no dispatch decision. This is deliberate (ADR-0005): the deterministic
code computes facts about the graph; judgement about what to do with
those facts stays with the operator. The boundary is enforced in three
places — critical path is documented "advisory; never gates dispatch",
blocker analysis carries the same clause (corrected from a doc line that
implied otherwise), and the Mermaid lens renders to stdout and writes no
canonical artifact. Keeping the analyses advisory is what lets them be
rich without becoming load-bearing in ways that would need their own
trust tier.

---

## 6. Why close here

Phase 3's scope was to **build the dependency engine** — projection,
validation, and the readiness/blocker/critical-path analyses. That engine
is built, every mutation is validated before commit, the analyses are
correct on fixtures including the cycle and dangling failures the
milestone names, effort is a real operator input that drives the path,
and the whole surface is operable (`atlas deps`) and visualisable (`deps
graph`). The milestone is met and CI-evidenced. There is no discovered
scope-extension as there was in Phase 2 — the phase completed the planned
work, minus the two tickets correctly retired/redefined.

The one open *operator decision* (the AT-7 bar threshold, §7) belongs to
the planning track, not this one, and gates nothing in Phase 3. Closing
here is clean.

---

## 7. Carry-forwards (owners and homes)

| Item | Owner / home | Priority |
| --- | --- | --- |
| Phase 4 design document (PM Engine + Linear sync) | Carried from Phase 0; write before Phase 4 build, governed by `pm-engine-and-linear-sync.md` | High — the next phase's charter |
| AT-7 bar threshold decision | Open **operator decision** (planning track); the content-coverage metric is built (ATLAS-112), the bar is unset | Operator decision; gates nothing in P3 |
| `dependencies → planning` import edge + three divergent natural-key helpers (`mermaid._natural`, `yaml_io._natural_key`, `reconciler._natural`) | Architecture-fitness consolidation ticket — extract the shared Mermaid/key primitives to a neutral layer; the import-linter steal-list item would mechanically catch this edge | Before more cross-layer edges accrue |
| Phase 3 milestone test + offline smoke harness | `tests/test_phase3_milestone.py` (lands with this closure); `scripts/scratch_seed.py` (zero-API hands-on harness) | Land with the close |
| MODIFY-apply must not clobber `estimated_effort` | Documented invariant in `dependency-engine.md`; consumed by the future MODIFY-apply ticket (also carried from Phase 2) | Before MODIFY-apply is built |
| Runtime-observability-as-computational-evidence loop | Highest-leverage longer-term addition; design owner TBD (a future phase) | Design-stage |
| Steal-list (import-linter / dependency-cruiser, mutation testing, agent-authored-suppression linters, KB-freshness sensor) | Steal-list register; promote to tickets when prioritised | As prioritised |

---

## 8. Phase 4 readiness

Phase 4 (PM Engine + Linear sync) is governed by
`pm-engine-and-linear-sync.md` and is the first consumer of the Phase 3
engine: it promotes ready work (the readiness predicate), uses blockers
and `unlocks` for sequencing hints, and mirrors state under ADR-0006
field ownership. The dependency engine hands Phase 4 exactly the read-only
analyses it needs, all advisory, so the PM Engine owns the decisions and
the dependency engine owns the facts.

Two sequencing notes:

- The Phase 4 design document is the gating carry-forward — write it
  before building, the way every prior phase was governed by its canonical
  design doc first.
- Resolve the `dependencies → planning` layering (§7) before Phase 4
  adds its own cross-layer edges (the PM Engine will import the readiness
  predicate and likely the renderer). Cleaning the one inversion now is
  cheaper than after Phase 4 multiplies it, and it is the natural first
  use of an import-linter contract.

The Dependency Engine is built, validated, and proven on fixtures. Phase
4 builds delivery coordination on top of facts it can trust.

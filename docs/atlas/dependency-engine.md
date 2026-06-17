# Dependency Engine Design (Phase 3)

Status: Active design document for Phase 3.

## Graph semantics

The graph is a NetworkX `DiGraph` projected on demand from the relational
tables (never persisted; the database is authoritative per ADR-0006).

- Nodes: tickets, epics, ADRs, components, keyed by entity key.
- Edge direction: `A -> B` means **A depends_on B** (B must complete
  first). `blocks` is the reverse view, derived at query time, never
  stored.
- Execution order is therefore a reverse topological order of the ticket
  subgraph.

## Graph projection (build)

ATLAS-31 builds the projection above; the analyses below consume it.

- Entry points (`atlas/dependencies/graph.py`): `build_dependency_graph(db)`
  reads the applied tickets, epics, ADRs, and `TicketDependency` rows from
  storage and returns the `DiGraph`; `project_graph(tickets, epics, adrs,
  dependencies)` is the pure core — models in, graph out, no I/O, so the
  same state yields an identical graph. Neither writes; the graph is never
  persisted.
- Node identity is the **entity key**: ticket `ATLAS-<n>`, epic
  `ATLAS-E<n>`, ADR `ADR-<nnnn>` (synthesised `ADR-{number:04d}` — ADRs
  have no key column). The UUID-based dependency endpoints resolve to these
  keys once at build time.
- Each node carries `node_type`, `key`, `entity_id`, `present`, and a typed
  payload sufficient for the analyses without re-querying storage: a ticket
  node carries `status`, `priority`, `risk_level`, `estimated_effort` (as
  stored — may be null until ATLAS-32), `ticket_type`,
  `acceptance_criteria_count`, and `epic_key`; an epic node carries
  `status`, `priority`, `risk_level`; an ADR node carries `status` and
  `number`. These mirror data-model §4.1 `GraphNode`, carried as native
  graph attributes — the §4.1/§4.2 Pydantic models are reserved for a
  possible future persisted graph.
- Each edge carries `dependency_type`, `reason`, and `dependency_id` (the
  row id, so validation can attribute a finding to its row). All stored
  dependency rows are projected, each tagged with its `dependency_type`;
  execution-order analyses (readiness, critical path, blockers) traverse
  the `depends_on` edges. Only `depends_on` is stored — `blocks` is the
  derived reverse view — so a single real edge type exists per pair and a
  plain `DiGraph` (one edge per `(source, target)`) is sufficient and
  structurally forbids a duplicate `depends_on` between a pair. Revisit
  `MultiDiGraph` only if a future stored edge type can coexist with
  `depends_on` on the same pair; until then a same-pair collision keeps the
  lowest-`id` row deterministically.
- A dependency target that resolves to no stored entity is represented as
  an **absent node** (`present=False`, `node_type` the declared target
  type, keyed by the target UUID) with its edge intact — never dropped and
  never raised on. Graph validation (ATLAS-40) detects these; a `component`
  target is absent-but-dormant until Phase 5 (D2), not an error here.

## Readiness predicate

A ticket is ready exactly when all of the following hold (refines
data-model §4.3):

1. `status` is `planned` or `backlog`.
2. Every `depends_on` ticket target has `status: done`.
3. Every `depends_on` ADR target has `status: accepted`.
4. The ticket has ≥1 acceptance criterion.
5. No dependency target is missing (dangling targets make a ticket
   not-ready and raise a validation error, not a silent skip).

Readiness does not require a rendered context pack — pack rendering is the
PM Engine's promotion step (Phase 4), which consumes this predicate.

ATLAS-34 implements the predicate in `atlas/dependencies/readiness.py`:
`is_ready(graph, key)` for one ticket and `ready_tickets(graph)` for the
whole set. It reads ATLAS-31's node attributes only (`status`, `node_type`,
`present`, `acceptance_criteria_count`) — never storage — and returns a
typed `ReadinessResult` carrying the failing condition(s), not a bare
boolean, so a caller sees WHY a ticket is not ready: each failure is a
`NotReadyReason` with a machine `NotReadyCode` (wrong status, a depends_on
ticket not done, a depends_on ADR not accepted, no acceptance criteria, a
dangling target), and `ReadinessResult.ready` is derived from the reasons so
it can never contradict them. The target's `node_type` selects condition 2
vs 3 (ticket→done, ADR→accepted); target types the conditions do not name
(epic, component) are not gating.

Condition 5 and ATLAS-40: readiness runs on an already-validated graph,
where ATLAS-40's `validate_graph` has already raised `DanglingTargetError`
on any `present=False` target before readiness is computed — that raise is
the hard gate. Readiness does not re-detect dangling targets; it defends
against a `present=False` target by reporting a not-ready `DANGLING_TARGET`
reason rather than crashing or treating it as satisfied. A dangling target
is therefore never silently ready on either side.

## Critical path

Computed over the subgraph of tickets not in a terminal status:

- Longest path weighted by `estimated_effort` (null effort counts as 1).
- Tie-break: greater downstream dependent count, then higher priority,
  then key order.
- Output: ordered ticket list with cumulative effort, exposed via CLI and
  consumed by the PM Engine for sequencing hints. It is advisory; it never
  gates dispatch.

## Blocker analysis

- `blocked(t)`: the set of unfinished `depends_on` targets of `t`.
- `unlocks(t)`: tickets that become ready if `t` completes — the metric
  for "what unlocks the most future work".
- High-risk blocker report: any blocking ticket with `risk_level` in
  {high, critical}, surfaced because the readiness rule treats unresolved
  high-risk blockers as blocking even for otherwise-ready work.

## Validation rules

Run on every graph build and on every dependency mutation:

- Acyclicity (cycle reported with the full cycle path).
- No self-edges; no duplicate edges (same source, target, type).
- No dangling polymorphic targets.
- No `depends_on` from a terminal-status ticket to a non-terminal one
  (completed work cannot newly depend on pending work — indicates a data
  error).

Any failure is a typed error; `atlas apply` and the PM Engine refuse to
proceed on an invalid graph.

## CLI

`atlas deps ready | blocked | critical-path | unlocks <KEY> | validate |
graph` — `graph` regenerates `roadmap.mmd`. All subcommands support
`--json` for machine consumption.

## Open items

- Whether epics get rollup readiness (current position: no — epics are
  grouping, not gating).
- Component nodes are schema-supported but unused until something
  populates them; revisit in Phase 5.

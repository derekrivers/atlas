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

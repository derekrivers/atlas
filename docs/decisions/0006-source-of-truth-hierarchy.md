# ADR-0006: Source-of-truth hierarchy

## Status

Accepted

## Context

Active documents currently nominate three different sources of truth:

- ADR-0002 and the master plan name PostgreSQL as the initial source of truth.
- ARCHITECTURE.md describes a YAML-and-markdown MVP with PostgreSQL "later".
- The harness-engineering operating philosophy treats repository documents as
  the system of record.

Without an explicit hierarchy, the first conflict between a document, a
database row, and a Linear ticket has no resolution rule, and agents cannot
know which layer they are permitted to write to.

## Decision

Atlas uses a three-layer hierarchy. Each layer is authoritative for a
different category of truth, and writes flow in defined directions only.

1. **Repository documents are the source of truth for intent.**
   Vision, architecture, ADRs, domain knowledge, roadmap intent, workflow
   rules, and lessons (once promoted) live in markdown under version control.
   Changing intent means changing a document via a reviewed commit.

2. **The Atlas database is the source of truth for operational state.**
   Ticket status, agent runs, evidence records, verification checks,
   plan runs, and delivery metrics live in PostgreSQL (SQLite acceptable for
   the local MVP behind the same SQLAlchemy layer). Operational state is
   *derived from and traceable to* intent, never the other way around.

3. **Generated planning files are renders, not sources.**
   `docs/planning/*.yaml` and any visualisation are build artifacts of
   `atlas plan` / `atlas apply`. They are committed for legibility and diff
   review, but hand-editing them is prohibited; the doc linter must flag
   planning files modified outside an `atlas apply`.

Write directions:

- Humans write documents. Agents propose document changes via PR only.
- The Planning Engine writes planning renders only through `atlas apply`
  (ADR-0007).
- Ticket *definitions* flow Atlas → Linear. Ticket *status* flows
  Linear → Atlas. No other field syncs bidirectionally. Conflicts resolve in
  favour of the layer that owns the field.

## Rationale

Docs-as-intent matches the harness-engineering finding that anything not in
the repository is invisible to agents. A relational store for operational
state avoids encoding high-churn status data in markdown, where it would
create constant merge noise. Declaring renders non-authoritative eliminates
the three-way ambiguity entirely.

## Consequences

- ADR-0002 is clarified, not superseded: PostgreSQL is the source of truth
  *for operational delivery state*, not for intent.
- ARCHITECTURE.md must be updated to state the hierarchy.
- The doc linter (Phase 0) must enforce the no-hand-edit rule on
  `docs/planning/`.
- Every operational record must carry provenance back to intent
  (doc path + commit SHA, or ticket key).

## Alternatives considered

- Postgres-only source of truth: rejected; makes intent illegible to agents
  and unreviewable by the operator.
- Docs-only (status in markdown): rejected; status churn does not belong in
  reviewed documents.
- Linear as source of truth: rejected; Atlas must remain provider-agnostic
  and Linear arrives only in Phase 4.

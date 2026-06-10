# ADR-0002: Use PostgreSQL for operational state

## Status

Accepted (scope clarified by ADR-0006)

## Context

Atlas requires durable structured storage for tickets, evidence,
verification checks, plan runs, and agent runs before any graph or vector
store is justified. An earlier phrasing ("source of truth") conflicted
with the docs-as-intent model; ADR-0006 resolves the hierarchy.

## Decision

Use PostgreSQL as the system of record for **operational delivery state**.
SQLite is acceptable locally behind the same SQLAlchemy layer. Repository
documents remain the source of truth for intent (ADR-0006).

## Rationale

Reliable, familiar, JSONB-capable, trivially hostable, and sufficient for
the MVP; the dependency graph projects from relational tables into
NetworkX.

## Consequences

- Graph and vector stores are deferred until the core loop is proven.
- All operational records carry provenance back to intent.

## Alternatives considered

- Neo4j from day one: premature.
- SQLite-only: acceptable locally, kept Postgres-compatible.
- Document database: weaker relational integrity for dependencies.

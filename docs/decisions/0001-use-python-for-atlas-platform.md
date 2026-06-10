# ADR-0001: Use Python for the Atlas platform foundation

## Status

Accepted

## Context

Atlas needs a primary implementation language for the planning engine,
dependency graph, evidence pipeline, and CLI. The single operator works
fastest in Python, the AI/agent tooling ecosystem (Pydantic, NetworkX,
SQLAlchemy, provider SDKs) is strongest there, and coding agents model
"boring", well-represented stacks most reliably.

## Decision

Use Python (3.11+) for the Atlas platform foundation.

## Rationale

Pydantic gives the schema-as-contract layer the data model depends on;
NetworkX covers the dependency graph without a graph database; agent
legibility favours mainstream, well-documented technology.

## Consequences

- All harness code, models, and CLIs are Python until superseded.
- Performance-critical components, if any emerge, require a future ADR.

## Alternatives considered

- TypeScript: strong ecosystem, weaker fit for the data/graph tooling.
- Elixir (Symphony's reference choice): excellent concurrency, poor fit
  for a single-operator Python-centric stack.

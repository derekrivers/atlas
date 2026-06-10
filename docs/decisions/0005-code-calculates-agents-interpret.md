# ADR-0005: Code calculates, agents interpret

## Status

Accepted

## Context

LLM-computed quantitative values are unauditable and irreproducible. Atlas
must be able to show exactly how every number it records was produced.

## Decision

Deterministic, tested code computes all quantitative values (metrics,
counts, coverage figures, dependency-graph calculations, scores,
aggregations). Agents interpret: meaning, quality, risk narrative,
trade-offs, judgement, confidence.

## Rationale

This mirrors the platform-wide division of labour (ADR-0007/0008):
environments own recoverable, checkable bookkeeping; models own semantic
judgement. Numbers are bookkeeping.

## Consequences

- No LLM-only numeric calculations anywhere in Atlas or products built on
  it.
- Every recorded outcome links input data, calculated values, and agent
  interpretation separately, so each can be audited independently.

## Alternatives considered

- Agent-computed values with spot checks: unauditable, rejected.
- Hybrid agent-calculation with code verification: doubles work, rejected.

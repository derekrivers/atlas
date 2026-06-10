# ADR-0003: Use a single repository for harness documentation

## Status

Accepted

## Context

Anything an agent cannot reach in-repo effectively does not exist to it
(the harness-engineering principle). Splitting strategy, architecture, and
planning docs across repositories or external tools would make organisational
knowledge illegible to execution agents.

## Decision

All Atlas harness documentation — strategy, specification, architecture,
ADRs, roadmap, runbooks, lessons — lives in the single Atlas repository,
indexed by `docs/MANIFEST.md`.

## Rationale

One repo means one context boundary, one doc linter, one anchor scheme for
the Planning Engine, and version-controlled knowledge with the code.

## Consequences

- External discussions must be encoded into repo documents to count.
- The doc linter (Phase 0) enforces internal consistency.

## Alternatives considered

- Wiki / Google Docs: invisible to agents, unversioned, rejected.
- Docs repo separate from code: splits the anchor space, rejected.

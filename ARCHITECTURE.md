# ARCHITECTURE.md

Atlas has the following canonical architecture:

```text
Human Intent
→ Knowledge System
→ Planning Engine (plan/apply, ADR-0007)
→ Dependency Engine
→ Project Manager Engine
→ Context Renderer
→ Execution Agents
→ Evidence Store (trust-tiered, ADR-0008)
→ Verification Engine
→ Knowledge Update (lesson promotion gate, ADR-0009)
```

## Source of truth (ADR-0006)

- Repository documents: source of truth for **intent**.
- Atlas database (SQLite locally, PostgreSQL-compatible): source of truth
  for **operational state**, always traceable to intent.
- `docs/planning/*.yaml` and `roadmap.mmd`: **renders**, written only by
  `atlas apply`.

The MVP starts locally with Python, Pydantic, SQLAlchemy/Alembic, YAML
renders, NetworkX, and markdown documents. Linear, GitHub evidence
ingestion, and Symphony integrations come later, in that order.

For detail, read `docs/architecture/technical-architecture.md`.

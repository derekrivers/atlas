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

## Layer spine

The `atlas` package is layered. Higher layers may import lower ones; a
lower layer must never import a higher one. Ordered high → low:

```text
atlas.cli           # CLI entry point and command wiring
atlas.planning      # plan/apply pipeline, reconciler, renderer
atlas.dependencies  # dependency-graph projection and analyses
atlas.storage       # database, tables, repositories
atlas.linear        # Phase 4 Linear boundary: client + field ownership
atlas.core          # models, enums, shared primitives
```

`atlas.tools` and `atlas.__main__` are consumers outside the spine
(`__main__` imports the CLI; nothing imports `tools`) and are
intentionally unconstrained. The order above is executable: an
`import-linter` `layers` contract (pyproject `[tool.importlinter]`)
enforces it in pre-commit and CI, so any inverted edge — e.g.
`dependencies → planning` — fails the build.

For detail, read `docs/architecture/technical-architecture.md`.

# Atlas Bootstrap Guide

## Purpose

This guide explains exactly what to do next now that the core Atlas documents exist.

You now have the strategic and technical foundation for Atlas:

- atlas-master-plan.md
- system-specification.md
- technical-architecture.md
- implementation-roadmap.md
- data-model-and-schemas.md

The next objective is not to build a product yet.

The next objective is to bootstrap Atlas itself.

Atlas must first become capable of reading its own documentation, generating a backlog, producing dependency-aware tickets, and creating execution-ready context packs.

---

# 1. The Immediate Goal

The first milestone is:

> Atlas can read its own docs and generate a structured dependency-aware backlog.

This is the first proof that Atlas is becoming a real harness rather than just a document set.

The first working commands should be:

```bash
atlas plan    # LLM proposal -> validation gates -> reconciled diff
atlas apply   # operator approval -> renders + PlanRun
```

On operator approval, `atlas apply` writes the renders:

```text
docs/planning/epics.yaml
docs/planning/tickets.yaml
docs/planning/dependencies.yaml
docs/planning/roadmap.mmd
```

`atlas plan` never writes them (ADR-0007).

Do not start with Symphony.

Do not start with Linear automation.

Do not start with product features.

Start with a local planning CLI.

---

# 2. Recommended Build Order

The correct build order is:

```text
Documents
    ↓
Repository skeleton
    ↓
Python project setup
    ↓
CI and doc linter (mechanical trust)
    ↓
Pydantic schemas
    ↓
Planning CLI
    ↓
YAML backlog generation
    ↓
Dependency graph
    ↓
Context pack renderer
    ↓
Evidence store
    ↓
Linear integration
    ↓
Symphony integration
```

This order matters.

If you start with agents too early, there will be no harness for them to operate inside.

---

# 3. Create the Repository

Create a new repository called:

```text
atlas
```

Recommended commands:

```bash
mkdir atlas
cd atlas
git init
```

Create the folder structure:

```bash
mkdir -p docs/atlas
mkdir -p docs/architecture
mkdir -p docs/decisions
mkdir -p docs/planning
mkdir -p docs/product
mkdir -p docs/runbooks
mkdir -p docs/tech-debt
mkdir -p tests
mkdir -p atlas
```

Create further structure (`apps/`, `workers/`, `infra/`) only when the
phase that needs it arrives.

Expected structure:

```text
atlas/
├── AGENTS.md
├── PRODUCT.md
├── ARCHITECTURE.md
├── ROADMAP.md
├── WORKFLOW.md
├── docs/
│   ├── atlas/
│   ├── architecture/
│   ├── decisions/
│   ├── planning/
│   ├── product/
│   ├── runbooks/
│   └── tech-debt/
├── atlas/
└── tests/
```

---

# 4. Add the Existing Atlas Documents

Place the generated documents here:

```text
docs/atlas/atlas-master-plan.md
docs/atlas/system-specification.md
docs/architecture/technical-architecture.md
docs/atlas/implementation-roadmap.md
docs/architecture/data-model-and-schemas.md
```

These documents become the initial seed memory for Atlas.

---

# 5. Create Root Control Documents

Create the following files in the repository root:

```text
AGENTS.md
PRODUCT.md
ARCHITECTURE.md
ROADMAP.md
WORKFLOW.md
```

At this stage, these can be short index documents that point to the deeper files.

---

## 5.1 AGENTS.md

Purpose:

Tell coding agents how to work in the repo.

The canonical AGENTS.md in the repository root is the source of truth; the starter content below is illustrative only:

```markdown
# AGENTS.md

## Purpose

This repository contains Atlas, a stateful organisational operating system for autonomous software delivery, planning, evidence tracking, and knowledge accumulation.

## Required Reading

Before starting work, read:

1. docs/atlas/atlas-master-plan.md
2. docs/atlas/system-specification.md
3. docs/architecture/technical-architecture.md
4. docs/architecture/data-model-and-schemas.md
5. docs/atlas/implementation-roadmap.md

## Rules

- Do not build product features before the Atlas harness foundation exists.
- Do not introduce Linear integration before local planning works.
- Do not introduce Symphony integration before context packs exist.
- Do not create broad rewrites.
- Keep tickets small and dependency-aware.
- Update documentation when behaviour changes.
- Tests must pass before work is considered complete.

## First Milestone

A local generative planning loop:

```bash
atlas plan    # LLM proposal -> gates -> reconciled diff
atlas apply   # operator approval -> docs/planning renders + PlanRun
```
```

---

## 5.2 PRODUCT.md

Purpose:

Summarise what Atlas is.

Starter content:

```markdown
# PRODUCT.md

Atlas is a stateful organisational operating system for autonomous software delivery, knowledge accumulation, evidence-driven execution, and continuous organisational learning.

Atlas itself is the platform.

The first goal is to build the Atlas harness:

- knowledge system
- planning engine
- dependency engine
- context renderer
- evidence store
- verification engine
- project manager engine

The first working feature is:

```bash
atlas plan
```
```

---

## 5.3 ARCHITECTURE.md

Purpose:

Explain the system layers.

Starter content:

```markdown
# ARCHITECTURE.md

Atlas has the following architecture:

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

Source of truth (ADR-0006): repository documents for intent; the Atlas
database (SQLite locally, PostgreSQL-compatible) for operational state;
docs/planning/ files are renders written only by atlas apply.

The MVP starts locally with Python, Pydantic, SQLAlchemy/Alembic, YAML
renders, NetworkX, and markdown documents. Linear, GitHub evidence
ingestion, and Symphony integrations come later, in that order.
```

---

## 5.4 ROADMAP.md

Purpose:

Point to the implementation roadmap.

Starter content:

```markdown
# ROADMAP.md

The canonical roadmap lives at:

docs/atlas/implementation-roadmap.md

The immediate milestone is:

Atlas can read its own docs and generate a dependency-aware backlog.
```

---

## 5.5 WORKFLOW.md

Purpose:

Define how development proceeds.

Starter content:

```markdown
# WORKFLOW.md

## Current Workflow

Development begins locally.

No Linear or Symphony automation should be introduced until the local planning CLI works.

## First Workflow

1. Edit Atlas docs (intent).
2. Run atlas plan — LLM proposal, validation gates, reconciled diff.
3. Review the diff.
4. Run atlas apply — keys assigned, renders written, PlanRun recorded.
5. Commit docs and generated planning output together.

## Future Workflow

Docs → Planning Engine → Dependency Graph → Linear → Context Pack → Symphony → PR → Evidence → Verification → Learning
```

---

# 6. Initialise Python Project

Atlas should start as a Python project.

Recommended stack:

```text
Python 3.11+
Pydantic
Jinja2
PyYAML
NetworkX
SQLAlchemy
Alembic
```

Dev tooling: pytest, ruff, mypy, pre-commit.

Typer and Rich arrive with the `atlas` CLI (Phase 2); they are not part
of the Phase 0 project setup.

The project is managed with `uv` and `uv.lock` is committed. On a fresh
clone:

```bash
uv sync
```

To add dependencies later:

```bash
uv add <package>
uv add --dev <dev-tool>
```

Recommended package layout:

```text
atlas/
├── __init__.py
├── cli.py
├── schemas/
│   ├── __init__.py
│   ├── product.py
│   ├── epic.py
│   ├── ticket.py
│   ├── dependency.py
│   └── context_pack.py
├── planning/
│   ├── __init__.py
│   ├── document_loader.py
│   ├── planner.py
│   └── yaml_writer.py
├── dependencies/
│   ├── __init__.py
│   └── graph.py
└── rendering/
    ├── __init__.py
    └── roadmap_mermaid.py
```

---

# 7. First Implementation Target

The first working command:

```bash
atlas plan
```

`atlas plan` is generative from day one (ADR-0007); there is no
deterministic-only mode.

It should:

1. Load the canonical documents from `docs/atlas`.
2. Produce an LLM proposal anchored to document headings.
3. Run the validation gates over the proposal.
4. Reconcile the proposal into a deterministic diff against the current
   backlog.
5. Stop. `atlas plan` never writes planning renders; on operator
   approval, `atlas apply` writes them (human-gated apply).

Output files (written only by `atlas apply`):

```text
docs/planning/epics.yaml
docs/planning/tickets.yaml
docs/planning/dependencies.yaml
docs/planning/roadmap.mmd
```

---

# 8. First Tickets

The canonical first backlog is the Phase 0 epic in
`docs/atlas/implementation-roadmap.md`, and later phases live there
too; do not maintain a parallel ticket list in this guide. Roadmap keys
are illustrative seeds until `atlas apply` assigns real keys
(ADR-0007). Reproduced from the roadmap for orientation:

```text
ATLAS-1  Repository structure per bootstrap guide
ATLAS-2  Python project setup (uv, pytest, ruff, mypy, pre-commit)
ATLAS-3  CI pipeline: tests, lint, type-check on every PR
ATLAS-4  Doc linter v1: validate ADR files against the ADR model; check
         MANIFEST cross-links and intra-doc links; ban legacy v1/v2/v3
         document names in active docs; flag hand-edits to docs/planning/
         outside `atlas apply`
ATLAS-5  Repair documentation drift surfaced by the linter
ATLAS-6  Land ADR-0006..0009 and the Planning Engine Specification as
         canonical; update root control documents
```

Milestone test: CI is green; the doc linter passes on the whole
repository and fails on a seeded bad fixture (an ADR missing rationale,
a stale MANIFEST link, a hand-edited planning file).

---

# 9. CLI Design

The initial CLI should support:

```bash
atlas plan
atlas apply
atlas validate
atlas graph
atlas context ATLAS-1
```

But only the `atlas plan` / `atlas apply` pair needs to exist first.

Recommended Typer structure:

```python
import typer

app = typer.Typer()

@app.command()
def plan():
    \"\"\"Propose a plan diff; never writes planning renders (ADR-0007).\"\"\"
    ...

@app.command()
def apply():
    \"\"\"Write planning renders after operator approval of the diff.\"\"\"
    ...

@app.command()
def validate():
    \"\"\"Validate generated planning files.\"\"\"
    ...

@app.command()
def graph():
    \"\"\"Build and inspect the dependency graph.\"\"\"
    ...

@app.command()
def context(ticket_key: str):
    \"\"\"Render a context pack for a ticket.\"\"\"
    ...

if __name__ == "__main__":
    app()
```

---

# 10. Generated YAML Shapes

Shapes below are illustrative; the Pydantic models are the single
contract, and the full render format (generated header, field order,
sorting, key + id pairing) is defined in
`docs/architecture/knowledge-core.md`.

## epics.yaml

```yaml
# Render written only by atlas apply. plan_run_id: <uuid>
# prompt_version: planner-v1.0.0
epics:
  - key: EPIC-FOUNDATION
    title: Repository Foundation
    phase: Phase 0
    objective: Bootstrap the Atlas repository and documentation system.
    status: planned
```

## tickets.yaml

```yaml
# Render written only by atlas apply. plan_run_id: <uuid>
# prompt_version: planner-v1.0.0
tickets:
  - key: ATLAS-1
    epic_key: EPIC-FOUNDATION
    title: Create Repository Skeleton
    status: planned
    ticket_type: infrastructure
    risk_level: low
    acceptance_criteria:
      - Repository structure exists.
      - Core docs folders exist.
```

## dependencies.yaml

```yaml
# Render written only by atlas apply. plan_run_id: <uuid>
# prompt_version: planner-v1.0.0
dependencies:
  - source: ATLAS-2
    target: ATLAS-1
    type: depends_on
    reason: Root docs require repository skeleton first.
```

---

# 11. Definition of Done for Phase 0

Phase 0 is complete when (per the roadmap's Phase 0 milestone test):

- Repository structure and root control docs exist.
- Core documents are committed and internally consistent.
- The Python project runs and tests pass.
- CI runs tests, lint, type-check, and the doc linter on every PR.
- The doc linter passes on the whole repository and fails on a seeded
  bad fixture (an ADR missing rationale, a stale MANIFEST link, a
  hand-edited planning file).

The proof commands:

```bash
uv run pytest
uv run python -m atlas.tools.doc_linter
```

The planning CLI is not Phase 0 work; the plan/apply loop is the
Phase 2 milestone.

---

# 12. When to Add Linear

Add Linear only after:

- tickets.yaml exists
- dependencies.yaml exists
- dependency graph can identify ready tickets
- ticket schema is stable

Then build:

```bash
atlas linear sync
```

This command should:

- Create missing tickets in Linear.
- Update existing tickets.
- Apply labels.
- Preserve dependencies.
- Avoid overwriting human edits unless explicitly allowed.

---

# 13. When to Add Symphony

Add Symphony only after:

- context packs exist
- tickets can be marked Ready for Agent
- evidence schema exists
- verification rules exist

Then build:

```bash
atlas execute ATLAS-42
```

Flow:

```text
Ticket
→ Context Pack
→ Symphony
→ PR
→ Evidence
→ Verification
→ Knowledge Update
```

---

# 14. When to Start Product Work

Start product work only after the Atlas harness MVP exists.

Minimum required harness capabilities:

- planning output
- dependency graph
- context pack generation
- evidence recording
- verification checklist

---

# 15. Practical Day-One Checklist

Do this first:

```text
1. Create GitHub repo named atlas.
2. Clone locally.
3. Create folder structure.
4. Add generated Atlas docs under docs/atlas.
5. Add root AGENTS.md, PRODUCT.md, ARCHITECTURE.md, ROADMAP.md, WORKFLOW.md.
6. Initialise Python project.
7. Add CI: tests, lint, type-check on every PR.
8. Add the doc linter and wire it into CI and pre-commit.
9. Stop: Phase 0 ends here; the roadmap drives Phase 1 onward.
10. Commit everything.
```

Suggested first commit:

```bash
git add .
git commit -m "Bootstrap Atlas harness documentation and planning foundation"
```

---

# 16. What Not To Do Yet

Do not yet:

- Build product features or UI.
- Integrate Symphony.
- Automate Linear.
- Add Neo4j.
- Add vector database.
- Add complex worker orchestration.

Those come later.

The first thing to prove is:

> Atlas can convert its own documentation into structured work.

---

# 17. The First Real Milestone

The first meaningful milestone is the plan/apply loop:

```bash
atlas plan
atlas apply
```

Expected outcome: `atlas plan` prints a reconciled diff (counts of
ADD / MODIFY / PROPOSE_ARCHIVE / CONFLICT entries and one block per
entry); after operator approval, `atlas apply` writes the renders and
records a PlanRun.

That is the moment Atlas becomes more than a plan.

It becomes the first version of the planning engine.

---

# 18. Suggested Folder Snapshot After Phase 0

```text
atlas/
├── AGENTS.md
├── CLAUDE.md
├── README.md
├── PRODUCT.md
├── ARCHITECTURE.md
├── ROADMAP.md
├── WORKFLOW.md
├── pyproject.toml
├── uv.lock
├── .github/
│   └── workflows/
│       └── ci.yml
├── docs/
│   ├── MANIFEST.md
│   ├── atlas/
│   ├── architecture/
│   ├── archive/
│   ├── decisions/
│   ├── planning/          # empty render target; written only by atlas apply
│   ├── product/
│   ├── runbooks/
│   └── tech-debt/
├── atlas/
│   ├── __init__.py
│   ├── planning/
│   │   └── prompts/
│   └── tools/
│       └── doc_linter.py
├── tools/
│   └── run_planner.py
└── tests/
```

---

# 19. Recommended First Agent Prompt

Use the reusable ticket prompt template in
`docs/runbooks/agent-ticket-prompt.md`: fill in `{TICKET-KEY}` and the
ticket's scope from the roadmap, keep the plan-approval gate, and run
one ticket per session. Do not paste ticket lists with hard-coded keys
into prompts; keys live in the roadmap and, once the planner is live,
in the rendered backlog.

---

# 20. North Star

The goal is not to build any single product.

The goal is to build Atlas: a stateful organisational operating system that can plan, manage, execute, verify, and learn from software delivery.

Products come later.

First, build the machine that will build them.

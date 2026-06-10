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

The first working command should be:

```bash
atlas plan
```

The command should read the Atlas docs and generate:

```text
docs/planning/epics.yaml
docs/planning/tickets.yaml
docs/planning/dependencies.yaml
docs/planning/roadmap.mmd
```

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
mkdir -p docs/decisions
mkdir -p docs/domain
mkdir -p docs/planning
mkdir -p docs/tech-debt
mkdir -p docs/runbooks
mkdir -p apps
mkdir -p packages
mkdir -p workers
mkdir -p infra
mkdir -p tests
mkdir -p atlas
```

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
│   ├── decisions/
│   ├── domain/
│   ├── planning/
│   ├── runbooks/
│   └── tech-debt/
├── atlas/
├── apps/
├── packages/
├── workers/
├── infra/
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

The first milestone is a local planning CLI:

```bash
atlas plan
```

This should generate:

- docs/planning/epics.yaml
- docs/planning/tickets.yaml
- docs/planning/dependencies.yaml
- docs/planning/roadmap.mmd
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
→ Planning Engine
→ Dependency Engine
→ Project Manager Engine
→ Context Renderer
→ Execution Agents
→ Evidence Store
→ Verification Engine
→ Knowledge Update

The MVP starts locally with:

- Python
- Pydantic
- YAML files
- NetworkX
- Markdown documents

PostgreSQL, Linear, GitHub and Symphony integrations come later.
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

1. Edit Atlas docs.
2. Run atlas plan.
3. Generate YAML backlog.
4. Review generated tickets.
5. Commit generated planning output.

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
Typer
PyYAML
NetworkX
Rich
Jinja2
pytest
```

Use `uv` if you are comfortable with it.

```bash
uv init
uv add pydantic typer pyyaml networkx rich jinja2
uv add --dev pytest
```

Alternative using pip:

```bash
python -m venv .venv
source .venv/bin/activate
pip install pydantic typer pyyaml networkx rich jinja2 pytest
pip freeze > requirements.txt
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

For the MVP, this command can be mostly deterministic.

It does not need to use an LLM yet.

It should:

1. Load docs from `docs/atlas`.
2. Generate seed epics from `implementation-roadmap.md`.
3. Generate seed tickets from roadmap sections.
4. Generate dependencies.
5. Write YAML files.
6. Render a simple HTML roadmap.

Output files:

```text
docs/planning/epics.yaml
docs/planning/tickets.yaml
docs/planning/dependencies.yaml
docs/planning/roadmap.mmd
```

---

# 8. First Ten Manual Tickets

Before Atlas can generate its own tickets, manually create these as your first implementation backlog.

## ATLAS-1: Create Repository Skeleton

Objective:

Create the Atlas repository structure.

Acceptance criteria:

- Root docs folders exist.
- Atlas package folder exists.
- Tests folder exists.
- Core generated documents are placed under docs/atlas.

---

## ATLAS-2: Add Root Control Docs

Objective:

Create root-level AGENTS.md, PRODUCT.md, ARCHITECTURE.md, ROADMAP.md, and WORKFLOW.md.

Acceptance criteria:

- Each file exists.
- Each file points to docs/atlas.
- AGENTS.md defines first milestone.
- WORKFLOW.md prevents premature Linear/Symphony integration.

---

## ATLAS-3: Initialise Python Project

Objective:

Create a Python project for Atlas.

Acceptance criteria:

- Python package exists.
- Dependencies installed.
- Test runner works.
- CLI entrypoint exists.

---

## ATLAS-4: Add Core Pydantic Schemas

Objective:

Implement initial schemas from data-model-and-schemas.md.

Acceptance criteria:

- Product schema exists.
- Epic schema exists.
- Ticket schema exists.
- Dependency schema exists.
- ContextPack schema exists.
- Tests cover basic validation.

---

## ATLAS-5: Add Planning Document Loader

Objective:

Create a loader for Atlas markdown documents.

Acceptance criteria:

- Can load files from docs/atlas.
- Can return file name, path, and content.
- Handles missing docs cleanly.
- Tests exist.

---

## ATLAS-6: Add Seed Roadmap Parser

Objective:

Parse implementation-roadmap.md into rough phase, epic, and ticket sections.

Acceptance criteria:

- Phases can be detected.
- Ticket keys can be extracted.
- Ticket titles can be extracted.
- Tests cover parsing.

---

## ATLAS-7: Generate epics.yaml

Objective:

Generate an initial epics YAML file.

Acceptance criteria:

- epics.yaml is created.
- Epics include key, title, description, phase.
- Output is deterministic.
- Tests exist.

---

## ATLAS-8: Generate tickets.yaml

Objective:

Generate an initial tickets YAML file.

Acceptance criteria:

- tickets.yaml is created.
- Tickets include key, title, objective, type, status.
- Tickets are associated with epics where possible.
- Tests exist.

---

## ATLAS-9: Generate dependencies.yaml

Objective:

Generate basic dependencies from the roadmap order.

Acceptance criteria:

- dependencies.yaml is created.
- Dependencies are represented as source and target ticket keys.
- Output can be loaded into the graph engine.
- Tests exist.

---

## ATLAS-10: Generate roadmap.mmd

Objective:

Render a simple visual roadmap from the generated YAML files.

Acceptance criteria:

- roadmap.mmd (Mermaid render of the dependency DAG) is created.
- Shows phases, epics, and tickets.
- Shows blocked/ready status if available.
- Can be opened locally in a browser.

---

# 9. CLI Design

The initial CLI should support:

```bash
atlas plan
atlas validate
atlas graph
atlas context ATLAS-1
```

But only `atlas plan` needs to exist first.

Recommended Typer structure:

```python
import typer

app = typer.Typer()

@app.command()
def plan():
    \"\"\"Generate Atlas planning outputs from docs.\"\"\"
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

## epics.yaml

```yaml
epics:
  - key: EPIC-FOUNDATION
    title: Repository Foundation
    phase: Phase 0
    objective: Bootstrap the Atlas repository and documentation system.
    status: planned
```

## tickets.yaml

```yaml
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
dependencies:
  - source: ATLAS-2
    target: ATLAS-1
    type: depends_on
    reason: Root docs require repository skeleton first.
```

---

# 11. Definition of Done for Phase 0

Phase 0 is complete when:

- Repository exists.
- Core documents are committed.
- Root control docs exist.
- Python project runs.
- `atlas plan` command exists.
- Planning outputs are generated.
- Tests pass.

The proof command:

```bash
atlas plan
pytest
```

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
7. Add first schemas.
8. Add simple CLI.
9. Make atlas plan generate static YAML from the roadmap.
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

The first meaningful milestone is:

```bash
atlas plan
```

Expected output:

```text
Generated:
- 10 epics
- 100 tickets
- 120 dependencies
- roadmap.mmd
```

That is the moment Atlas becomes more than a plan.

It becomes the first version of the planning engine.

---

# 18. Suggested Folder Snapshot After Phase 0

```text
atlas/
├── AGENTS.md
├── PRODUCT.md
├── ARCHITECTURE.md
├── ROADMAP.md
├── WORKFLOW.md
├── pyproject.toml
├── docs/
│   ├── atlas/
│   │   ├── atlas-master-plan.md
│   │   ├── system-specification.md
│   │   ├── technical-architecture.md
│   │   ├── implementation-roadmap.md
│   │   └── data-model-and-schemas.md
│   ├── decisions/
│   ├── domain/
│   ├── planning/
│   │   ├── epics.yaml
│   │   ├── tickets.yaml
│   │   ├── dependencies.yaml
│   │   └── roadmap.mmd
│   └── tech-debt/
├── atlas/
│   ├── __init__.py
│   ├── cli.py
│   ├── schemas/
│   ├── planning/
│   ├── dependencies/
│   └── rendering/
└── tests/
```

---

# 19. Recommended First Agent Prompt

Once the repo exists, the first agent prompt should be:

```text
We are bootstrapping Atlas.

Read:
- AGENTS.md
- PRODUCT.md
- ARCHITECTURE.md
- ROADMAP.md
- WORKFLOW.md
- docs/atlas/implementation-roadmap.md
- docs/architecture/data-model-and-schemas.md

Your task:
Implement ATLAS-3, ATLAS-4 and ATLAS-5 only.

That means:
- initialise the Python project if missing
- create the core Pydantic schemas for Product, Epic, Ticket, Dependency and ContextPack
- create the markdown document loader
- add tests

Do not implement Linear.
Do not implement Symphony.
Do not build product features.
Do not add a database yet.

Definition of done:
- pytest passes
- docs updated if needed
- no unrelated work
```

---

# 20. North Star

The goal is not to build any single product.

The goal is to build Atlas: a stateful organisational operating system that can plan, manage, execute, verify, and learn from software delivery.

Products come later.

First, build the machine that will build them.

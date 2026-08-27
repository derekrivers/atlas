# Atlas Master Plan

## 1. Executive Summary

Atlas is a stateful organisational operating system for autonomous software delivery, knowledge accumulation, evidence-driven execution, and continuous organisational learning.

Atlas is not merely an AI coding wrapper. Atlas is the operating environment that allows interchangeable AI agents to perform narrow, well-defined, reviewable work while the organisation retains memory, decisions, evidence, dependencies, and lessons.

Products will be built on Atlas once the harness exists. Atlas itself is the primary asset.

The core principle is:

> Models provide reasoning. Atlas provides memory.

AI models can change. Providers can change. Execution agents can change. Atlas owns the durable knowledge layer.

---

## 2. Core Thesis

The opportunity is not to make AI magically build software. The opportunity is to build the harness around AI agents so they can repeatedly perform useful, auditable, evidence-backed work.

For software delivery, that means:

```text
Vision → Documentation → Planning → Dependencies → Tickets → Context → Execution → Evidence → Verification → Learning
```

Atlas succeeds when this loop compounds over time.

---

## 3. What Atlas Is

Atlas is a platform that coordinates:

- product knowledge
- architecture decisions
- roadmap planning
- dependency-aware ticket generation
- project management workflows
- execution context generation
- AI agent execution
- evidence collection
- completion verification
- organisational learning
- technical debt management

Atlas can be used to build and improve software products.

---

## 4. What Atlas Is Not

Atlas should not begin as:

- a pure LLM wrapper
- a system that trusts agent claims without evidence
- a system that relies on model memory for important knowledge
- a system where Symphony, Codex, or any other agent is responsible for product strategy

Atlas is the harness. Agents are workers inside the harness.

---

## 5. Core Philosophy

Traditional AI products often try to place the intelligence inside the model. Atlas takes the opposite position.

The model is temporary. The organisation is permanent.

Therefore:

- models provide reasoning
- Atlas provides memory
- documentation is executable organisational knowledge
- decisions are captured as ADRs
- dependencies are first-class citizens
- evidence is required for completion
- failures become reusable lessons
- successes become reusable patterns
- technical debt is actively managed

The guiding phrase is:

> Models think. Atlas remembers.

---

## 6. Atlas Platform Architecture

The current Atlas platform architecture is:

```text
Human Intent
    ↓
Knowledge System
    ↓
Planning Engine
    ↓
Dependency Engine
    ↓
Project Manager Engine
    ↓
Context Renderer
    ↓
Execution Agents
    ↓
Evidence Store
    ↓
Verification Engine
    ↓
Knowledge Update
```

This architecture is the canonical starting point for Atlas.

There is no active v1, v2, or v3 architecture in the repository. Earlier documents were design exploration. The repository starts from this canonical architecture.

---

## 7. Knowledge System

The Knowledge System stores durable organisational knowledge.

It includes:

### Product Memory

- vision
- goals
- constraints
- requirements
- user journeys
- acceptance criteria

### Architecture Memory

- ADRs
- architectural decisions
- technology choices
- integration strategies
- trade-offs

### Delivery Memory

- epics
- tickets
- dependencies
- PRs
- releases
- delivery status

### Organisational Memory

- lessons learned
- failure patterns
- success patterns
- known solutions
- technical debt history

The Knowledge System ensures Atlas does not depend on any model remembering previous work.

`docs/atlas/knowledge-context-consolidation.md` owns the dedicated programme
for keeping growing knowledge deterministic, authority-complete, measurable
and fresh without deleting history or turning generated projections into a
second source of truth.

---

## 8. Planning Engine

The Planning Engine converts product and architecture documents into executable work.

Inputs:

- PRODUCT.md
- ARCHITECTURE.md
- ROADMAP.md
- WORKFLOW.md
- ADRs
- domain documentation

Outputs:

- epics
- tickets
- dependencies
- milestones
- risk labels
- acceptance criteria

The first proof of Atlas is the local command:

```bash
atlas plan
```

This command should generate:

```text
docs/planning/epics.yaml
docs/planning/tickets.yaml
docs/planning/dependencies.yaml
docs/planning/roadmap.mmd
```

The Planning Engine is the missing layer between high-level design and executable Linear/Symphony work.

Planning is generative but governed: an LLM proposes, a deterministic reconciler diffs the proposal against the existing backlog, validation gates check it mechanically, and the operator approves via `atlas apply`. The model never assigns ticket keys and never modifies in-flight work. See `docs/atlas/planning-engine-specification.md` and ADR-0007.

---

## 9. Dependency Engine

Dependencies are first-class citizens in Atlas.

The Dependency Engine maintains a graph of:

- products
- epics
- tickets
- ADRs
- components
- evidence
- lessons

It answers:

- what can be worked on now?
- what is blocked?
- what unlocks the most future work?
- what is on the critical path?
- what can be parallelised?

Initial implementation should use PostgreSQL as source of truth and NetworkX for graph processing. A graph database can be added later only when the core loop is proven.

---

## 10. Project Manager Engine

The Project Manager Engine acts like an autonomous engineering manager.

Responsibilities:

- monitor Linear
- monitor GitHub
- update dependency state
- detect ready work
- keep blocked work blocked
- generate follow-up tickets
- detect failed agent work
- split oversized tickets
- maintain roadmap state
- create recurring technical debt work

The PM Engine does not write product code. It coordinates the delivery system.

Suggested delivery states:

```text
Backlog → Planned → Blocked → Ready for Agent → In Progress → PR Open → Review Required → Changes Requested → Done
```

---

## 11. Context Renderer

The Context Renderer creates compact execution packs for agents.

A context pack should include:

- ticket objective
- constraints
- relevant docs
- relevant ADRs
- related tickets
- dependency notes
- historical lessons
- acceptance criteria
- risks
- test commands
- definition of done

The purpose is to give an execution agent the minimum high-value context needed to complete a task without wasting tokens or encouraging broad rewrites.

---

## 12. Evidence and Verification

Atlas is evidence-driven.

An agent does not complete work by saying it is complete. Completion requires evidence.

Evidence types include:

- test results
- build results
- lint results
- coverage reports
- screenshots
- PR reviews
- deployment results
- documentation updates
- manual approvals

The Verification Engine checks:

- acceptance criteria are met
- tests pass
- CI passes
- documentation is updated
- scope is respected
- required evidence exists
- human approval exists where required

The rule is:

> No evidence = no completion.

Evidence is trust-tiered (ADR-0008): CI-sourced and human evidence is authoritative; agent-submitted evidence is capped at pending until corroborated. Evidence records are append-only and pinned to a commit SHA.

---

## 13. Learning System

The Learning System captures outcomes and makes future work better.

It records:

- success patterns
- failure patterns
- recurring blockers
- useful implementation approaches
- agent failure modes
- delivery metrics
- technical debt trends

Every completed ticket should create or update organisational knowledge when something useful was learned.

Atlas should get better because it remembers what worked and what failed.

---

## 14. Technical Debt Steward

Atlas should actively manage technical debt.

Suggested cadence:

### Daily

- failed CI
- flaky tests
- stale PRs
- repeated agent failures

### Weekly

- weak test coverage
- duplicated code
- oversized files
- stale documentation
- TODO/FIXME review

### Monthly

- dependency updates
- security review
- architecture review
- performance review
- database/index review

The goal is to stop agent-built systems degrading as they accelerate.

---

## 15. Agent Execution Layer

Atlas should support multiple execution providers:

- Symphony
- Codex
- OpenAI models
- Claude
- local models
- future providers

Agents are replaceable. Atlas remains constant.

Execution flow:

```text
Ready Ticket
    ↓
Context Pack
    ↓
Execution Agent
    ↓
Pull Request
    ↓
Evidence
    ↓
Verification
    ↓
Knowledge Update
```

Symphony is an execution engine. It should pick up ready, dependency-cleared, context-rich tickets. It should not own strategy, planning, or roadmap decomposition.

---

## 16. Safety and Governance

### Engineering Safety

- no secrets committed
- no destructive migrations without review
- no production deployment without CI
- no major architecture changes without ADR
- no large unexplained rewrites
- no ticket execution outside scope

### Agent Safety

- agents must follow AGENTS.md
- agents must read relevant docs
- agents must update docs when behaviour changes
- agents must not ignore failing tests
- agents must not invent APIs
- agents must not silently remove functionality

---

## 17. Implementation Strategy

The first goal is to bootstrap Atlas itself.

Recommended order:

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

Do not start with Symphony. Do not start with Linear automation. Do not start with product features.

Start with:

```bash
atlas plan
```

---

## 18. Key Design Decisions

1. Atlas is a platform, not a single product.
2. Models are replaceable.
3. Memory is the strategic asset.
4. Documentation is infrastructure.
5. Planning and execution are separate concerns.
6. Dependencies are first-class citizens.
7. Evidence is required for completion.
8. Organisational learning must persist.
9. Technical debt must be actively managed.
10. Code calculates; agents interpret.
11. Symphony executes; Atlas plans and governs.
12. PostgreSQL is the initial source of truth.
13. NetworkX is sufficient for the first dependency graph implementation.
14. The repo starts from the canonical architecture; earlier v1/v2/v3 labels are design history only.
15. Documents are the source of truth for intent; the database for operational state; planning YAML is a render (ADR-0006).
16. Planning is generative with deterministic reconciliation and human-gated apply (ADR-0007).
17. CI is the system-tier evidence producer; evidence is trust-tiered and commit-pinned (ADR-0008).
18. Atlas is single-operator; agent-authored lessons require operator promotion before retrieval (ADR-0009).

---

## 19. North Star

Atlas is the operating environment for a self-improving software organisation.

The core loop compounds software capability:

```text
Vision → Docs → Planning → Dependencies → Tickets → Execution → Evidence → Learning
```

This loop creates a continuously improving product-building system.

The final master principle is:

> Build the operating environment where AI agents can repeatedly perform narrow, well-defined, reviewable work.

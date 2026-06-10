# Atlas System Specification

## Vision

Atlas is a stateful organisational operating system that enables autonomous software delivery,
knowledge accumulation, evidence-driven execution, and continuous organisational learning.

Atlas itself is the platform; products are built on top of it once the harness exists.

---

# 1. Core Principles

1. Models provide reasoning.
2. Atlas provides memory.
3. Evidence is required for completion.
4. Dependencies are first-class citizens.
5. Documentation is executable organisational knowledge.
6. Planning and execution are separate concerns.
7. Learning compounds over time.

---

# 2. Atlas Architecture

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

---

# 3. Core Subsystems

## Knowledge System
Stores:
- Product Memory
- Architecture Memory
- Delivery Memory
- Organisational Memory

## Planning Engine
Generates:
- Epics
- Tickets
- Dependencies
- Acceptance Criteria

## Dependency Engine
Maintains:
- Dependency Graph
- Critical Path
- Blocked Work
- Parallel Work

## Project Manager Engine
Responsibilities:
- Linear coordination
- Ticket readiness
- Blocker management
- Delivery flow
- Roadmap updates

## Context Renderer
Builds compact task packs from:
- ADRs
- Docs
- Related tickets
- Historical lessons
- Relevant code

## Evidence Store
Stores:
- Test results
- CI output
- Coverage
- Screenshots
- Review outcomes

## Verification Engine
Confirms:
- Acceptance criteria met
- Tests pass
- Docs updated
- Scope respected

## Learning System
Captures:
- Success patterns
- Failure patterns
- Technical debt
- Delivery metrics

Agent-authored lessons enter as DRAFT and require operator promotion before they are retrievable into context packs (ADR-0009).

---

# 4. Knowledge Model

Product Memory
- Vision
- Requirements
- Personas
- User journeys

Architecture Memory
- ADRs
- Patterns
- Standards

Delivery Memory
- Epics
- Tickets
- PRs
- Releases

Organisational Memory
- Lessons learned
- Failure analyses
- Reusable solutions

---

# 5. Repository Structure

atlas/
├── AGENTS.md
├── PRODUCT.md
├── ARCHITECTURE.md
├── ROADMAP.md
├── WORKFLOW.md
├── docs/
├── apps/
├── packages/
├── workers/
├── infra/
├── atlas/
│   ├── planning/
│   ├── dependencies/
│   ├── context/
│   ├── evidence/
│   ├── knowledge/
│   └── pm/
└── tests/

---

# 6. Planning Engine Specification

Input:
- PRODUCT.md
- ARCHITECTURE.md
- ROADMAP.md
- ADRs

Output:
- Epics
- Tickets
- Dependencies
- Risk labels
- Milestones

Commands:

atlas plan   (LLM proposal -> validation gates -> reconciled diff)
atlas apply  (operator-approved diff -> planning renders + PlanRun)

Detail: docs/atlas/planning-engine-specification.md (ADR-0007)

---

# 7. Dependency Engine Specification

Graph Node Types:
- Epic
- Ticket
- ADR
- Component

Capabilities:
- Topological ordering
- Readiness detection
- Critical path calculation
- Blocker detection

---

# 8. PM Engine Specification

Loop:
1. Sync Linear
2. Update graph
3. Detect ready work
4. Move tickets
5. Detect debt
6. Generate follow-up work

---

# 9. Context Renderer Specification

Inputs:
- Ticket
- Dependencies
- ADRs
- Relevant docs
- Historical lessons

Output:
- Context Pack

Goal:
Provide the minimum high-value context required for successful execution.

---

# 10. Evidence Architecture

Evidence Types:
- Build
- Test
- Lint
- Coverage
- Documentation
- Review
- Deployment

Rule:
No evidence = no completion.

Trust tiers (ADR-0008): system (CI) and human evidence are authoritative; agent evidence is pending until corroborated. Records are append-only and commit-pinned.

---

# 11. Technical Debt Steward

Daily:
- Failed CI
- Flaky tests

Weekly:
- Coverage review
- Duplicate code review

Monthly:
- Security review
- Dependency review
- Architecture review

---

# 12. Symphony Integration

Flow:

Ready Ticket
    ↓
Context Pack
    ↓
Symphony
    ↓
PR
    ↓
Verification
    ↓
Knowledge Update

---

# 13. Phase Roadmap

Phase 0 - Foundation
Phase 1 - Knowledge System
Phase 2 - Planning Engine
Phase 3 - Dependency Engine
Phase 4 - PM Engine
Phase 5 - Context Renderer
Phase 6 - Evidence System
Phase 7 - Verification Engine
Phase 8 - Symphony Integration
Phase 9 - Learning System
Phase 10 - Multi-Product Atlas

---

# 14. Long-Term Vision

Atlas evolves into:

Documentation
→ Planning
→ Project Management
→ Organisational Memory
→ Evidence Driven Delivery
→ Multi-Agent Team
→ Self-Managing Software Company
→ Multi-Product Organisation
→ Atlas Managing Atlas

---

# Final Principle

Atlas is not an AI application.

Atlas is a stateful operating environment that accumulates organisational intelligence while interchangeable AI agents perform work inside it.

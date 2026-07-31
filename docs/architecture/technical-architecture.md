# Atlas Technical Architecture

## Purpose

This document defines the technical architecture of Atlas.

Atlas is a stateful organisational operating system that coordinates AI agents, maintains organisational memory, manages delivery workflows, and accumulates knowledge over time.

---

# 1. System Architecture

Human
  ↓
Atlas Knowledge Layer
  ↓
Planning Engine
  ↓
Dependency Engine
  ↓
Project Manager Engine
  ↓
Context Renderer
  ↓
Execution Agent
  ↓
Evidence Store
  ↓
Verification Engine
  ↓
Knowledge Update

---

# 2. Knowledge Store Design

## Product Memory

Stores:
- Vision
- Requirements
- Personas
- User Journeys
- Acceptance Criteria

Schema:

Product
- id
- name
- vision
- goals
- constraints
- created_at

## Architecture Memory

ADR
- id
- title
- decision
- rationale
- consequences
- status

## Organisational Memory

Lesson
- id
- category
- problem
- solution
- outcome
- confidence

---

# 3. Dependency Graph Model

Node Types:
- Product
- Epic
- Ticket
- ADR
- Component

Edge Types:
- depends_on
- relates_to
- implements
- blocks

Capabilities:
- Topological sorting
- Critical path analysis
- Readiness detection
- Dependency validation

Recommended Library:
- NetworkX

---

# 4. Planning Engine Design

Inputs:
- PRODUCT.md
- ARCHITECTURE.md
- ROADMAP.md
- ADRs

Outputs:
- Epics
- Tickets
- Dependencies
- Risk labels
- Acceptance criteria

Pipeline (ADR-0007; see docs/atlas/planning-engine-specification.md):

Documentation (+ git blob SHAs)
→ Proposer (LLM, versioned prompt template)
→ Proposal (schema-validated, anchored, null keys for new work)
→ Validation Gates (DAG, anchors, sizing, key integrity)
→ Deterministic Reconciler (key / anchor / similarity matching)
→ Plan Diff
→ Operator approval (atlas apply)
→ Planning renders + PlanRun record

---

# 5. Project Manager Engine

Responsibilities:
- Monitor Linear
- Update dependencies
- Detect blockers
- Detect ready work
- Generate debt tickets
- Maintain roadmap state

State Machine:

Backlog
→ Planned
→ Ready
→ In Progress
→ PR Open
→ Review
→ Done

---

# 6. Context Renderer

Purpose:

Create compact execution context for agents.

Inputs:
- Ticket
- ADRs
- Relevant docs
- Dependencies
- Historical lessons

Outputs:

Context Pack

Context Pack Structure:
- Objective
- Constraints
- Relevant Docs
- Acceptance Criteria
- Risks
- Test Commands
- Definition of Done

---

# 7. Evidence Store

Purpose:

Store proof of work.

Evidence Types:
- Test Results
- Build Results
- Coverage Reports
- Lint Reports
- Screenshots
- Review Outcomes

Schema:

Evidence
- id
- type
- ticket_id
- commit_sha
- external_run_id
- payload_hash
- source
- created_at

Records are append-only and trust-tiered (ADR-0008). CI (GitHub Actions) is the system-tier producer; the MVP polls the GitHub Checks API and normalises results into the same payload a webhook would deliver, so hosting later swaps the transport without schema change.

---

# 8. Verification Engine

Validation Rules:

Engineering:
- Tests pass
- CI passes
- Docs updated
- Scope respected

Rule:

No evidence = No completion

---

# 9. Learning System

Capture:
- Success patterns
- Failure patterns
- Delivery metrics
- Technical debt trends

Outputs:
- Lessons
- Playbooks
- Future context enrichment

---

# 10. Linear Integration

Capabilities:
- Create epics
- Create tickets
- Update status
- Manage dependencies
- Sync roadmap state

Field ownership per ADR-0006: ticket definitions flow Atlas → Linear; ticket status flows Linear → Atlas. No other field syncs bidirectionally.

---

# 11. GitHub Integration

Capabilities:
- Create branches
- Create pull requests
- Track reviews
- Track merges
- Collect evidence

---

# 12. Symphony Adapter

Flow:

Ticket
→ Context Pack
→ Symphony
→ Pull Request
→ Evidence
→ Verification
→ Knowledge Update

---

# 13. Atlas Database Model

Core Tables:

products
adrs
epics
tickets
dependencies
evidence
lessons
agent_runs
plan_runs
verification_checks
debt_items

---

# 14. APIs

Planning API
Dependency API
Knowledge API
Evidence API
PM API

---

# 15. Future Architecture

The next architecture programme expands Atlas through bounded authority rather
than replacing the delivered core:

1. **Governed write foundation.** Authenticated operator sessions,
   server-owned identity, idempotent commands and durable receipts precede any
   browser mutation.
2. **Exact-head acceptance.** Browser acceptance composes existing evidence,
   verification and mainline-freshness services; it does not gain merge
   authority.
3. **Capacity-aware admission.** Atlas governs entry to `Ready for Agent` with
   working, review, risk and component budgets. Symphony continues to schedule
   and run the admitted work.
4. **Evidence before adaptation.** Reproducible delivery intelligence feeds a
   distinct code-quality debt register, which can then support governed
   planning recommendations through the existing operator-owned apply seam.
5. **Product-scoped control.** Repository, tracker, credentials, policy,
   events, evidence, knowledge and capacity become explicitly product-scoped
   before Atlas coordinates a second product.
6. **Atlas managing Atlas.** The capstone composes detection, proposal,
   operator approval, bounded admission, exact-head acceptance and outcome
   measurement on Atlas itself.

This sequence is recorded in
`docs/atlas/phase-13-20-programme-horizon.md`. Knowledge graphs, vector search,
autonomous refactoring, hosted multi-user operation and production deployment
remain uncommitted future options. None is an implicit prerequisite or granted
authority for the Phase 13–20 programme.

---

# Final Technical Principle

Atlas is a stateful organisational platform.

Agents are execution engines.

Knowledge, evidence, dependencies, and learning are permanent assets owned by Atlas.

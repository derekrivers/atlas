# Atlas Master Plan v2

## Executive Summary

Atlas is a stateful organisational operating system for autonomous software delivery, knowledge accumulation, and product development.

The core insight behind Atlas is that AI models are temporary, while organisational knowledge is permanent.

Atlas separates reasoning from memory.

Reasoning is provided by interchangeable AI models:
- OpenAI
- Symphony
- Codex
- Claude
- Future models

Memory is provided by Atlas.

Atlas stores:
- Product knowledge
- Architectural decisions
- Delivery state
- Dependency graphs
- Implementation evidence
- Organisational learning
- Technical debt
- Historical outcomes

The first product built on Atlas is an investment research platform.

However, Atlas itself is the primary asset.

The investment platform is simply Product #1.

---

# 1. Core Philosophy

Traditional AI systems attempt to place intelligence inside the model.

Atlas takes the opposite approach.

Models provide reasoning.
Atlas provides memory.

The model is temporary.
The organisation is permanent.

Atlas is not an AI application.

Atlas is an organisational memory system that uses AI agents as workers.

---

# 2. Harness Principles

## Lessons from Harness-1

Harness-1 validated a critical principle:

> Frontier agent performance is driven by the quality of the harness surrounding the model.

Atlas adopts this philosophy.

The harness owns:
- State
- Memory
- Evidence
- Verification
- Context rendering
- Learning

The model owns:
- Reasoning
- Decision making
- Execution

## Models Are Stateless

All models forget.

Atlas remembers.

## Evidence Over Claims

Agents do not complete work by claiming completion.

Completion requires evidence:
- Tests passed
- CI passed
- Documentation updated
- Acceptance criteria satisfied

---

# 3. Atlas Reference Architecture

AI Models
↓
Atlas Harness
↓
Knowledge System
Planning System
Delivery System
↓
Products

Atlas is the operating system.

Agents are replaceable.

---

# 4. Atlas Knowledge System

## Product Memory

Stores:
- Vision
- Requirements
- User journeys
- Constraints
- Acceptance criteria

## Architecture Memory

Stores:
- ADRs
- Architectural decisions
- Technology choices
- Integration strategies

## Delivery Memory

Stores:
- Tickets
- PRs
- Reviews
- Releases
- Deployment records

## Organisational Memory

Stores:
- Historical failures
- Historical successes
- Lessons learned
- Known solutions

This becomes Atlas's long-term competitive advantage.

---

# 5. Planning Engine

Converts:

Vision
↓
Documentation
↓
Epics
↓
Tickets
↓
Dependencies

Responsibilities:
- Decomposition
- Sequencing
- Risk analysis
- Acceptance criteria generation

---

# 6. Dependency Engine

Dependencies are first-class citizens.

Atlas maintains a dependency graph.

Example:

Database
↓
Models
↓
Data Import
↓
Research Engine
↓
Scoring Engine

The graph determines:
- Ready work
- Blocked work
- Parallel work
- Critical path

---

# 7. Project Manager Engine

Acts as an autonomous engineering manager.

Responsibilities:
- Monitor Linear
- Monitor GitHub
- Maintain roadmap
- Manage dependencies
- Generate follow-up tickets
- Manage blockers
- Track velocity

The PM Engine coordinates work.

It does not write code.

---

# 8. Technical Debt Steward

Atlas continuously manages technical debt.

Daily:
- Failed builds
- Flaky tests
- Stale PRs

Weekly:
- Coverage analysis
- Duplicate code detection
- Large file detection
- Documentation drift

Monthly:
- Security review
- Dependency review
- Architecture review

---

# 9. Learning System

Workflow:

Ticket
↓
Implementation
↓
Evidence
↓
Outcome
↓
Knowledge Capture
↓
Future Retrieval

Every success becomes reusable.

Every failure becomes institutional knowledge.

---

# 10. Agent Execution Layer

Supported providers:
- Symphony
- Codex
- GPT
- Claude
- Future providers

Atlas remains constant.

Agents are execution engines.

---

# 11. Investment Research Platform

Atlas Research is Product #1.

Capabilities:
- Market data ingestion
- Financial analysis
- Stock screening
- Valuation analysis
- Sentiment analysis
- Recommendation generation
- Backtesting
- Newsletter generation

Principle:

> Code calculates. Agents interpret.

---

# 12. TradingAgents Integration

TradingAgents provides:
- Fundamental analysis
- Technical analysis
- News analysis
- Sentiment analysis
- Bull case
- Bear case
- Risk analysis

Atlas adds:
- Persistence
- Scoring
- Governance
- Tracking
- Backtesting
- Commercialisation

---

# 13. Repository Architecture

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
└── tests/

---

# 14. Documentation Architecture

Documentation is organisational infrastructure.

Core documents:
- AGENTS.md
- PRODUCT.md
- ARCHITECTURE.md
- ROADMAP.md
- WORKFLOW.md

Supporting:
- ADRs
- Domain docs
- Data contracts
- Planning outputs
- Runbooks
- Debt reports

---

# 15. Evidence Architecture

Every action should generate evidence.

Examples:
- Test reports
- Coverage reports
- CI results
- Screenshots
- Deployment evidence

Atlas becomes evidence-driven.

---

# 16. Safety and Governance

Product Safety:
- No live trading in v1
- Recommendations must be auditable

Engineering Safety:
- No secrets committed
- ADRs required for major changes

Agent Safety:
- Follow AGENTS.md
- Update documentation
- Do not ignore failing tests

---

# 17. Phase Roadmap

Phase 0 – Harness Foundation

Phase 1 – Planning Engine

Phase 2 – PM Engine

Phase 3 – Dependency Engine

Phase 4 – Knowledge System

Phase 5 – Organisational Memory

Phase 6 – Evidence System

Phase 7 – Symphony Integration

Phase 8 – Atlas Research Platform

Phase 9 – Backtesting

Phase 10 – Commercial Layer

Phase 11 – Paper Trading

Phase 12 – Optional Broker Integration

---

# 18. Long-Term Vision

Documentation
↓
Planning
↓
Project Management
↓
Knowledge Graph
↓
Organisational Memory
↓
Evidence Driven Delivery
↓
Multi-Agent Team
↓
Self-Managing Software Company
↓
Multi-Product Organisation
↓
Atlas Managing Atlas

---

# 19. Key Design Decisions

1. Research-first, not trading-first.
2. Atlas is a platform, not a trading bot.
3. Models are replaceable.
4. Memory is the strategic asset.
5. Documentation is infrastructure.
6. Planning and execution are separate concerns.
7. Dependencies are first-class citizens.
8. Evidence is required for completion.
9. Organisational learning must persist.
10. Technical debt must be actively managed.
11. Atlas is a stateful harness, not an AI application.
12. Atlas Research is Product #1.

---

# 20. Final Master Principle

> Models provide reasoning. Harnesses provide memory.

Atlas succeeds by becoming the persistent operating environment that stores knowledge, evidence, decisions, dependencies, and organisational learning while interchangeable AI agents perform the work.

Investing:

Data → Analysis → Recommendation → Tracking → Learning

Software Delivery:

Vision → Documentation → Planning → Dependencies → Tickets → Execution → Evidence → Learning

The first loop compounds investment intelligence.

The second loop compounds software capability.

Together they create a continuously improving software organisation.

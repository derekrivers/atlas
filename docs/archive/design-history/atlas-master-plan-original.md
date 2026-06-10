# Atlas Master Plan

## Executive Summary

Atlas is an autonomous investment research and software-delivery platform.

It has two major dimensions:

1. **Atlas Product**
   - An AI-powered investment research platform.
   - Uses market data, financial metrics, news, and agentic analysis to produce stock research, rankings, investment memos, backtests, and eventually subscriber-facing products.

2. **Atlas Build System**
   - A Harness/Symphony-powered autonomous engineering system.
   - Converts product vision into structured documentation, then into ordered Linear tickets, then into agent-executed pull requests.
   - Includes a planning engine, project manager engine, dependency manager, tech debt steward, and delivery loop.

The conclusion reached is that Atlas should not start as a simple trading bot. It should start as a structured autonomous research and software-building system.

The long-term ambition is to create a self-improving platform that compounds:

- investment knowledge
- software capability
- research output
- automation maturity
- commercial potential

Atlas is not just an app. It is a repeatable operating model for building apps with AI agents.

---

## 1. Core Thesis

The central thesis is:

> Wealth generation through AI should begin with research, ranking, evidence, tracking, and repeatable decision support before autonomous money movement.

A fully automated trading bot is risky, brittle, and difficult to prove. A research platform that continuously analyses markets, generates investment memos, tracks recommendations, and produces commercial content is more achievable and more valuable.

The second thesis is:

> AI coding agents only become genuinely useful when surrounded by a strong harness: documentation, architecture, issue decomposition, dependency management, tests, review loops, and project management.

Therefore Atlas needs both:

- an investment intelligence engine
- an autonomous software delivery engine

---

## 2. What Atlas Is

Atlas is an AI-powered investment research platform that analyses public equities and produces ranked investment intelligence.

Its purpose is to help identify attractive investment opportunities by combining deterministic financial calculations with LLM-powered interpretation.

Atlas should eventually support:

- stock screening
- company research
- valuation analysis
- technical analysis
- news analysis
- sentiment analysis
- risk assessment
- investment memo generation
- recommendation tracking
- backtesting
- portfolio simulation
- newsletter generation
- premium subscriber dashboards
- optional paper trading
- optional future broker integration

Atlas is not initially a live trading system.

It is first a research, scoring, tracking, and publishing platform.

---

## 3. What Atlas Is Not

Atlas should not begin as:

- a black-box trading bot
- a fully autonomous broker-connected system
- a high-frequency trading platform
- a magic stock predictor
- a pure LLM wrapper
- a system that lets LLMs calculate financial metrics directly
- a platform that trades real capital before proving historical usefulness

The first goal is not:

> “Let AI trade my money.”

The first goal is:

> “Build a reliable autonomous research department that can surface opportunities, explain them, track outcomes, and improve over time.”

---

## 4. External References

This plan was influenced by:

- OpenAI Harness Engineering: https://openai.com/index/harness-engineering/
- OpenAI open-source Codex orchestration, Symphony: https://openai.com/index/open-source-codex-orchestration-symphony/
- TradingAgents repository: https://github.com/TauricResearch/TradingAgents

---

## 5. TradingAgents Role

The TradingAgents repository should be used as the starting investment-research engine.

Its value is not that it can magically trade.

Its value is that it provides a multi-agent investment committee pattern.

The useful structure is:

- technical analyst
- fundamental analyst
- news analyst
- sentiment analyst
- bull researcher
- bear researcher
- trader/recommendation agent
- risk manager
- portfolio decision agent

This gives Atlas a ready-made research workflow.

However, TradingAgents should not be treated as the entire product.

Atlas should wrap, extend, control, and evaluate TradingAgents.

TradingAgents becomes:

> the research brain inside a larger product system.

Atlas adds:

- reliable data pipelines
- scoring methodology
- persistence
- performance tracking
- backtesting
- dashboards
- commercial publishing
- governance
- human approval gates
- orchestration
- safety controls

---

## 6. Product Architecture

The high-level Atlas product architecture is:

```text
Market Data Sources
        ↓
Data Import Workers
        ↓
PostgreSQL Research Database
        ↓
Deterministic Metrics Engine
        ↓
TradingAgents Research Layer
        ↓
Atlas Scoring Engine
        ↓
Recommendation Store
        ↓
Backtesting + Performance Tracking
        ↓
Dashboard + Newsletter + API
```

The key design rule is:

> Code calculates. Agents interpret.

Financial metrics should be calculated deterministically in Python.

Agents should interpret the calculated evidence.

For example:

Code calculates:

- P/E
- EV/EBITDA
- revenue growth
- free cash flow yield
- debt ratios
- ROIC
- volatility
- drawdown
- moving averages

Agents interpret:

- business quality
- investment thesis
- competitive advantage
- news significance
- risk narrative
- bull case
- bear case
- confidence level

---

## 7. Recommended Tech Stack

### Backend

Use Python because:

- TradingAgents is Python-based.
- Financial data pipelines are easier in Python.
- Backtesting libraries are stronger in Python.
- LLM orchestration tends to be Python-first.

Recommended backend stack:

```text
Python 3.11+
FastAPI
Pydantic
SQLAlchemy
Alembic
PostgreSQL
Celery
Redis
```

### Frontend

Use:

```text
Next.js
TypeScript
Tailwind
shadcn/ui
Recharts
```

This supports:

- dashboards
- score tables
- research pages
- filters
- charts
- subscriber UI
- portfolio visualisation

### Data

Primary market data:

```text
EODHD
```

Later additions:

```text
SEC filings
earnings transcripts
company announcements
news feeds
insider transactions
analyst estimates
macroeconomic data
```

### LLM Providers

Start with:

```text
OpenAI
```

Add provider abstraction for:

```text
Anthropic
local models
OpenRouter
Azure OpenAI
```

Do not optimise for local LLM hosting in v1.

### Infrastructure

MVP deployment:

```text
Docker Compose
Hetzner or DigitalOcean VPS
PostgreSQL
Redis
GitHub Actions
```

Later:

```text
managed Postgres
object storage
queue scaling
separate worker machines
observability stack
```

---

## 8. Repository Design

Atlas should use a single repository.

The Harness documentation must live inside the application repository because Symphony/Codex workers need direct access to it.

A separate Harness repo would only work if Symphony explicitly cloned or mounted it into every workspace, which adds fragility.

Recommended structure:

```text
atlas/
├── AGENTS.md
├── WORKFLOW.md
├── ARCHITECTURE.md
├── PRODUCT.md
├── ROADMAP.md
├── README.md
├── apps/
│   ├── api/
│   └── web/
├── packages/
│   ├── tradingagents_adapter/
│   ├── eodhd_client/
│   ├── scoring/
│   └── shared/
├── workers/
│   ├── data_importer/
│   ├── agent_runner/
│   ├── backtester/
│   └── newsletter/
├── docs/
│   ├── architecture/
│   ├── domain/
│   ├── product/
│   ├── data-contracts/
│   ├── decisions/
│   ├── planning/
│   ├── execution-plans/
│   ├── testing/
│   ├── runbooks/
│   └── tech-debt/
├── infra/
│   ├── docker-compose.yml
│   └── github-actions/
└── tests/
```

---

## 9. Harness Documentation Philosophy

The Harness docs are not ordinary documentation.

They are the operating system for the coding agents.

The principle is:

> If the agent cannot see it, it does not exist.

Therefore the repository must explain:

- what the product is
- what the architecture is
- what the domain concepts mean
- what the coding standards are
- what should never be done
- how tests are run
- how agents should approach tasks
- how tickets should be decomposed
- how recommendations work
- how financial data should be interpreted
- how scoring works
- how risk is handled
- how documentation is updated

The docs should not all live in `AGENTS.md`.

`AGENTS.md` should be a short map.

The deeper context should live under `docs/`.

---

## 10. Core Harness Documents

### AGENTS.md

Purpose:

- tell agents how to work in the repo
- point to the most important docs
- define non-negotiable rules
- define test commands
- define PR expectations

It should include:

```text
Before starting:
1. Read PRODUCT.md
2. Read ARCHITECTURE.md
3. Read relevant docs/domain files
4. Read the Linear ticket
5. Check related decisions in docs/decisions
```

It should also include rules like:

```text
Do not invent financial calculations.
Do not bypass deterministic metric code.
Do not add broker execution without explicit approval.
Do not modify scoring rules without updating docs/domain/atlas-score.md.
Do not create recommendations without persistence and auditability.
```

### PRODUCT.md

Defines:

- product vision
- target users
- core use cases
- commercial model
- MVP scope
- non-goals
- long-term ambition

### ARCHITECTURE.md

Defines:

- system layers
- backend/frontend boundaries
- worker design
- database responsibilities
- external integrations
- agent boundaries
- deployment model

### WORKFLOW.md

Defines:

- how Symphony picks up work
- what Linear statuses mean
- how tickets move
- how PRs are reviewed
- how failed tickets are handled
- how blocked tickets are managed

### ROADMAP.md

Defines:

- phases
- epics
- milestones
- dependency order
- release goals

### docs/domain/

Contains domain-specific truth.

Examples:

```text
company.md
security.md
price-history.md
financial-metrics.md
atlas-score.md
recommendations.md
agent-runs.md
investment-memos.md
backtesting.md
portfolio.md
risk.md
```

### docs/data-contracts/

Defines external data expectations.

Examples:

```text
eodhd.md
news.md
sec-filings.md
earnings-transcripts.md
```

For EODHD, this should define:

- adjusted vs unadjusted prices
- symbol format
- exchange handling
- missing data behaviour
- retry rules
- rate limits
- API failure behaviour
- historical import strategy

### docs/decisions/

Architecture Decision Records.

Examples:

```text
0001-use-python-fastapi.md
0002-use-postgresql.md
0003-use-single-repo-for-harness-docs.md
0004-do-not-enable-live-trading-in-v1.md
0005-use-code-for-calculations-and-llms-for-interpretation.md
```

### docs/planning/

Contains planning system outputs:

```text
roadmap.md
epics.md
dependency-graph.md
ticket-breakdown.md
release-plan.md
```

### docs/tech-debt/

Contains recurring debt reports and debt strategy:

```text
debt-register.md
weekly-debt-review.md
refactor-candidates.md
test-coverage-gaps.md
```

---

## 11. Atlas Domain Model

### Company

A company is the business entity.

Example:

```text
Apple Inc.
Bank of America
Shell plc
```

Fields:

- name
- description
- sector
- industry
- country
- website
- market cap
- employee count
- fiscal year end

### Security

A security is the traded instrument.

A company can have multiple securities.

Example:

```text
Company: Shell plc
Security: SHEL.L
Security: SHEL.AS
```

Fields:

- ticker
- exchange
- currency
- instrument type
- active status
- primary flag

### Daily Price

Represents historical OHLCV data.

Fields:

- security_id
- date
- open
- high
- low
- close
- adjusted_close
- volume
- data_vendor
- imported_at

Rule:

> Use adjusted close for long-term return analysis unless a specific reason exists to use raw close.

### Financial Metric

Represents calculated or imported business metrics.

Examples:

- revenue
- net income
- EBITDA
- free cash flow
- total debt
- cash
- ROIC
- ROE
- gross margin
- operating margin
- P/E
- EV/EBITDA
- FCF yield

Rule:

> Store raw source values separately from calculated ratios.

### Agent Run

A single execution of the research process.

Fields:

- company/security
- run type
- model used
- input data snapshot
- started_at
- completed_at
- status
- cost estimate
- token usage
- error details

### Agent Output

The structured output from an individual agent.

Examples:

- fundamental agent output
- technical agent output
- news agent output
- risk agent output
- bull thesis
- bear thesis

Fields:

- agent_run_id
- agent_name
- structured_json
- text_summary
- confidence
- warnings

### Recommendation

The final investment judgement.

Fields:

- security_id
- recommendation date
- action
- confidence
- atlas score
- fair value estimate
- current price
- margin of safety
- risk rating
- time horizon
- thesis summary

Actions:

```text
Strong Buy
Buy
Hold
Avoid
Sell
```

### Investment Memo

A generated research report.

Sections:

- executive summary
- business overview
- valuation
- fundamental analysis
- technical analysis
- bull thesis
- bear thesis
- risks
- catalysts
- final recommendation

### Backtest Result

Tracks how recommendations performed.

Fields:

- recommendation_id
- one_week_return
- one_month_return
- three_month_return
- six_month_return
- twelve_month_return
- benchmark_return
- excess_return
- max_drawdown
- notes

---

## 12. Atlas Scoring Engine

Atlas needs a proprietary score.

The Atlas Score should be deterministic where possible and explainable.

Initial components:

```text
Atlas Score =
  Fundamental Score
  + Valuation Score
  + Quality Score
  + Momentum Score
  + News Score
  - Risk Penalty
```

Suggested weighting for v1:

```text
Fundamental Score: 30%
Valuation Score: 25%
Quality Score: 20%
Momentum Score: 10%
News/Sentiment Score: 10%
Risk Adjustment: -5% to -30%
```

The scoring engine should produce:

- component scores
- final score
- explanation
- missing data warnings
- confidence level

Important rule:

> A high Atlas Score is not a trade instruction. It is a research ranking signal.

---

## 13. Recommendation Philosophy

Recommendations must be auditable.

Every recommendation should answer:

```text
What was recommended?
When was it recommended?
What price was used?
What evidence supported it?
What risks were identified?
Which model generated it?
Which data snapshot was used?
How did it perform later?
```

This is vital because the system must learn from outcomes.

Without tracking, Atlas is just producing opinions.

With tracking, Atlas becomes a research laboratory.

---

## 14. Backtesting and Performance Tracking

Backtesting is not optional.

It is how Atlas discovers whether its research has value.

The system should track:

- absolute return
- benchmark-relative return
- win rate
- average winner
- average loser
- drawdown
- sector bias
- country bias
- recommendation quality by agent
- recommendation quality by confidence band
- recommendation quality by market regime

Important future analysis:

```text
Do high-confidence recommendations outperform low-confidence ones?
Does the fundamental agent add value?
Does the news agent add noise?
Does the technical agent improve timing?
Are certain sectors consistently misjudged?
```

This allows Atlas to improve.

---

## 15. Commercial Model

Atlas can generate value in multiple ways.

### Personal Investment Research

The first user is the creator.

Atlas helps identify opportunities and avoid weak ideas.

### Premium Screener

Users pay for access to ranked investment opportunities.

Features:

- top Atlas Score companies
- undervalued companies
- quality compounders
- dividend opportunities
- risk alerts
- watchlists

### Newsletter

Weekly output:

- top opportunities
- biggest upgrades
- biggest downgrades
- market commentary
- deep-dive memo

### API

Later, Atlas could expose:

- score API
- recommendation API
- company memo API
- screening API

### Portfolio Tools

Later:

- model portfolios
- allocation recommendations
- rebalancing alerts
- paper trading

---

## 16. Product MVP

The MVP should not try to cover the whole market.

The MVP should prove the loop on a small universe.

Suggested MVP universe:

```text
S&P 500
or
FTSE 100 + S&P 500
```

MVP capabilities:

```text
Import company universe
Import daily prices
Import basic financial metrics
Run TradingAgents for one ticker
Persist agent outputs
Generate investment memo
Calculate Atlas Score
Display dashboard table
Track recommendation outcomes
```

MVP success is:

> The system can analyse a stock, explain the recommendation, store the result, and track whether it was useful.

---

## 17. Symphony Role

Symphony is not the product.

Symphony is the execution engine for building the product.

The build hierarchy is:

```text
Human Intent
        ↓
Harness Docs
        ↓
Planning Engine
        ↓
Linear Tickets
        ↓
Project Manager Engine
        ↓
Symphony
        ↓
Codex Workers
        ↓
Pull Requests
        ↓
Human Review
        ↓
Merged Code
```

Symphony should work from Linear tickets.

Each ticket should be small, clear, dependency-aware, and grounded in repository docs.

Symphony’s job:

- pick up ready tickets
- create isolated workspace
- run coding agent
- produce PR
- retry on failure
- report status

Symphony should not be responsible for product strategy.

---

## 18. Planning Engine

The Planning Engine is the missing layer between product design and Linear.

Its job is to convert:

```text
PRODUCT.md
ARCHITECTURE.md
ROADMAP.md
domain docs
```

into:

```text
epics
tickets
dependency graph
acceptance criteria
risk labels
implementation order
```

The Planning Engine should understand:

- what needs building
- what depends on what
- what can be parallelised
- what should be blocked
- what is too large
- what requires human approval
- what needs architecture review

Example output:

```text
Epic: Market Data Foundation

ATLAS-1: Create database schema
ATLAS-2: Add company model
ATLAS-3: Add security model
ATLAS-4: Implement EODHD client
ATLAS-5: Import daily prices
ATLAS-6: Add data import worker
ATLAS-7: Add import status dashboard
```

With dependencies:

```text
ATLAS-2 depends on ATLAS-1
ATLAS-3 depends on ATLAS-1
ATLAS-5 depends on ATLAS-3 and ATLAS-4
ATLAS-6 depends on ATLAS-5
ATLAS-7 depends on ATLAS-6
```

---

## 19. Project Manager Engine

The Project Manager Engine monitors Linear, GitHub, CI, PRs, and the roadmap.

Its job is to keep the app building.

It should act like an autonomous engineering manager.

Responsibilities:

```text
Monitor Linear board
Move unblocked tickets to Ready
Keep blocked tickets blocked
Detect completed dependencies
Detect failed tickets
Split oversized tickets
Create follow-up tickets
Create tech debt tickets
Prioritise work
Maintain delivery flow
Protect WIP limits
Update roadmap status
```

The PM Engine should not write product code.

It manages the delivery system.

---

## 20. Linear Workflow

Suggested statuses:

```text
Backlog
Planned
Blocked
Ready for Symphony
In Progress
PR Open
Review Required
Changes Requested
Done
Rejected
Needs Human Decision
```

Rules:

- Symphony only picks up `Ready for Symphony`.
- Tickets with unmet dependencies stay `Blocked`.
- Tickets requiring architecture decisions go to `Needs Human Decision`.
- Failed tickets are inspected by the PM Engine.
- Oversized tickets are split before retry.
- Completed tickets update dependency state.

---

## 21. Ticket Structure

Every Linear ticket should include:

```text
Title
Objective
Context
Relevant docs
Dependencies
Acceptance criteria
Non-goals
Implementation notes
Test requirements
Documentation requirements
Risk level
Definition of done
```

Example:

```text
Title:
Implement EODHD daily price import

Objective:
Import daily OHLCV price data for configured securities.

Context:
Atlas uses EODHD as the first market data provider. Daily prices must be persisted for later scoring, charting, and backtesting.

Relevant docs:
- docs/data-contracts/eodhd.md
- docs/domain/price-history.md
- ARCHITECTURE.md

Dependencies:
- Company model exists
- Security model exists
- Database migration system exists

Acceptance Criteria:
- EODHD client fetches daily OHLCV data
- adjusted_close is stored
- failed API calls are retried
- duplicate imports are idempotent
- tests cover success and failure cases
- docs are updated

Non-goals:
- Intraday prices
- Broker execution
- Full market import

Definition of Done:
- Tests pass
- PR opened
- Docs updated
- No unrelated refactors
```

---

## 22. Dependency Graph

The dependency graph is central.

Without it, the system becomes chaotic.

The graph should answer:

```text
What can be worked on now?
What is blocked?
What unlocks the most future work?
What tickets are prerequisites?
What tickets are parallelisable?
```

The PM Engine should use the dependency graph to move tickets automatically.

Example:

```text
Database Foundation
        ↓
Company + Security Models
        ↓
EODHD Client
        ↓
Daily Price Import
        ↓
Backtesting
        ↓
Recommendation Tracking
```

If `Daily Price Import` is not complete, backtesting should remain blocked.

---

## 23. Tech Debt Steward

The Project Manager Engine should include a Tech Debt Steward.

This is a scheduled agent that creates maintenance work.

Its purpose is to stop the codebase degrading as agents build quickly.

Cadence:

```text
Daily:
- inspect failed CI
- inspect flaky tests
- inspect stale PRs
- inspect repeated Symphony failures

Weekly:
- scan TODO/FIXME comments
- identify weak test coverage
- identify duplicated code
- identify oversized files
- identify stale docs
- create 2-3 tech debt tickets

Monthly:
- dependency update review
- security review
- performance review
- database/index review
- architecture review
```

The system should maintain a debt budget.

Suggested rule:

```text
70% feature work
20% tech debt
10% investigation/spikes
```

Or:

```text
Always keep 2-3 tech debt tickets ready.
Do not let tech debt exceed 25% of active work unless the build is unstable.
```

---

## 24. Handling Failed Agent Work

Failure is expected.

The system should learn from it.

If a Symphony task fails once:

```text
Retry with clearer instructions.
```

If it fails twice:

```text
Move to Review Required.
PM Engine analyses failure.
```

If it fails three times:

```text
Split ticket into smaller tickets.
Create prerequisite refactor if needed.
Move original ticket back to Blocked.
```

Example:

```text
Original:
Build recommendation dashboard

Split into:
- Add recommendation API endpoint
- Add recommendation table component
- Add recommendation detail page
- Add loading/error states
- Add frontend tests
```

This is how the PM Engine improves agent success rates.

---

## 25. Safety and Governance

Atlas must have explicit safety boundaries.

### Investment Safety

Rules:

```text
No live broker execution in v1.
No automatic real-money trading without human approval.
No recommendation without audit trail.
No recommendation without input data snapshot.
No hidden scoring changes.
No LLM-only financial calculations.
```

### Engineering Safety

Rules:

```text
No secrets committed.
No destructive migrations without review.
No production deployment without CI.
No architecture changes without ADR.
No large unexplained rewrites.
No ticket execution outside scope.
```

### Agent Safety

Rules:

```text
Agents must follow AGENTS.md.
Agents must read relevant docs.
Agents must update docs when behaviour changes.
Agents must not ignore failing tests.
Agents must not invent APIs.
Agents must not silently remove functionality.
```

---

## 26. Human Role

The human is not removed.

The human changes role.

The human should focus on:

- product direction
- architecture decisions
- risk appetite
- investment philosophy
- commercial strategy
- approval of sensitive changes
- review of major PRs
- budget control

The agents handle:

- ticket implementation
- routine refactors
- documentation updates
- test creation
- board movement
- dependency tracking
- research generation
- scheduled maintenance

The human becomes:

> founder, architect, investor, and reviewer.

---

## 27. Phase Roadmap

### Phase 0: Harness Foundation

Goal:

Create the repo as an agent-legible system.

Deliverables:

```text
AGENTS.md
WORKFLOW.md
PRODUCT.md
ARCHITECTURE.md
ROADMAP.md
docs/domain/*
docs/data-contracts/*
docs/decisions/*
docs/planning/*
```

Success criteria:

```text
An agent can understand what Atlas is, how to work on it, and what not to do.
```

### Phase 1: Planning System

Goal:

Convert design docs into Linear tickets.

Deliverables:

```text
planning engine
epic generator
ticket generator
dependency graph generator
Linear integration
ticket templates
```

Success criteria:

```text
Given the Atlas docs, the system can generate a dependency-aware project backlog.
```

### Phase 2: PM Engine

Goal:

Keep Linear flowing.

Deliverables:

```text
Linear board monitor
dependency resolver
ready-ticket mover
blocked-ticket manager
failed-ticket analyser
tech debt steward
```

Success criteria:

```text
The PM Engine can move tickets to Ready for Symphony only when dependencies are satisfied.
```

### Phase 3: Symphony Integration

Goal:

Allow Symphony to build Atlas from ready tickets.

Deliverables:

```text
Symphony setup
Codex workspace configuration
GitHub integration
PR workflow
CI integration
retry rules
```

Success criteria:

```text
A ready Linear ticket becomes a PR without manual coding.
```

### Phase 4: Atlas Data Foundation

Goal:

Ingest market data.

Deliverables:

```text
company model
security model
daily price model
EODHD client
daily import worker
import status API
```

Success criteria:

```text
Atlas can import and store price data for selected securities.
```

### Phase 5: Research Engine

Goal:

Integrate TradingAgents.

Deliverables:

```text
TradingAgents adapter
single ticker analysis
agent output persistence
investment memo generation
model/cost tracking
```

Success criteria:

```text
Atlas can analyse one ticker and store a complete research memo.
```

### Phase 6: Scoring and Recommendations

Goal:

Rank opportunities.

Deliverables:

```text
Atlas Score engine
recommendation model
recommendation API
score explanation
risk rating
```

Success criteria:

```text
Atlas can produce ranked recommendations with explanations.
```

### Phase 7: Dashboard

Goal:

Make research visible.

Deliverables:

```text
Next.js dashboard
company page
score table
recommendation history
memo viewer
charts
```

Success criteria:

```text
A user can browse Atlas research through a web UI.
```

### Phase 8: Backtesting

Goal:

Evaluate recommendation quality.

Deliverables:

```text
return tracking
benchmark comparison
recommendation performance dashboard
agent performance analysis
```

Success criteria:

```text
Atlas can show whether its recommendations performed well over time.
```

### Phase 9: Newsletter and Commercial Layer

Goal:

Turn research into a product.

Deliverables:

```text
weekly report generator
newsletter markdown/html output
subscriber auth
Stripe integration
premium screener
```

Success criteria:

```text
Atlas can produce subscriber-ready investment research.
```

### Phase 10: Paper Trading

Goal:

Simulate portfolio execution.

Deliverables:

```text
paper portfolio
position sizing rules
rebalance simulation
risk constraints
performance tracking
```

Success criteria:

```text
Atlas can manage a simulated portfolio using its recommendations.
```

### Phase 11: Future Broker Integration

Goal:

Optional controlled execution.

Deliverables:

```text
broker adapter
approval workflow
position limits
kill switch
audit logging
```

Success criteria:

```text
Atlas can prepare trades, but real execution remains gated by human approval unless explicitly changed.
```

---

## 28. First Ten Tickets

The first tickets should not build features.

They should establish the world the agents will operate in.

### ATLAS-1: Create Repository Skeleton

Create repo structure with docs, apps, packages, workers, infra.

### ATLAS-2: Add AGENTS.md

Define agent rules and point to deeper docs.

### ATLAS-3: Add Product Vision

Create PRODUCT.md with Atlas vision, users, goals, non-goals.

### ATLAS-4: Add Architecture Overview

Create ARCHITECTURE.md with system layers and boundaries.

### ATLAS-5: Add Workflow Rules

Create WORKFLOW.md describing Linear, Symphony, PR, review, and status flow.

### ATLAS-6: Add Domain Model Docs

Create docs/domain for company, security, prices, recommendations, agent runs, Atlas Score.

### ATLAS-7: Add ADR Framework

Create docs/decisions and first ADRs.

### ATLAS-8: Add Roadmap

Create ROADMAP.md and docs/planning initial roadmap.

### ATLAS-9: Add Ticket Template

Create a standard Linear ticket template.

### ATLAS-10: Add Dependency Graph Format

Define how dependencies will be represented and consumed by the PM Engine.

---

## 29. Key Design Decisions Reached

### Decision 1: Atlas is research-first, not trade-first

Reason:

Research is easier to validate, safer, and more commercially useful.

### Decision 2: TradingAgents is used as an engine, not the whole product

Reason:

It provides useful research orchestration, but Atlas needs storage, scoring, tracking, UI, and governance.

### Decision 3: One repository, docs embedded

Reason:

Symphony workers need direct access to Harness context.

### Decision 4: AGENTS.md is a map, not a manual

Reason:

Large instruction files waste context. Deep knowledge belongs in structured docs.

### Decision 5: Code calculates, LLMs interpret

Reason:

Financial calculations must be deterministic and testable.

### Decision 6: Symphony executes, but does not plan

Reason:

Symphony picks up tickets. It does not create product strategy or dependency-aware roadmaps.

### Decision 7: Add Planning Engine

Reason:

A product design must be converted into epics, tickets, dependencies, and acceptance criteria.

### Decision 8: Add Project Manager Engine

Reason:

The Linear board needs autonomous coordination so Symphony always has correct ready work.

### Decision 9: Add Tech Debt Steward

Reason:

Agent-built systems can accumulate debt quickly. Maintenance must be scheduled automatically.

### Decision 10: No live trading in v1

Reason:

Real-money execution requires proven performance, risk controls, auditability, and human approval.

---

## 30. The Big Picture

Atlas is really two products evolving together.

### Product One: Investment Research Platform

This generates wealth intelligence.

```text
Market data
    ↓
Agent research
    ↓
Scoring
    ↓
Recommendations
    ↓
Backtesting
    ↓
Dashboard/newsletter
```

### Product Two: Autonomous Software Delivery System

This builds Atlas.

```text
Vision
    ↓
Harness docs
    ↓
Planning engine
    ↓
Project manager engine
    ↓
Linear
    ↓
Symphony
    ↓
Codex workers
    ↓
PRs
```

Together, these create a powerful compounding system.

Atlas researches markets.

The build system improves Atlas.

Atlas generates insights.

The PM engine keeps development moving.

The Tech Debt Steward keeps the codebase healthy.

The Planning Engine turns new ideas into executable work.

Symphony converts ready tickets into pull requests.

The human guides strategy.

---

## 31. Final Master Principle

The master principle is:

> Do not try to make AI magically build or trade. Build the operating environment where AI agents can repeatedly perform narrow, well-defined, reviewable work.

For investing, that means:

```text
data → analysis → recommendation → tracking → learning
```

For software delivery, that means:

```text
vision → docs → roadmap → tickets → dependencies → Symphony → PRs
```

Atlas succeeds if both loops become reliable.

The first loop compounds investment intelligence.

The second loop compounds software capability.

That is the real opportunity.

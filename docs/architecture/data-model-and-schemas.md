# Atlas Data Model and Schemas

## Purpose

This document defines the canonical data models and schemas for Atlas.

Atlas is a stateful organisational operating system. Its core assets are not prompts or agents; its core assets are structured knowledge, dependencies, evidence, decisions, delivery state, and learning records.

This document defines the initial schema blueprint for:

- Pydantic models
- PostgreSQL tables
- Dependency graph nodes and edges
- Evidence records
- Context packs
- ADRs
- Tickets
- Epics
- Lessons
- Agent runs

---

# 1. Schema Principles

The code blocks in this document are contractual for field names, string
values, requiredness, and defaults; declaration idioms (enum base class,
union syntax) follow repository lint conventions. Completion of bare
generic parameters (e.g. `dict` → `dict[str, Any]`) likewise follows
repository lint conventions; JSON-object keys are strings, so the
parameterisation matches the storage contract.

## 1.1 Models Are Replaceable, Data Is Permanent

Atlas should treat AI models as interchangeable execution providers.

Persistent data must therefore remain model-agnostic.

Do not store knowledge only inside prompts.

Do not rely on a model remembering previous work.

Persist every important decision, dependency, result, and lesson.

---

## 1.2 Evidence Over Claims

Agent claims are not sufficient.

A ticket is not complete because an agent says it is complete.

A plan is not valid because an agent says it is valid.

Atlas requires structured evidence.

---

## 1.3 Every Important Object Has Identity

All core objects should have stable IDs.

Examples:

- product_id
- epic_id
- ticket_id
- adr_id
- evidence_id
- lesson_id
- agent_run_id

Stable IDs allow Atlas to link knowledge over time.

---

## 1.4 Time Must Be First-Class

Most entities require:

- created_at
- updated_at
- completed_at where relevant
- archived_at where relevant

Atlas needs historical state, not only current state.

---

## 1.5 Human and Agent Attribution

Atlas should know who or what created a record.

Use:

```text
created_by_type: human | agent | system
created_by_id: string
```

This allows future auditability.

---

# 2. Shared Types

## 2.1 UUID

Recommended ID format:

```python
UUID
```

For external-facing ticket keys, Atlas may also maintain human-readable keys:

```text
ATLAS-1
ATLAS-42
```

---

## 2.2 Actor Type

```python
from enum import Enum

class ActorType(str, Enum):
    HUMAN = "human"
    AGENT = "agent"
    SYSTEM = "system"
```

---

## 2.3 Entity Status

```python
class EntityStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"
    DEPRECATED = "deprecated"
```

---

## 2.4 Risk Level

```python
class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
```

---

## 2.5 Evidence Status

```python
class EvidenceStatus(str, Enum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    NOT_APPLICABLE = "not_applicable"
```

---

# 3. Core Platform Models

---

# 3.1 Product

A product is a software product, platform, or internal system managed by Atlas.

Atlas itself is a product.

## Pydantic Model

```python
from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime
from typing import Optional

class Product(BaseModel):
    id: UUID
    key: str
    name: str
    description: str
    vision: str
    status: EntityStatus
    goals: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    created_by_type: ActorType
    created_by_id: str
    created_at: datetime
    updated_at: datetime
    archived_at: Optional[datetime] = None
```

## PostgreSQL Table

```sql
CREATE TABLE products (
    id UUID PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    vision TEXT NOT NULL,
    status TEXT NOT NULL,
    goals JSONB NOT NULL DEFAULT '[]',
    non_goals JSONB NOT NULL DEFAULT '[]',
    constraints JSONB NOT NULL DEFAULT '[]',
    created_by_type TEXT NOT NULL,
    created_by_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    archived_at TIMESTAMPTZ
);
```

---

# 3.2 Architecture Decision Record

ADRs capture architectural decisions and prevent agents from rediscovering or contradicting previous choices.

## Pydantic Model

```python
class ADRStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"

class ArchitectureDecisionRecord(BaseModel):
    id: UUID
    product_id: UUID
    number: int
    title: str
    status: ADRStatus
    context: str
    decision: str
    rationale: str
    consequences: list[str] = Field(default_factory=list)
    alternatives_considered: list[str] = Field(default_factory=list)
    supersedes_adr_id: Optional[UUID] = None
    created_by_type: ActorType
    created_by_id: str
    created_at: datetime
    updated_at: datetime
```

## PostgreSQL Table

```sql
CREATE TABLE architecture_decision_records (
    id UUID PRIMARY KEY,
    product_id UUID NOT NULL REFERENCES products(id),
    number INTEGER NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    context TEXT NOT NULL,
    decision TEXT NOT NULL,
    rationale TEXT NOT NULL,
    consequences JSONB NOT NULL DEFAULT '[]',
    alternatives_considered JSONB NOT NULL DEFAULT '[]',
    supersedes_adr_id UUID REFERENCES architecture_decision_records(id),
    created_by_type TEXT NOT NULL,
    created_by_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE(product_id, number)
);
```

---

# 3.3 Epic

An epic groups related work.

## Pydantic Model

```python
class EpicStatus(str, Enum):
    BACKLOG = "backlog"
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    ARCHIVED = "archived"

class Epic(BaseModel):
    id: UUID
    product_id: UUID
    key: str
    title: str
    description: str
    objective: str
    status: EpicStatus
    priority: int
    risk_level: RiskLevel
    created_by_type: ActorType
    created_by_id: str
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
```

## PostgreSQL Table

```sql
CREATE TABLE epics (
    id UUID PRIMARY KEY,
    product_id UUID NOT NULL REFERENCES products(id),
    key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    objective TEXT NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    risk_level TEXT NOT NULL,
    created_by_type TEXT NOT NULL,
    created_by_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ
);
```

---

# 3.4 Ticket

A ticket is the atomic unit of agent-executable work.

Tickets must be small, scoped, dependency-aware, and verifiable.

## Pydantic Model

```python
class TicketStatus(str, Enum):
    BACKLOG = "backlog"
    PLANNED = "planned"
    BLOCKED = "blocked"
    READY_FOR_AGENT = "ready_for_agent"
    IN_PROGRESS = "in_progress"
    PR_OPEN = "pr_open"
    REVIEW_REQUIRED = "review_required"
    CHANGES_REQUESTED = "changes_requested"
    DONE = "done"
    REJECTED = "rejected"
    NEEDS_HUMAN_DECISION = "needs_human_decision"

class TicketType(str, Enum):
    FEATURE = "feature"
    BUG = "bug"
    TECH_DEBT = "tech_debt"
    SPIKE = "spike"
    DOCUMENTATION = "documentation"
    INFRASTRUCTURE = "infrastructure"
    RESEARCH = "research"

class Ticket(BaseModel):
    id: UUID
    product_id: UUID
    epic_id: Optional[UUID] = None
    key: str
    title: str
    objective: str
    context: str
    status: TicketStatus
    ticket_type: TicketType
    risk_level: RiskLevel
    priority: int
    relevant_docs: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    implementation_notes: list[str] = Field(default_factory=list)
    test_requirements: list[str] = Field(default_factory=list)
    documentation_requirements: list[str] = Field(default_factory=list)
    definition_of_done: list[str] = Field(default_factory=list)
    estimated_effort: Optional[int] = None  # populated from Phase 3 (critical path)
    external_linear_id: Optional[str] = None
    external_github_issue_id: Optional[str] = None
    created_by_type: ActorType
    created_by_id: str
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
```

## PostgreSQL Table

```sql
CREATE TABLE tickets (
    id UUID PRIMARY KEY,
    product_id UUID NOT NULL REFERENCES products(id),
    epic_id UUID REFERENCES epics(id),
    key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    objective TEXT NOT NULL,
    context TEXT NOT NULL,
    status TEXT NOT NULL,
    ticket_type TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    relevant_docs JSONB NOT NULL DEFAULT '[]',
    acceptance_criteria JSONB NOT NULL DEFAULT '[]',
    non_goals JSONB NOT NULL DEFAULT '[]',
    implementation_notes JSONB NOT NULL DEFAULT '[]',
    test_requirements JSONB NOT NULL DEFAULT '[]',
    documentation_requirements JSONB NOT NULL DEFAULT '[]',
    definition_of_done JSONB NOT NULL DEFAULT '[]',
    estimated_effort INTEGER,
    external_linear_id TEXT,
    external_github_issue_id TEXT,
    created_by_type TEXT NOT NULL,
    created_by_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ
);
```

---

# 3.5 Ticket Dependency

A dependency records that one ticket cannot proceed until another ticket, ADR, or component exists.

## Pydantic Model

```python
class DependencyType(str, Enum):
    # depends_on is the single stored direction; "blocks" is derived at
    # query time, never stored, to prevent contradictory inverse edges.
    DEPENDS_ON = "depends_on"
    RELATES_TO = "relates_to"
    IMPLEMENTS = "implements"
    SUPERSEDES = "supersedes"

class TicketDependency(BaseModel):
    id: UUID
    source_ticket_id: UUID
    target_entity_type: str
    target_entity_id: UUID
    dependency_type: DependencyType
    reason: str
    created_at: datetime
```

## PostgreSQL Table

```sql
CREATE TABLE ticket_dependencies (
    id UUID PRIMARY KEY,
    source_ticket_id UUID NOT NULL REFERENCES tickets(id),
    target_entity_type TEXT NOT NULL,
    target_entity_id UUID NOT NULL,
    dependency_type TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
```

## Example

```yaml
source_ticket: ATLAS-25
dependency_type: depends_on
target_ticket: ATLAS-14
reason: Ticket generation requires the Ticket model to exist first.
```

---

# 3.6 Lesson

A lesson records organisational learning.

Lessons may come from successful work, failed work, incidents, recurring blockers, or human decisions.

## Pydantic Model

```python
class LessonCategory(str, Enum):
    SUCCESS_PATTERN = "success_pattern"
    FAILURE_PATTERN = "failure_pattern"
    ARCHITECTURE = "architecture"
    TESTING = "testing"
    DELIVERY = "delivery"
    PRODUCT = "product"
    RESEARCH = "research"
    TECH_DEBT = "tech_debt"

class Lesson(BaseModel):
    id: UUID
    product_id: UUID
    # Agent-authored lessons default to DRAFT; only ACTIVE lessons are
    # retrievable into context packs (ADR-0009).
    status: EntityStatus = EntityStatus.DRAFT
    category: LessonCategory
    title: str
    problem: str
    solution: str
    outcome: str
    confidence: float
    related_ticket_ids: list[UUID] = Field(default_factory=list)
    related_adr_ids: list[UUID] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    created_by_type: ActorType
    created_by_id: str
    created_at: datetime
    updated_at: datetime
```

## PostgreSQL Table

```sql
CREATE TABLE lessons (
    id UUID PRIMARY KEY,
    product_id UUID NOT NULL REFERENCES products(id),
    status TEXT NOT NULL DEFAULT 'draft',
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    problem TEXT NOT NULL,
    solution TEXT NOT NULL,
    outcome TEXT NOT NULL,
    confidence NUMERIC(4,3) NOT NULL,
    related_ticket_ids JSONB NOT NULL DEFAULT '[]',
    related_adr_ids JSONB NOT NULL DEFAULT '[]',
    tags JSONB NOT NULL DEFAULT '[]',
    created_by_type TEXT NOT NULL,
    created_by_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
```

---

# 3.7 Evidence

Evidence records proof of completion, failure, warning, or validation.

## Pydantic Model

```python
class EvidenceType(str, Enum):
    TEST_RESULT = "test_result"
    BUILD_RESULT = "build_result"
    LINT_RESULT = "lint_result"
    COVERAGE_REPORT = "coverage_report"
    SCREENSHOT = "screenshot"
    PR_REVIEW = "pr_review"
    DEPLOYMENT_RESULT = "deployment_result"
    DOCUMENTATION_UPDATE = "documentation_update"
    MANUAL_APPROVAL = "manual_approval"

class Evidence(BaseModel):
    # Append-only. Trust tiers per ADR-0008: created_by_type system|human
    # may carry any status; agent-created evidence is capped at PENDING
    # until corroborated by a system-tier record or human approval.
    id: UUID
    product_id: UUID
    ticket_id: Optional[UUID] = None
    agent_run_id: Optional[UUID] = None
    evidence_type: EvidenceType
    status: EvidenceStatus
    summary: str
    commit_sha: Optional[str] = None      # required for system-tier CI evidence
    external_run_id: Optional[str] = None # CI workflow / check run ID
    payload_hash: Optional[str] = None    # SHA-256 of raw payload at ingestion
    source_uri: Optional[str] = None
    raw_payload: dict = Field(default_factory=dict)
    created_by_type: ActorType
    created_by_id: str
    created_at: datetime
```

## PostgreSQL Table

```sql
CREATE TABLE evidence (
    id UUID PRIMARY KEY,
    product_id UUID NOT NULL REFERENCES products(id),
    ticket_id UUID REFERENCES tickets(id),
    agent_run_id UUID, -- deliberately no FK: Phase 8 reconstructs agent runs from observation, so evidence may precede its run row
    evidence_type TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    commit_sha TEXT,
    external_run_id TEXT,
    payload_hash TEXT,
    source_uri TEXT,
    raw_payload JSONB NOT NULL DEFAULT '{}',
    created_by_type TEXT NOT NULL,
    created_by_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
```

---

# 3.8 Agent Run

An agent run records a discrete execution by an AI agent or orchestration system.

## Pydantic Model

```python
class AgentProvider(str, Enum):
    OPENAI = "openai"
    SYMPHONY = "symphony"
    CODEX = "codex"
    CLAUDE = "claude"
    LOCAL = "local"
    HUMAN = "human"

class AgentRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NEEDS_HUMAN = "needs_human"

class AgentRun(BaseModel):
    id: UUID
    product_id: UUID
    ticket_id: Optional[UUID] = None
    provider: AgentProvider
    model: Optional[str] = None
    status: AgentRunStatus
    objective: str
    input_context_pack_id: Optional[UUID] = None
    output_summary: Optional[str] = None
    error_summary: Optional[str] = None
    cost_estimate_usd: Optional[float] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
```

## PostgreSQL Table

```sql
CREATE TABLE agent_runs (
    id UUID PRIMARY KEY,
    product_id UUID NOT NULL REFERENCES products(id),
    ticket_id UUID REFERENCES tickets(id),
    provider TEXT NOT NULL,
    model TEXT,
    status TEXT NOT NULL,
    objective TEXT NOT NULL,
    input_context_pack_id UUID,
    output_summary TEXT,
    error_summary TEXT,
    cost_estimate_usd NUMERIC(12,4),
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL
);
```

---

# 3.9 Context Pack

A context pack is the compact execution brief given to an agent.

It is the Atlas equivalent of Harness-1's budget-aware rendered context.

## Pydantic Model

```python
class ContextPack(BaseModel):
    id: UUID
    product_id: UUID
    ticket_id: Optional[UUID] = None
    title: str
    objective: str
    constraints: list[str] = Field(default_factory=list)
    relevant_docs: list[str] = Field(default_factory=list)
    relevant_adrs: list[UUID] = Field(default_factory=list)
    related_tickets: list[UUID] = Field(default_factory=list)
    historical_lessons: list[UUID] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    test_commands: list[str] = Field(default_factory=list)
    definition_of_done: list[str] = Field(default_factory=list)
    rendered_markdown: str
    input_doc_shas: dict[str, str] = Field(default_factory=dict)  # staleness detection
    token_estimate: Optional[int] = None
    created_at: datetime
```

## PostgreSQL Table

```sql
CREATE TABLE context_packs (
    id UUID PRIMARY KEY,
    product_id UUID NOT NULL REFERENCES products(id),
    ticket_id UUID REFERENCES tickets(id),
    title TEXT NOT NULL,
    objective TEXT NOT NULL,
    constraints JSONB NOT NULL DEFAULT '[]',
    relevant_docs JSONB NOT NULL DEFAULT '[]',
    relevant_adrs JSONB NOT NULL DEFAULT '[]',
    related_tickets JSONB NOT NULL DEFAULT '[]',
    historical_lessons JSONB NOT NULL DEFAULT '[]',
    acceptance_criteria JSONB NOT NULL DEFAULT '[]',
    risks JSONB NOT NULL DEFAULT '[]',
    test_commands JSONB NOT NULL DEFAULT '[]',
    definition_of_done JSONB NOT NULL DEFAULT '[]',
    rendered_markdown TEXT NOT NULL,
    input_doc_shas JSONB NOT NULL DEFAULT '{}',
    token_estimate INTEGER,
    created_at TIMESTAMPTZ NOT NULL
);
```

---

# 3.10 Plan Run

A plan run records one execution of the Planning Engine (ADR-0007). Rows are inserted at `proposed` and finalised exactly once to `applied`, `rejected`, or `failed`, setting only `approved_by`, `applied_at`, and `failure_reason`; all other fields are immutable after insert, and rows are never deleted.

## Pydantic Model

```python
class PlanRunStatus(str, Enum):
    PROPOSED = "proposed"
    APPLIED = "applied"
    REJECTED = "rejected"
    FAILED = "failed"

class PlanRun(BaseModel):
    id: UUID
    product_id: UUID
    status: PlanRunStatus
    input_doc_shas: dict[str, str]
    model_provider: str
    model_name: str
    prompt_version: str
    similarity_threshold: float
    raw_output_hash: str
    diff_summary: dict = Field(default_factory=dict)
    failure_reason: Optional[str] = None
    approved_by: Optional[str] = None
    created_at: datetime
    applied_at: Optional[datetime] = None
```

## PostgreSQL Table

```sql
CREATE TABLE plan_runs (
    id UUID PRIMARY KEY,
    product_id UUID NOT NULL REFERENCES products(id),
    status TEXT NOT NULL,
    input_doc_shas JSONB NOT NULL DEFAULT '{}',
    model_provider TEXT NOT NULL,
    model_name TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    similarity_threshold NUMERIC(4,3) NOT NULL,
    raw_output_hash TEXT NOT NULL,
    diff_summary JSONB NOT NULL DEFAULT '{}',
    failure_reason TEXT,
    approved_by TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    applied_at TIMESTAMPTZ
);
```

---

# 4. Dependency Graph Schema

Atlas should maintain a graph abstraction over the relational tables.

This can initially be generated from PostgreSQL into NetworkX.

Later it may be persisted in a graph database.

---

## 4.1 Graph Node

```python
class GraphNodeType(str, Enum):
    PRODUCT = "product"
    EPIC = "epic"
    TICKET = "ticket"
    ADR = "adr"
    COMPONENT = "component"
    LESSON = "lesson"
    EVIDENCE = "evidence"

class GraphNode(BaseModel):
    id: UUID
    node_type: GraphNodeType
    key: str
    title: str
    metadata: dict = Field(default_factory=dict)
```

---

## 4.2 Graph Edge

```python
class GraphEdgeType(str, Enum):
    DEPENDS_ON = "depends_on"
    BLOCKS = "blocks"
    RELATES_TO = "relates_to"
    IMPLEMENTS = "implements"
    EVIDENCES = "evidences"
    LEARNED_FROM = "learned_from"
    SUPERSEDES = "supersedes"

class GraphEdge(BaseModel):
    id: UUID
    source_id: UUID
    target_id: UUID
    edge_type: GraphEdgeType
    reason: str
    metadata: dict = Field(default_factory=dict)
```

---

## 4.3 Readiness Rule

A ticket is ready when:

```text
ticket.status in planned/backlog
AND all depends_on tickets are done
AND no unresolved high-risk blocker exists
AND required ADRs are accepted
AND ticket has acceptance criteria
```

---

## 4.4 Critical Path Rule

Critical path should be calculated over unresolved ticket dependencies.

Useful fields (estimated_effort lives on Ticket and is populated from Phase 3):

```text
estimated_effort
priority
number_of_downstream_dependents
risk_level
```

---

# 5. Verification Schema

Verification is the process of determining whether work is actually complete.

---

## 5.1 Verification Check

```python
class VerificationCheckType(str, Enum):
    ACCEPTANCE_CRITERIA = "acceptance_criteria"
    TESTS = "tests"
    LINT = "lint"
    DOCUMENTATION = "documentation"
    SCOPE = "scope"
    SECURITY = "security"
    HUMAN_APPROVAL = "human_approval"

class VerificationCheck(BaseModel):
    id: UUID
    ticket_id: UUID
    check_type: VerificationCheckType
    status: EvidenceStatus
    summary: str
    required: bool = True
    evidence_ids: list[UUID] = Field(default_factory=list)
    created_at: datetime
    completed_at: Optional[datetime] = None
```

---

## 5.2 PostgreSQL Table

```sql
CREATE TABLE verification_checks (
    id UUID PRIMARY KEY,
    ticket_id UUID NOT NULL REFERENCES tickets(id),
    check_type TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    required BOOLEAN NOT NULL DEFAULT TRUE,
    evidence_ids JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ
);
```

---

# 6. Technical Debt Schema

Technical debt should be structured, tracked, and linked to tickets.

---

## 6.1 Debt Item

```python
class DebtCategory(str, Enum):
    TEST_COVERAGE = "test_coverage"
    DUPLICATION = "duplication"
    LARGE_FILE = "large_file"
    STALE_DOCS = "stale_docs"
    SECURITY = "security"
    PERFORMANCE = "performance"
    ARCHITECTURE = "architecture"
    DEPENDENCY = "dependency"

class DebtItem(BaseModel):
    id: UUID
    product_id: UUID
    category: DebtCategory
    title: str
    description: str
    severity: RiskLevel
    detected_by: str
    source_uri: Optional[str] = None
    remediation_ticket_id: Optional[UUID] = None
    status: EntityStatus
    created_at: datetime
    updated_at: datetime
```

---

## 6.2 PostgreSQL Table

```sql
CREATE TABLE debt_items (
    id UUID PRIMARY KEY,
    product_id UUID NOT NULL REFERENCES products(id),
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    severity TEXT NOT NULL,
    detected_by TEXT NOT NULL,
    source_uri TEXT,
    remediation_ticket_id UUID REFERENCES tickets(id),
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
```

---

# 7. Context Pack JSON Contract

This is the payload Atlas should pass to execution agents.

```json partial
{
  "id": "7f3e9b2a-5c1d-4e8f-a6b4-9d2c8e7f1a30",
  "ticket_id": "c4a8d1f6-2b9e-4d57-8e3a-6f1b0c9d4e72",
  "title": "Implement Dependency Graph v1",
  "objective": "Create the first dependency graph implementation using NetworkX.",
  "constraints": [
    "Do not introduce a graph database in v1.",
    "Use PostgreSQL as source of truth."
  ],
  "relevant_docs": [
    "technical-architecture.md",
    "data-model-and-schemas.md"
  ],
  "relevant_adrs": [
    "ADR-0003"
  ],
  "related_tickets": [
    "ATLAS-31",
    "ATLAS-32"
  ],
  "historical_lessons": [],
  "acceptance_criteria": [
    "Can build graph from tickets and ticket_dependencies.",
    "Can return ready tickets.",
    "Can detect blocked tickets."
  ],
  "risks": [
    "Avoid over-engineering with Neo4j in v1."
  ],
  "test_commands": [
    "pytest tests/dependencies"
  ],
  "definition_of_done": [
    "Tests pass.",
    "Docs updated.",
    "No unrelated refactors."
  ]
}
```

---

# 8. Ticket JSON Contract

The Pydantic models are the single contract; JSON Schemas are generated from them (Phase 1, ATLAS-16) and the example below is illustrative only.

```json partial
{
  "key": "ATLAS-42",
  "title": "Implement Dependency Graph v1",
  "objective": "Create a dependency graph service for ticket sequencing.",
  "context": "Atlas requires dependency-aware project management so that the PM Engine can identify ready and blocked work.",
  "ticket_type": "feature",
  "risk_level": "medium",
  "priority": 10,
  "relevant_docs": [
    "technical-architecture.md"
  ],
  "acceptance_criteria": [
    "Graph can be constructed from tickets.",
    "Blocked tickets can be identified.",
    "Ready tickets can be identified."
  ],
  "non_goals": [
    "Do not integrate Linear in this ticket.",
    "Do not add graph visualisation in this ticket."
  ],
  "test_requirements": [
    "Unit tests for graph construction.",
    "Unit tests for readiness detection."
  ],
  "definition_of_done": [
    "Tests pass.",
    "Documentation updated.",
    "No unrelated code changes."
  ]
}
```

---

# 9. ADR JSON Contract

```json partial
{
  "number": 3,
  "title": "Use PostgreSQL as the initial source of truth",
  "status": "accepted",
  "context": "Atlas needs durable structured storage before introducing graph or vector databases.",
  "decision": "Use PostgreSQL as the initial system of record.",
  "rationale": "PostgreSQL is reliable, familiar, easy to deploy, supports JSONB, and can support the MVP.",
  "consequences": [
    "Graph state will initially be derived from relational tables.",
    "Vector and graph stores can be added later."
  ],
  "alternatives_considered": [
    "Neo4j from day one",
    "SQLite-only local storage",
    "Document database"
  ]
}
```

---

# 10. Lesson JSON Contract

```json partial
{
  "category": "failure_pattern",
  "title": "Oversized tickets reduce agent success rate",
  "problem": "Large multi-part tickets caused agents to make broad, hard-to-review changes.",
  "solution": "Split tickets into narrow, dependency-aware units with explicit non-goals.",
  "outcome": "Agent PRs became easier to review and less likely to fail.",
  "confidence": 0.9,
  "tags": ["planning", "ticketing", "agent-execution"]
}
```

---

# 11. Initial MVP Storage Recommendation

Avoid adding:

```text
Neo4j
Vector database
Kafka
Distributed workers
Complex event sourcing
```

until Atlas has proven its core loop.

---

# 12. MVP Core Loop

The first working loop should be:

```text
Create Product
↓
Create ADRs
↓
Generate Epics
↓
Generate Tickets
↓
Generate Dependencies
↓
Build Dependency Graph
↓
Render Context Pack
↓
Run Agent
↓
Store Evidence
↓
Verify Completion
↓
Capture Lesson
```

This loop proves Atlas.

---

# 13. Future Extensions

Later schema additions:

- Vector embeddings for docs, lessons, and tickets
- Graph database export
- Event sourcing
- Audit log table
- Cost accounting table
- Multi-product portfolio model
- Team and permission model
- Scheduled automation model
- Notification model
- Deployment model

---

# 14. Final Schema Principle

Atlas should never depend on model memory for business-critical knowledge.

Every important fact should live in structured data.

Every important action should produce evidence.

Every important outcome should update organisational memory.

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

Platform constraints (Phase 1 closure): text fields do not carry NUL
(U+0000) or surrogate code points (PostgreSQL TEXT and valid Unicode
forbid them), and datetimes lie within years 1900–9000 (UTC
normalisation overflows near the extremes). These are contract,
documented rather than enforced per-field; revisit and add validators
if property tests or production evidence force enforcement.

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
    priority: int = Field(ge=-2147483648, le=2147483647)  # SQL INTEGER range
    risk_level: RiskLevel
    # Reconciler anchor-match pass; AT-1 traceability — every item
    # traceable to a document anchor.
    source_anchor: str
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
    source_anchor TEXT NOT NULL,
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
    priority: int = Field(ge=-2147483648, le=2147483647)  # SQL INTEGER range
    relevant_docs: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    implementation_notes: list[str] = Field(default_factory=list)
    test_requirements: list[str] = Field(default_factory=list)
    documentation_requirements: list[str] = Field(default_factory=list)
    definition_of_done: list[str] = Field(default_factory=list)
    # Populated from Phase 3 (critical path); SQL INTEGER range.
    estimated_effort: Optional[int] = Field(
        default=None, ge=-2147483648, le=2147483647
    )
    external_linear_id: Optional[str] = None
    external_github_issue_id: Optional[str] = None
    # Free-form retrieval facets (ATLAS-127): planner-populated later
    # (ATLAS-128). The Phase 5 context renderer matches ADRs and lessons to a
    # ticket partly by these; no controlled vocabulary in v1.
    tags: list[str] = Field(default_factory=list)
    component: Optional[str] = None
    # PM-Engine definition cursor (ATLAS-42): updated_at at the last
    # confirmed definition push to Linear; a push happens only while
    # updated_at > linear_synced_at. Written by the sync loop, never bumped
    # by an Atlas definition edit. This is not the board-freshness timestamp;
    # /api/v1/status derives that from PmSyncReceipt.finished_at.
    linear_synced_at: Optional[datetime] = None
    # PM-Engine transition signal (ATLAS-118): the Linear state id observed on
    # the last pull. The out-of-ownership anomaly detector logs one DebtItem
    # per transition into an unmapped state by comparing the fetched id against
    # this; a persisting unmapped state writes no new row. Written only by the
    # sync loop, never bumped by an Atlas definition edit (like linear_synced_at).
    last_observed_linear_state_id: Optional[str] = None
    # PM-Engine dwell clock (ATLAS-119): when the ticket entered its current
    # status. Stamped by apply_linear_status (the sole post-creation status
    # writer) only on a real status change, so it marks the start of the current
    # dwell episode and is the per-episode dedup boundary for DWELL_BREACH.
    # NULL means "unknown entry time" and dwell SKIPS it (never a false breach).
    # Written only by the sync loop, never bumped by an Atlas definition edit.
    status_entered_at: Optional[datetime] = None
    # PM-Engine review-cycling counter (ATLAS-120): the number of
    # changes_requested -> pr_open round trips. Incremented by
    # apply_linear_status (the sole post-creation status writer) only on that
    # transition; at more than three the step-5 review-cycling pass routes the
    # ticket to needs_human_decision via set_state. Monotonic in v1 (no reset on
    # human intervention). Status-coupled, never bumped by an Atlas definition
    # edit.
    review_cycle_count: int = 0
    # Learning-system extraction cursor (ATLAS-106): the last time the
    # learning extractor attempted extraction for this ticket. Never bumped by
    # an Atlas definition edit.
    lesson_extraction_attempted_at: Optional[datetime] = None
    # Reconciler anchor-match pass; AT-1 traceability — every item
    # traceable to a document anchor.
    source_anchor: str
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
    tags JSONB NOT NULL DEFAULT '[]',
    component TEXT,
    linear_synced_at TIMESTAMPTZ,
    last_observed_linear_state_id TEXT,
    status_entered_at TIMESTAMPTZ,
    review_cycle_count INTEGER NOT NULL DEFAULT 0,
    lesson_extraction_attempted_at TIMESTAMPTZ,
    source_anchor TEXT NOT NULL,
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
    created_by_type: ActorType
    created_by_id: str
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
    created_by_type TEXT NOT NULL,
    created_by_id TEXT NOT NULL,
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
Agent-authored extraction drafts leave `confidence` null until the operator
assigns confidence at promotion.

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
    confidence: float | None = Field(default=None, ge=0, le=1)
    source_ticket_id: UUID
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
    confidence NUMERIC(4,3) CHECK (confidence >= 0 AND confidence <= 1),
    source_ticket_id UUID NOT NULL,
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
    PR_MERGED = "pr_merged"

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
    job_name: Optional[str] = None        # CI job/check identity
    source_event_at: Optional[datetime] = None # GitHub lifecycle timestamp
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
    job_name TEXT,
    source_event_at TIMESTAMPTZ,
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
    # SQL INTEGER range.
    prompt_tokens: Optional[int] = Field(default=None, ge=-2147483648, le=2147483647)
    completion_tokens: Optional[int] = Field(
        default=None, ge=-2147483648, le=2147483647
    )
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
    # Ordered compression-ladder rungs that fired while rendering (ATLAS-55).
    compression_applied: list[str] = Field(default_factory=list)
    input_doc_shas: dict[str, str] = Field(default_factory=dict)  # staleness detection
    # SQL INTEGER range.
    token_estimate: Optional[int] = Field(default=None, ge=-2147483648, le=2147483647)
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
    compression_applied JSONB NOT NULL DEFAULT '[]',
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
    # Provenance chain: input_doc_shas (what was read) -> prompt_hash
    # (what was asked) -> raw_output_hash (what came back).
    prompt_hash: str  # SHA-256 of the rendered prompt text
    # Reproducible-by-record: model_name names the model, model_parameters
    # the call settings (temperature, max_tokens). With prompt_hash, a run is
    # reproducible from its record.
    model_parameters: dict = Field(default_factory=dict)
    similarity_threshold: float
    raw_output_hash: str
    # The validated proposal (post-parse, pre-key-assignment): the input
    # `atlas apply` materialises the backlog from, keeping the chain
    # raw_output_hash -> proposal -> renders legible.
    proposal: dict = Field(default_factory=dict)
    # Per-stage generation provenance (ADR-0010; planning-large-corpora §5.3):
    # one record per model call — {stage, prompt_version, prompt_hash,
    # raw_output_hash} — that the composite prompt_hash/prompt_version are
    # derived from. The single-call path is the degenerate one-stage list, so
    # the count truthfully answers "how many stages produced this proposal".
    generation_stages: list[dict] = Field(default_factory=list)
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
    prompt_hash TEXT NOT NULL,
    model_parameters JSONB NOT NULL DEFAULT '{}',
    similarity_threshold NUMERIC(4,3) NOT NULL,
    raw_output_hash TEXT NOT NULL,
    proposal JSONB NOT NULL DEFAULT '{}',
    generation_stages JSONB NOT NULL DEFAULT '[]',
    diff_summary JSONB NOT NULL DEFAULT '{}',
    failure_reason TEXT,
    approved_by TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    applied_at TIMESTAMPTZ
);
```

---

# 3.11 Planning Proposal Contract

A proposal is the structured output of the planner (ADR-0007): the
complete desired backlog state, parsed and validated before the
reconciler runs. This section is the canonical contract. The Pydantic
models implementing it — `Proposal`, `ProposalEpic`, `ProposalTicket`,
`ProposalDependency` — land with ATLAS-23; the planner template renders
their generated JSON Schema at run time. The versioned prompt templates
implement this contract; where a released template's illustrative
example differs from this section, this section wins.

Proposal items carry no `id`, `status`, timestamps, or `created_by`
fields — system-owned fields are assigned at apply (ADR-0007). Where a
proposal field shares its name with a canonical model field
(`priority`, `risk_level`, `acceptance_criteria`, …), it carries the
canonical model's type and constraints.

## Envelope

A proposal is a single JSON object with exactly four keys:

```json model=Proposal
{
  "epics": [],
  "tickets": [],
  "dependencies": [],
  "planner_notes": []
}
```

`planner_notes` is a list of strings recording anything ambiguous,
contradictory, unanchorable, or in conflict with a frozen ticket — an
empty list when there is nothing to report. A proposal is full-state:
it is the complete desired backlog, and an existing item it omits is a
proposal to archive that item.

## ProposalEpic

Required fields: `key` (string or null), `title`, `description`,
`objective`, `priority` (integer), `risk_level`, `source_anchor`.

- `key` is `null` for new epics; an existing epic echoes its exact key.
  Epic keys take the form `ATLAS-E<n>` and are assigned by
  `atlas apply`, never by the model.
- `source_anchor` is `<doc path>#<heading-slug>` and must resolve to a
  real heading at the recorded input SHA.

## ProposalTicket

Required fields: `key` (string or null), `epic_ref`, `title`,
`objective`, `context` (non-empty), `ticket_type`, `risk_level`,
`priority` (integer), `source_anchor`, `relevant_docs`, `tags`,
`component` (string or null), `acceptance_criteria` (1–7 entries),
`non_goals` (≥1), `test_requirements` (≥1), `implementation_notes`,
`documentation_requirements`, `definition_of_done` (≥1).

- `tags` and `component` are the free-form retrieval facets the planner
  populates (ATLAS-128); they carry into the stored Ticket's matching
  ATLAS-127 fields at apply. Like `relevant_docs` and `key`/`epic_ref`,
  they are required with no default — the planner always emits the keys,
  with an empty `tags` list and a `null` `component` when nothing fits.
  No controlled vocabulary in v1.
- `epic_ref` is an echoed epic key or `new_epic:<n>`, where `<n>` is
  the zero-based position of the referenced epic in this proposal's
  `epics` array. `epic_ref` may be `null` only when `ticket_type` is
  `tech_debt` (structure gate).
- `key` and `source_anchor` follow the same rules as ProposalEpic;
  ticket keys take the form `ATLAS-<n>`.

## ProposalDependency

Required fields: `source`, `target`, `dependency_type`, `reason`
(non-empty).

- `source` and `target` reference tickets: an echoed key for existing
  tickets, or `new:<n>` with `<n>` the zero-based position in this
  proposal's `tickets` array. Index bounds are validated at parse time.
- `dependency_type` is `depends_on` only — the inverse is derived,
  never stored (§3.5).
- Milestone 1 limitation: proposal dependencies are ticket-to-ticket
  only. ADR and component targets (§3.5's polymorphic range) enter the
  proposal contract in a later phase.

---

# 3.12 Key Counter

The monotonic key counter is operational state (ADR-0007; design in
knowledge-core.md "Key counter"): keys are assigned only at apply, only
by the environment, and never reused — including across archived keys.
The table holds one row per key prefix, keyed on the prefix itself.
`ATLAS` (tickets) and `ATLAS-E` (epics) are distinct prefixes with
independent counters; the prefix is the product-scoped identity. An
unseen prefix has no row and reads as a high-water mark of `0`; its
first assignment mints `<prefix>-1`.

No-reuse is structural, not caller discipline: `high_water` is a pure
high-water mark decoupled from backlog membership (archiving a ticket
cannot lower it), the primary key on `prefix` makes one authoritative
counter per prefix, and the repository exposes only a monotonic advance
(no setter, no decrement) that participates in the caller-supplied apply
transaction. The advance is the single stateful operation; the
deterministic mapping of `new:<n>`/`new_epic:<n>` placeholders to
assigned keys is a pure function over the diff and the current marks
(ATLAS-25).

This is operational infrastructure, not a document-rendered entity: it
has no canonical Pydantic model and no `docs/generated/` schema. The
repository's public currency is plain integers and an assigned range,
never a row.

## PostgreSQL Table

```sql
CREATE TABLE key_counters (
    prefix TEXT PRIMARY KEY,
    high_water INTEGER NOT NULL DEFAULT 0 CHECK (high_water >= 0)
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

## 5.3 Operator Action Idempotency Key

The idempotency-key table is internal gateway state for governed operator
writes. It reserves a key identity before command invocation so duplicate
submissions serialise at the database boundary. The raw `Idempotency-Key`
header is never stored; `idempotency_key_identity` is a SHA-256 identity for
lookup and uniqueness.

A row without a matching terminal receipt is an explicit in-progress owner.
Recovery and retries must not infer that a missing receipt means a previously
started command is safe to repeat. The table is append-only: no update, no
delete.

It has no canonical Pydantic model and no generated schema. The public receipt
model below is the audit record exposed to callers.

## 5.4 PostgreSQL Table

```sql
CREATE TABLE operator_action_keys (
    idempotency_key_identity TEXT PRIMARY KEY,
    request_fingerprint TEXT NOT NULL,
    receipt_id UUID NOT NULL,
    correlation_id UUID NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    created_by_type TEXT NOT NULL,
    created_by_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
```

---

## 5.5 Operator Action Receipt

An `OperatorActionReceipt` is the append-only terminal audit record for one
governed operator command. It stores server-resolved actor context, action and
target identity, the idempotency key identity, the canonical request
fingerprint, bounded non-secret result metadata and before/after status where a
domain command has a status transition.

Receipts never store raw idempotency keys, token/session/CSRF values, full
request bodies, raw evidence payloads, lesson content or exception traces.
Receipt result codes and before/after states are closed, server-controlled
vocabularies rather than command-authored strings. Result codes are one of
`action_succeeded`, `action_refused`, `stale_state`, `action_failed` or
`action_conflict`; before/after states, where present, are `EntityStatus`
values. Outcome/result pairs are constrained: `succeeded` pairs only with
`action_succeeded`; `refused` with `action_refused` or `stale_state`; `failed`
with `action_failed`; and `conflict` with `action_conflict`. Receipt metadata is
default-deny. The only approved keys are `changed`
(strict boolean), `affected_count` (strict integer `0..1000000`) and
`confidence` (strict finite float `0.0..1.0`); string, object and list values
are never valid metadata. The gateway drops unapproved command-result fields
without inspecting their values. Canonical model validation and every public
repository writer reject unapproved fields and wrongly typed approved fields,
including models constructed without initial validation.

## Pydantic Model

```python
MAX_OPERATOR_ACTION_METADATA_BYTES = 4096
OperatorActionMetadataKey = Literal["affected_count", "changed", "confidence"]
OperatorActionMetadataValue = StrictBool | StrictInt | StrictFloat

class OperatorActionOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    REFUSED = "refused"
    FAILED = "failed"
    CONFLICT = "conflict"

class OperatorActionResultCode(str, Enum):
    ACTION_SUCCEEDED = "action_succeeded"
    ACTION_REFUSED = "action_refused"
    STALE_STATE = "stale_state"
    ACTION_FAILED = "action_failed"
    ACTION_CONFLICT = "action_conflict"

class OperatorActionReceipt(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    id: UUID
    correlation_id: UUID
    action: str = Field(min_length=1, max_length=128)
    target_type: str = Field(min_length=1, max_length=128)
    target_id: str = Field(min_length=1, max_length=256)
    created_by_type: ActorType
    created_by_id: str = Field(min_length=1, max_length=128)
    idempotency_key_identity: str = Field(min_length=1, max_length=80)
    request_fingerprint: str = Field(min_length=1, max_length=80)
    outcome: OperatorActionOutcome
    result_code: OperatorActionResultCode
    result_metadata: dict[
        OperatorActionMetadataKey, OperatorActionMetadataValue
    ] = Field(default_factory=dict)
    before_status: EntityStatus | None = None
    after_status: EntityStatus | None = None
    created_at: datetime
    completed_at: datetime
```

## 5.6 PostgreSQL Table

```sql
CREATE TABLE operator_action_receipts (
    id UUID PRIMARY KEY,
    correlation_id UUID NOT NULL UNIQUE,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    created_by_type TEXT NOT NULL,
    created_by_id TEXT NOT NULL,
    idempotency_key_identity TEXT NOT NULL UNIQUE REFERENCES operator_action_keys(idempotency_key_identity),
    request_fingerprint TEXT NOT NULL,
    outcome TEXT NOT NULL,
    result_code TEXT NOT NULL,
    result_metadata JSONB NOT NULL DEFAULT '{}',
    before_status TEXT,
    after_status TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT operator_action_receipts_outcome_result_code CHECK (
        (outcome = 'succeeded' AND result_code = 'action_succeeded') OR
        (outcome = 'refused' AND result_code IN ('action_refused', 'stale_state')) OR
        (outcome = 'failed' AND result_code = 'action_failed') OR
        (outcome = 'conflict' AND result_code = 'action_conflict')
    )
);
```

## 5.7 Acceptance Session

An `AcceptanceSession` is the durable, immutable-head history for one PR
acceptance attempt. Creation pins the requested repository and PR, the
canonical close-set, the exact head and live `main` identities, a structured
snapshot of the shared PR integration assessment, the live ticket acceptance
criteria and their canonical SHA-256 fingerprint, the creation-command key
identity and the server-resolved `human/operator` actor. Raw GitHub payloads,
PR title/body text, raw idempotency keys, credentials, evidence payloads and
logs are not stored.

The close-set is unique and sorted by ticket key. Criteria are stored in that
key order and then by their zero-based index in each ticket's current
`acceptance_criteria` list. The fingerprint input is the UTF-8 canonical JSON
array of `{ticket_key, criterion_index, text}` objects: keys sorted, no
insignificant whitespace, Unicode unescaped, then prefixed `sha256:`. Callers
cannot provide criterion text to session creation.

Lifecycle values are `preflight_passed`, `evidence_ready`,
`confirmations_ready`, `verification_passed`, `merge_ready`, `stale`,
`blocked` and `failed`; the last three are terminal. Every row retains bounded
step summaries for `preflight`, `evidence`, `confirmations`, `verification`
and `readiness`. A summary has a `pending`, `complete`, `blocked` or `failed`
state, typed reasons, receipt IDs and its occurrence timestamp. The
top-level typed reason vocabulary distinguishes PR eligibility, exact-head,
base/ref/repository, external-state, close-set, ticket-state, criteria,
session and historical-step failures; presentation never stores or returns a
foreign exception message as a reason.

`stored_merge_ready` and `historical_readiness_reasons` are historical state.
The pure stored projection labels them `historical_only` and always emits
`is_current_merge_authority: false`. A later bounded live-readiness service
must compose that stored projection with new external reads; cached true is
never current merge authority.

The canonical model is a frozen value snapshot. Storage permits only the
narrow lifecycle-summary transition surface. Database triggers reject changes
to `id`, repository/PR, close-set, head/base, initial assessment, criteria,
fingerprints, creation-command identity, actor or `created_at`. There is no
general update or retarget operation. A partial unique index permits at most
one non-terminal row per repository/PR; consequently a moved head starts a new
session only after the old row is terminal, normally `stale`. The creation-key
identity is unique for all time and replays its original result.

## Pydantic Model

```python
class AcceptanceSession(BaseModel):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    id: UUID
    repository_owner: str
    repository_name: str
    pr_number: int
    close_set: tuple[str, ...]
    head_ref: str
    head_sha: str
    head_repository: str
    base_ref: str
    base_sha: str
    base_repository: str
    initial_assessment: AcceptanceAssessmentSnapshot
    criteria_snapshot: tuple[AcceptanceCriterionSnapshot, ...]
    criteria_fingerprint: str
    creation_idempotency_key_identity: str
    created_by_type: ActorType
    created_by_id: str
    lifecycle: AcceptanceSessionLifecycle
    step_summaries: dict[AcceptanceSessionStep, AcceptanceStepSummary]
    blocking_reasons: tuple[AcceptanceSessionBlockingReason, ...] = ()
    stored_merge_ready: bool = False
    historical_readiness_reasons: tuple[
        AcceptanceSessionBlockingReason, ...
    ] = ()
    created_at: datetime
    updated_at: datetime
    staled_at: datetime | None = None
```

## 5.8 PostgreSQL Table

```sql
CREATE TABLE acceptance_sessions (
    id UUID PRIMARY KEY,
    repository_owner TEXT NOT NULL,
    repository_name TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    close_set JSONB NOT NULL,
    head_ref TEXT NOT NULL,
    head_sha TEXT NOT NULL,
    head_repository TEXT NOT NULL,
    base_ref TEXT NOT NULL,
    base_sha TEXT NOT NULL,
    base_repository TEXT NOT NULL,
    initial_assessment JSONB NOT NULL,
    criteria_snapshot JSONB NOT NULL,
    criteria_fingerprint TEXT NOT NULL,
    creation_idempotency_key_identity TEXT NOT NULL UNIQUE,
    created_by_type TEXT NOT NULL,
    created_by_id TEXT NOT NULL,
    lifecycle TEXT NOT NULL,
    step_summaries JSONB NOT NULL,
    blocking_reasons JSONB NOT NULL,
    stored_merge_ready BOOLEAN NOT NULL DEFAULT FALSE,
    historical_readiness_reasons JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    staled_at TIMESTAMPTZ,
    CONSTRAINT acceptance_sessions_operator_actor CHECK (
        created_by_type = 'human' AND created_by_id = 'operator'
    ),
    CONSTRAINT acceptance_sessions_stale_timestamp CHECK (
        (lifecycle = 'stale' AND staled_at IS NOT NULL) OR
        (lifecycle <> 'stale' AND staled_at IS NULL)
    )
);

CREATE UNIQUE INDEX uq_acceptance_sessions_non_terminal_pr
    ON acceptance_sessions (repository_owner, repository_name, pr_number)
    WHERE lifecycle NOT IN ('stale', 'blocked', 'failed');
```

---

# 6. Delivery-Anomaly Schema

The PM Engine records delivery anomalies as it reconciles Atlas and
Linear (pm-engine-and-linear-sync.md "Anomaly and dwell detection"). A
`DebtItem` is an append-only operational record (ADR-0006 §2), **one row
per observation** — not a mutable tracked item. It is written by the
system from deterministic observation (`created_by_type = system`), so it
carries no trust tier and no PENDING cap; it is NOT evidence (ADR-0008).
Recording a `DebtItem` never changes ticket state. Recurrence and
severity are derived by query — never stored, never a creation gate.

ADR-0011 records why this name now denotes the delivery anomaly and how
the prior code-quality technical-debt register is deferred.

This section also contains PM sync receipts. They are operational records
written by the same reconciliation boundary, but they are tick-scoped rather
than ticket-scoped and they record provenance for successful and unsuccessful
board observations.

---

## 6.1 Debt Item

```python
class AnomalyType(str, Enum):
    OUT_OF_OWNERSHIP_TRANSITION = "out_of_ownership_transition"
    REVIEW_CYCLE = "review_cycle"
    DWELL_BREACH = "dwell_breach"
    STALE_BLOCK = "stale_block"
    PACK_RENDER_FAILURE = "pack_render_failure"

class DebtItem(BaseModel):
    id: UUID
    product_id: UUID
    ticket_id: UUID
    anomaly_type: AnomalyType
    summary: str
    observed_at: datetime
    created_by_type: ActorType
    created_by_id: str
    created_at: datetime
```

`observed_at` is when the anomaly occurred; `created_at` is when the row
was written. There is no `updated_at` and no `status`: the row is never
mutated (append-only enforcement lives in `DebtItemRepo`, not the model).
Unlike `evidence.agent_run_id`, `ticket_id` is required and FK-backed — an
anomaly is always observed against an existing synced ticket.

---

## 6.2 PostgreSQL Table

```sql
CREATE TABLE debt_items (
    id UUID PRIMARY KEY,
    product_id UUID NOT NULL REFERENCES products(id),
    ticket_id UUID NOT NULL REFERENCES tickets(id),
    anomaly_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    created_by_type TEXT NOT NULL,
    created_by_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
```

---

## 6.3 Tick Failure

A `TickFailure` is the second operational record the PM Engine appends, a
sibling of `DebtItem`: a durable, append-only record (ADR-0006 §2) of one
PM-scheduler tick that crashed, written by the system from deterministic
observation (`created_by_type = system`), so it carries no trust tier and no
PENDING cap — it is NOT evidence (ADR-0008). A tick crash is observed at the
**tick level, not against any one ticket**, so it has no `ticket_id` and no
FK target — the very reason it is a separate model and cannot be a `DebtItem`
(whose `ticket_id` is required and FK-backed). It is the record half of
create-on-crash; the scheduler that catches a crashing tick, records one row,
and continues is the sole writer (ATLAS-50).

```python
class TickFailure(BaseModel):
    id: UUID
    occurred_at: datetime
    failure_signature: str
    detail: str
    created_by_type: ActorType
    created_by_id: str
```

`occurred_at` is when the tick crashed (create-on-crash records at the crash
instant), and is the field the query-time dedup predicate
(`TickFailureRepo.recorded_since`) compares against. `failure_signature` is the
free-form grouping key that predicate dedups on (no enum — a crash has no fixed
taxonomy); `detail` is the error text. There is no `updated_at` and no `status`:
the row is never mutated (append-only enforcement lives in `TickFailureRepo`,
not the model). Dedup is a pure query-time predicate over these rows — never
stored, never a creation gate; the dedup window (`since`) is supplied by the
caller (ATLAS-50), not pinned here.

---

## 6.4 PostgreSQL Table

```sql
CREATE TABLE tick_failures (
    id UUID PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL,
    failure_signature TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_by_type TEXT NOT NULL,
    created_by_id TEXT NOT NULL
);
```

---

## 6.5 Ticket Status Transition

A `TicketStatusTransition` is the third operational record the PM Engine
appends, a sibling of `DebtItem` and `TickFailure`: a durable, append-only
record (ADR-0006 §2) of ONE real status transition — from/to status and the
instant — written by the system from deterministic observation
(`created_by_type = system`), so it carries no trust tier and no PENDING cap —
it is NOT evidence (ADR-0008). Unlike `TickFailure` (tick-level, no FK target)
and like `DebtItem`, it is **ticket-scoped**: every transition belongs to an
existing synced ticket, so `ticket_id` is required and FK-backed; there is no
`product_id`. The sole writer is `apply_linear_status` (the sole status
writer), which appends one row inline on its own transaction so the transition
commits atomically with the status change — a transition exists iff the status
actually changed (a set-to-same status records nothing).

```python
class TicketStatusTransition(BaseModel):
    id: UUID
    ticket_id: UUID
    from_status: str
    to_status: str
    occurred_at: datetime
    created_by_type: ActorType
    created_by_id: str
```

`from_status` is the status left and `to_status` the status entered; both are
non-null because the writer records only inside the real-change branch, where
the prior status always exists and differs from the new one. `occurred_at` is
the transition instant. There is no `updated_at`, no `created_at`, and no
`status`: the row is never mutated (append-only enforcement lives in
`TicketStatusTransitionRepo`, not the model). This append-only log coexists with
`Ticket.status_entered_at` (ATLAS-119) — the overwritten dwell clock for the
*current* episode — and replaces neither it nor `review_cycle_count`
(ATLAS-120). The single dwell value cannot reconstruct history; this log can, so
historical per-state cycle time becomes computable (the consumer is ATLAS-126).

---

## 6.6 PostgreSQL Table

```sql
CREATE TABLE ticket_status_transitions (
    id UUID PRIMARY KEY,
    ticket_id UUID NOT NULL REFERENCES tickets(id),
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    created_by_type TEXT NOT NULL,
    created_by_id TEXT NOT NULL
);
```

---

## 6.7 PM Sync Receipt

A `PmSyncReceipt` is the fourth operational record the PM Engine appends: a
durable, append-only receipt for one PM sync tick's local completion boundary.
It is written after the tick body has either completed, failed, been cancelled,
or rejected a malformed board pull. The record carries bounded provenance only:
timing, product/project identity, deterministic fingerprints, result
classification and integer counters. It never stores Linear issue bodies,
comments, tokens, credentials or raw payloads.

```python
class PmSyncReceiptResult(str, Enum):
    SUCCESS_DEFINITION_CHANGED = "success_definition_changed"
    SUCCESS_STATUS_ONLY = "success_status_only"
    SUCCESS_ZERO_ACTION = "success_zero_action"
    PARTIAL = "partial"
    MALFORMED_PULL = "malformed_pull"
    CANCELLED = "cancelled"
    FAILED = "failed"

class PmSyncReceipt(BaseModel):
    id: UUID
    product_id: Optional[UUID] = None
    product_key: Optional[str] = None
    linear_project_id: str
    started_at: datetime
    finished_at: datetime
    status_map_fingerprint: str
    fetched_board_fingerprint: str
    fetched_board_issue_count: int = Field(ge=0, le=2147483647)
    result: PmSyncReceiptResult
    counters: dict[str, int]
    error_summary: Optional[str] = None
    created_by_type: ActorType
    created_by_id: str
```

`started_at` is the injected tick clock sampled at entry and `finished_at` is a
second clock sample taken after the tick body completes, or immediately before
an unsuccessful receipt is recorded. The latter is the actual completion
instant used by status projections, never an alias for the start time.
`product_id` and `product_key`
are populated when the tick's product is unambiguous from the board or a
single-product store; otherwise they remain null rather than guessing. The
`linear_project_id` is always the configured Linear project id. The two
fingerprints are SHA-256 hashes over canonical, bounded inputs: the status map
by Linear state id, and fetched board issue ids/identifiers/state metadata.
`counters` stores the bounded `SyncResult` integer counters by name.
`error_summary` is optional bounded diagnostic metadata for unsuccessful
receipts: a sanitized exception type plus a controlled local error code. It
never contains `str(error)`, an HTTP body, a GraphQL payload, a token or other
arbitrary external exception text.

Only `SUCCESS_DEFINITION_CHANGED`, `SUCCESS_STATUS_ONLY` and
`SUCCESS_ZERO_ACTION` are successful receipts. `PARTIAL`, `MALFORMED_PULL`,
`CANCELLED` and `FAILED` remain queryable for diagnosis but never advance the
latest successful sync timestamp. There is no `updated_at` and no mutable
status; append-only enforcement lives in `PmSyncReceiptRepo`.

---

## 6.8 PostgreSQL Table

```sql
CREATE TABLE pm_sync_receipts (
    id UUID PRIMARY KEY,
    product_id UUID REFERENCES products(id),
    product_key TEXT,
    linear_project_id TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL,
    status_map_fingerprint TEXT NOT NULL,
    fetched_board_fingerprint TEXT NOT NULL,
    fetched_board_issue_count INTEGER NOT NULL,
    result TEXT NOT NULL,
    counters JSONB NOT NULL DEFAULT '{}',
    error_summary TEXT,
    created_by_type TEXT NOT NULL,
    created_by_id TEXT NOT NULL
);
```

---

# 7. Context Pack JSON Contract

This is the payload Atlas should pass to execution agents.

```json partial model=ContextPack
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
    "2c8e5f1a-9b4d-4e6c-8a3f-7d2b9c4e6f10"
  ],
  "related_tickets": [
    "5e2d8c4b-7f1a-4b9e-a6c3-1f8d4b7e2a90",
    "8a4f2e6c-3d9b-4c7a-b1e5-6c2f9a3d7b41"
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

```json partial model=Ticket
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

```json partial model=ArchitectureDecisionRecord
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

```json partial model=Lesson
{
  "category": "failure_pattern",
  "title": "Oversized tickets reduce agent success rate",
  "problem": "Large multi-part tickets caused agents to make broad, hard-to-review changes.",
  "solution": "Split tickets into narrow, dependency-aware units with explicit non-goals.",
  "outcome": "Agent PRs became easier to review and less likely to fail.",
  "confidence": null,
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

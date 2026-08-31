# AGENTS.md

## Purpose

This repository contains Atlas, a stateful organisational operating system
for autonomous software delivery, planning, evidence tracking, and knowledge
accumulation.

## How to navigate

`docs/MANIFEST.md` lists every canonical document. Read what your task
needs:

- Strategy and philosophy → `docs/atlas/atlas-master-plan.md`
- Platform overview → `docs/atlas/system-specification.md`
- Engineering architecture → `docs/architecture/technical-architecture.md`
- Models, tables, contracts → `docs/architecture/data-model-and-schemas.md`
- Planning (plan/apply, reconciler, gates) →
  `docs/atlas/planning-engine-specification.md`
- Delivery programme → `docs/atlas/implementation-roadmap.md`
- Operational practice / fresh-agent bootstrap →
  `docs/runbooks/operational-practice.md`
- Operator environment / credentials / runtime facts →
  `docs/runbooks/operator-environment.md`
- Symphony-dispatched agent lifecycle →
  `docs/runbooks/symphony-agent-execution.md`
- Symphony service/runtime operation →
  `docs/runbooks/symphony-runtime-operation.md`
- Symptom-driven diagnosis → `docs/runbooks/troubleshooting.md`
- Review and acceptance → `docs/runbooks/review-doctrine.md`,
  `docs/runbooks/reviewer-session.md`, `docs/runbooks/pr-acceptance.md`
- Governing decisions → `docs/decisions/` (ADR-0001..0011; ADR-0004
  retired, number not reused)
- Day-one setup → `docs/atlas/bootstrap-guide.md`

## Repository Codex skills

Repository skills under `.codex/skills/` are procedural adapters beneath the
canonical documents and deterministic Atlas CLI. They aid navigation and
execution but never override repository authority.

| Task or capability | Procedural skill |
| --- | --- |
| Current-state or repository investigation | `atlas-investigate` |
| Hand-dispatched maintenance execution | `atlas-maintenance-execution` |
| Candidate validation | `atlas-validation` |
| Ratified design or phase decomposition | `atlas-ticket-planning` |
| Operator planning plan/apply | `atlas-planning-apply` |
| Ordinary dispatched ticket implementation | `atlas-ticket-execution` |
| `Changes Requested` remediation | `atlas-ticket-remediation` |
| PR semantic review | `atlas-pr-review` |
| PR acceptance | `atlas-pr-acceptance` |
| Bounded Linear operations | `linear` |

## Codex delegation and write isolation

Subagents parallelise cognition; worktrees parallelise mutation. Use bounded
subagents for independent read-heavy investigation or review when that
materially reduces latency or keeps noisy evidence out of the primary context.
Give each child one question, explicit scope and prohibited actions, and an
exact return contract. The primary agent retains authority, waits for every
requested result, resolves contradictions against canonical Atlas authority,
and synthesises the outcome; child consensus is not authority.

One mutable checkout has one writer. Parallel implementation requires isolated
worktrees with unique branches, exact starting SHAs, declared owned and excluded
paths, dependencies, and one primary writer per worktree. Serialize units whose
mutable path ownership overlaps; do not use optimistic conflict resolution as a
coordination model.

Subagents may not independently mint canonical tickets, mutate Linear, operate
managed services, mutate production databases, merge PRs, or broaden scope.
Canonical documents and the deterministic Atlas CLI remain above skills and
agent instructions in authority. Tier-0 operational work must assess
retrospective recovery and eventual liveness as well as immediate crash safety:
failing closed on one action must not make the subsystem permanently
fail-stopped.

## Rules

- Treat `docs/atlas/atlas-master-plan.md` as the single canonical master
  plan; the MANIFEST resolves conflicts.
- For programme-level investigation, review, diagnosis, minting or live
  milestone work, read `docs/runbooks/operational-practice.md` first. Establish
  exact repository, ticket, PR, database and runtime identities before making
  claims about current state; prior conversations and cached reports are
  working context, not authority.
- Documents are the source of truth for intent; the database for
  operational state; `docs/planning/` files are renders (ADR-0006).
- Never hand-edit `docs/planning/`; only `atlas apply` writes there
  (ADR-0007).
- Never invent ticket keys; keys are assigned by the reconciler on apply.
- Documentation edits are exact-match replacements: if the text an
  instruction quotes does not match the file verbatim, stop and
  report the mismatch — never approximate, never patch the nearest
  similar text.
- Evidence rules per ADR-0008: agent-submitted evidence is PENDING until
  corroborated; no system-tier evidence means no completion.
- Agent-authored lessons are DRAFT until the operator promotes them
  (ADR-0009).
- Do not reference retired v1/v2/v3 document naming in active docs.
- Do not build product features before the Atlas harness foundation exists.
- Do not introduce Linear integration before local planning works.
- Do not introduce Symphony integration before context packs exist.
- Keep work units small and dependency-aware. Update documentation when
  behaviour changes. Before publication, calculate `atlas validation-plan`
  from exact base/head identities, every changed path, every explicit governing
  work-contract validation requirement and every declared test file. The CLI must
  prove that path set against the read-only Git diff and prove explicit test
  files at the head. Run every ordered command and explicit test target. Run a
  complete local sweep only when the named `full-sweep` conservative profile
  is selected or the operator explicitly instructs it; never narrow a selected
  plan or add an unselected sweep as ritual. A selected-check failure prevents
  publication, and any head change makes old-head results historical only.
  Scoped local results are agent-tier confidence only; complete CI at the
  accepted identity remains the system-tier authority and runs unchanged.
- For Symphony-dispatched canonical tickets, after one successful current-main
  rebase, a passing validation plan and one successful candidate publication,
  record exact commands/results, move the ticket through `PR Open` to `CI
  Pending`, and stop in the same turn. Do not
  poll CI or wait for review. `CI Pending` must remain absent from Symphony's
  active states; only the system-tier reconciler owns its exit to `Review
  Required` or `Changes Requested`.
- If a substantial session reveals a durable operating rule, diagnosis
  pattern or environment fact that a fresh agent would otherwise have to
  rediscover, put it in the owning canonical document/runbook or Atlas lesson
  before relying on that knowledge in a later session.
- Gate conduct: a plan gate is a single presentation, not a conversation.
  Present your understanding, the plan, and any working assumptions, then
  stop once. Resolve minor ambiguity by stating an assumption the operator
  can veto at the gate — never by asking a clarifying question, which costs
  a round trip a stated assumption would not. Halt and flag (do not
  interrogate) only for a genuine blocker: conflicting canonical docs,
  ambiguity no reasonable assumption resolves, or a required out-of-scope
  or destructive action.

## First Milestone

A local generative planning loop:

```bash
atlas plan    # LLM proposal -> gates -> reconciled diff
atlas apply   # operator approval -> docs/planning renders + PlanRun
```

Outputs: `docs/planning/epics.yaml`, `tickets.yaml`, `dependencies.yaml`,
`roadmap.mmd`. Acceptance tests AT-1..AT-7 are defined in
`docs/atlas/planning-engine-specification.md`.

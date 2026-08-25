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
- Keep tickets small and dependency-aware. Update documentation when
  behaviour changes. Before publication, calculate `atlas validation-plan`
  from exact base/head identities, every changed path, every explicit ticket
  validation requirement and every ticket-declared test file. The CLI must
  prove that path set against the read-only Git diff and prove explicit test
  files at the head. Run every ordered command and explicit test target. Run a
  complete local sweep only when the named `full-sweep` conservative profile
  is selected or the operator explicitly instructs it; never narrow a selected
  plan or add an unselected sweep as ritual. A selected-check failure prevents
  publication, and any head change makes old-head results historical only.
  Scoped local results are agent-tier confidence only; complete CI at the
  accepted identity remains the system-tier authority and runs unchanged.
- After one successful current-main rebase, a passing validation plan and one
  successful candidate publication, record exact commands/results, move the
  ticket through `PR Open` to `CI Pending`, and stop in the same turn. Do not
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

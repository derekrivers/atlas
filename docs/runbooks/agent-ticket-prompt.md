# Atlas — Agent Ticket Prompt (Claude Code / VS Code)

Reusable prompt for instructing a coding agent to pick up one roadmap
ticket. Replace everything in `{curly braces}`. One ticket per session.

---

## The prompt

```text
Read CLAUDE.md and AGENTS.md in the repository root now, and follow
AGENTS.md for everything in this session.

## Task

Implement {TICKET-KEY} — {one-line ticket title}, from
docs/atlas/implementation-roadmap.md ({Phase N — Phase name}).

## Required reading before writing any code

1. AGENTS.md (rules) and docs/MANIFEST.md (canonical document index)
2. The {TICKET-KEY} entry and its surrounding epic in
   docs/atlas/implementation-roadmap.md, including the phase's
   milestone test
3. The phase design doc: {docs/path/to/phase-design-doc.md}
4. {Any other specific sections, e.g. data-model-and-schemas.md §3.4,
   or the relevant ADRs}

State, in two or three sentences, your understanding of the ticket's
objective and the phase milestone it serves, plus any working assumptions
you are making about ambiguous points. This is a statement, not a question
turn: fold those assumptions into the plan below, where the operator vetoes
or corrects them at the single gate (AGENTS.md, gate conduct). Do not open
an interactive exchange of clarifying questions. Halt and flag only for a
genuine blocker — docs that conflict, or ambiguity no reasonable assumption
resolves; a doc conflict is never yours to resolve, the MANIFEST's conflict
order decides and doc fixes are their own ticket.

## Plan first

Before editing anything, present a short plan: files you will create or
change, the tests you will add, and anything you will explicitly NOT do.
Wait for my approval, then implement. If you are running in a mode that
cannot pause for input, present the plan and end your turn; do not
proceed to implementation in the same turn.

## Scope

- In scope: {what the ticket covers — lift from the roadmap entry}
- Out of scope: {adjacent tickets / later phases — name them, e.g.
  "ATLAS-17 YAML serialisation: do not start it"}
- Do not refactor, rename, or "improve" anything outside the ticket. If
  you find a genuine defect outside scope, report it at the end as a
  proposed follow-up; do not fix it.

## Hard constraints (per AGENTS.md — restated because they are the ones
agents most often break)

- Never write to docs/planning/ (ADR-0007).
- Never invent or renumber ticket keys.
- Update canonical docs in the same change when behaviour diverges from
  them — code and docs land together or not at all.

## Definition of done

- Acceptance criteria: {1–7 concrete, falsifiable criteria}
- All tests pass locally: {command, e.g. `uv run pytest`}
- Lint and type-check pass: {e.g. `uv run ruff check . &&
  uv run ruff format --check . && uv run mypy atlas tests`}
- New behaviour is covered by new tests, including at least one negative
  case
- Docs updated if behaviour changed; no unrelated diffs
- A short completion report: what changed, how each acceptance criterion
  is evidenced (test names / CI output), and any proposed follow-ups

Do not claim completion without the evidence above — an agent saying
"done" is not done (ADR-0008).
```

---

## Filled example

```text
Read CLAUDE.md and AGENTS.md in the repository root now, and follow
AGENTS.md for everything in this session.

## Task

Implement ATLAS-4 — Doc linter v1, from
docs/atlas/implementation-roadmap.md (Phase 0 — Foundation and
Mechanical Trust).

## Required reading before writing any code

1. AGENTS.md and docs/MANIFEST.md
2. The ATLAS-4 entry, the Bootstrap Repository epic, and the Phase 0
   milestone test in docs/atlas/implementation-roadmap.md
3. docs/architecture/data-model-and-schemas.md §3.2 (the ADR model the
   linter validates against)
4. ADR-0006 and ADR-0007 (why docs/planning/ must be machine-written)

State your understanding of the objective in two or three sentences,
with any working assumptions. Present them in the plan; do not ask
clarifying questions (AGENTS.md, gate conduct). Halt only for a genuine
blocker or a doc conflict.

## Plan first

Present a short plan (files, tests, non-goals) and wait for approval.
If you are running in a mode that cannot pause for input, present the
plan and end your turn; do not proceed to implementation in the same
turn.

## Scope

- In scope: a linter that (a) validates docs/decisions/*.md contain
  Status/Context/Decision/Rationale/Consequences/Alternatives sections,
  (b) checks every path listed in docs/MANIFEST.md exists and every
  canonical doc is listed, (c) bans legacy v1/v2/v3 document names
  outside docs/archive/, (d) flags changes to docs/planning/ not made
  by atlas apply
- Out of scope: ATLAS-5 (repairing drift the linter finds — report
  findings only), ATLAS-16 (schema validation of JSON examples), CI
  wiring beyond a single runnable entry point
- No refactors outside the ticket.

## Hard constraints

- Never write to docs/planning/ (ADR-0007).
- Never invent or renumber ticket keys.
- Code and doc updates land together.

## Definition of done

- `uv run python -m atlas.tools.doc_linter` exits 0 on the current repo
- It exits non-zero on each seeded bad fixture: an ADR missing its
  Rationale section, a MANIFEST entry pointing at a missing file, a
  legacy name in an active doc, a hand-edited planning file
- `uv run pytest` passes; linter behaviour covered by tests including
  the negative fixtures
- `uv run ruff check .`, `uv run ruff format --check .`, and
  `uv run mypy atlas tests` pass; `uv run pre-commit run --all-files`
  passes
- Completion report mapping each criterion to its evidence
```

---

## Variant: audit-class tickets (two gates)

Use this variant when the ticket is an audit, review, or drift repair
whose fixes cannot be enumerated in advance — the findings are the
work. Replace the template's "Plan first" section with:

```text
## Plan first — two gates

This ticket is an audit: its repairs cannot be fully listed in
advance, so it runs with two pauses.

Gate 1 (method): present your plan — what will be verified, the
audit method and anchors, any mechanical changes — and stop. If you
are running in a mode that cannot pause for input, present the plan
and end your turn.

Gate 2 (findings): after the read-through but before editing
anything, present the complete findings list, each item with
file:line, a quoted excerpt, and a classification:
  (a) contradiction — content contradicts an accepted ADR or a
      canonical document; repair in scope once approved
  (b) staleness — accurate-but-outdated framing, missing or
      dangling references; repair in scope on approval
  (c) improvement — stylistic or structural suggestions; report
      only, never implement
Stop at gate 2 and wait for approval of the classification before
making any edit. The approval may reclassify items; repair only
what is approved as (a) or (b).
```

Rules of the variant:

- Class (c) items are never implemented in the audit ticket. At close,
  every (c) item must be explicitly assigned — to a named future
  ticket, an owning design doc, or linter v2 — or dropped with a
  one-line rationale. Nothing vague survives.
- Route repairs to the canonical source: mirror it or point at it
  rather than hand-writing parallel content. Maintained copies of
  canonical content are drift on a timer.
- Why two gates: a single plan gate forces an audit agent to guess its
  repairs or proceed unapproved; gate 2 catches misclassification at
  the cost of one reply, before any edit exists to redo. First use:
  ATLAS-6, where gate 2 reclassified one finding.

---

## Variant: specification-gap tickets (single gate, named gaps)

Use the specification-gap variant when a design doc leaves a genuine
design gap the ticket must close. Add to the template, before the
Plan-first section:

```text
## The design gap(s) you must resolve in your plan

{The operator names each gap and constrains the proposal space —
e.g. "propose the mapping mechanism, with the failure mode for an
unmapped fence stated".}

Resolve these BY PROPOSAL AT THE PLAN GATE, never silently mid-read.
Your plan must state, per gap: the chosen convention, its failure
modes, and the exact wording added to the owning canonical doc —
convention and enforcement land in the same change. Default
constraint: prefer explicit declaration over inference — mechanical
trust beats cleverness.
```

Rules of the variant:

- The OPERATOR names the gaps. An agent that discovers an unnamed gap
  mid-read stops and asks — a named-gap licence never extends to
  gaps the prompt did not name.
- The chosen convention's wording lands in the owning canonical doc
  in the same change as its enforcement; a convention without
  enforcement is a documented wish.
- First uses: ATLAS-16 (fence mapping), ATLAS-17 (scalar
  representation), ATLAS-18 (four gaps), ATLAS-19 (three gaps).

---

## Variant: single-gate autonomy

Use this variant when the operator grants execution autonomy.
Replace the template's "Plan first" section with:

```text
## Plan first — single gate, then autonomous execution

Before editing anything, present a short plan: files you will create
or change, the tests you will add, and anything you will explicitly
NOT do. Wait for my approval. If you are running in a mode that
cannot pause for input, present the plan and end your turn.

After the plan is approved, execute to completion WITHOUT further
prompts:
- Run any read, build, test, lint, type-check, or pre-commit command
  freely — no permission requests, no narration before routine
  commands.
- Make all in-scope file edits without per-edit confirmation.
- No "shall I continue", no progress check-ins, no pauses between
  steps.
- Stop and ask ONLY if: (a) the docs conflict or are genuinely
  ambiguous beyond any operator-named gaps; (b) correct
  implementation would require touching files outside the approved
  plan; or (c) an operation would be destructive or irreversible
  outside the repository working tree.
```

Rules of the variant:

- The three stop conditions are the only three. Everything else —
  including failures, lint errors, and forced consequences of the
  approved plan — is the agent's to resolve within it.
- Prompt text cannot grant client-side permissions: the operator's
  tool allowlist must match the autonomy granted, or the session
  degrades into the permission prompts the prompt promised away.
- First use: ATLAS-11; zero mid-execution prompts and zero gate
  violations across ATLAS-11..19.

---

## Notes on use

- One ticket per session. If the agent proposes bundling a second
  ticket, decline and start a fresh session for it.
- The plan-approval gate is the cheapest review you will ever do; never
  skip it. A post-hoc review of a finished tree is weaker than
  pre-approval of a plan, because sunk work biases the review. A plan's
  scope boundaries, contract decisions, and operator rulings are
  binding; layout and implementation details are indicative — an agent
  may improve indicative elements within the approved scope, reporting
  each such change as a judgment call in the completion report.
  (Origin: ATLAS-18's conversion-site centralisation.)
- Gate presentations and completion reports are repo-resident PR
  artefacts: the gate presentation is posted as the opening PR
  description (or first comment when the branch precedes the plan),
  and the completion report is posted as a PR comment at close. A
  remote reviewer works from the PR by number; the operator relays
  verdicts. The conversation pane is a working surface, not the
  record.
- When the agent gets something wrong, the durable fix is usually a
  missing rule or doc clarification — encode it in AGENTS.md or the
  relevant canonical doc and commit, so every future session inherits
  it. That is the harness loop working as designed.
- If the operator's own uncommitted working-tree edits are present when
  execution begins and an in-scope test or pin depends on them (e.g. a
  hand-written roadmap line a count-pin must match), surface them at the
  first gate or in the completion report and land the dependent change
  together with the edit, so the committed tree is internally
  consistent — never silently absorb the edit, and never silently
  exclude it and leave the pin to fail. (Origin: ATLAS-112's roadmap
  line and the 92→93 enumeration pin.)
- Branch from a fresh `origin/main`, never local `HEAD`: `git fetch`
  first, and before every push `git log origin/main..HEAD` must show only
  this ticket's commits (and `git diff --stat origin/main..HEAD` only its
  files). A stray local commit riding the branch silently expands the diff
  past the approved scope and can land unrelated hazards. (Origin: PR #155,
  where an unpushed local commit re-added an active-inbox stub — a
  duplicate-mint hazard — and reopened the PR.)
- A mid-execution scope addition is a NEW GATE, even when the operator
  directs it. Flagging the tension and then complying folds an
  unratified amendment into an approved diff — flag-and-comply is the
  named failure; flag-and-route (a separate gated change) is the rule.
  (Origin: PR #156, where operator-directed script additions rode an
  approved one-bullet diff and reopened the PR.)
- Roadmap keys are illustrative seeds until `atlas apply` exists; once
  the planner is live, paste the rendered ticket (objective, acceptance
  criteria, context pack) in place of the hand-filled sections above.
- Hand-dispatched work never claims a forward key. Either mint first
  (inbox stub -> `atlas apply` -> use the assigned key) or label the PR
  with the non-key meta convention (`ATLAS-00xM`). A number used ahead of
  the counter becomes a permanent namespace smear; the ATLAS-111..146
  burn (debt register: "Key-namespace burn") is the cost of the last one.

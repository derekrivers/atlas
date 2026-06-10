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
objective and the phase milestone it serves. If the ticket is ambiguous
or the docs conflict, stop and ask before implementing — do not resolve
doc conflicts yourself; the MANIFEST's conflict order decides, and doc
fixes are their own ticket.

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
- Lint and type-check pass: {e.g. `uv run ruff check . && uv run mypy .`}
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

State your understanding of the objective in two or three sentences.
If anything is ambiguous or the docs conflict, stop and ask.

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
- `uv run ruff check .` and `uv run mypy .` pass
- Completion report mapping each criterion to its evidence
```

---

## Notes on use

- One ticket per session. If the agent proposes bundling a second
  ticket, decline and start a fresh session for it.
- The plan-approval gate is the cheapest review you will ever do; never
  skip it. A post-hoc review of a finished tree is weaker than
  pre-approval of a plan, because sunk work biases the review.
- When the agent gets something wrong, the durable fix is usually a
  missing rule or doc clarification — encode it in AGENTS.md or the
  relevant canonical doc and commit, so every future session inherits
  it. That is the harness loop working as designed.
- Roadmap keys are illustrative seeds until `atlas apply` exists; once
  the planner is live, paste the rendered ticket (objective, acceptance
  criteria, context pack) in place of the hand-filled sections above.

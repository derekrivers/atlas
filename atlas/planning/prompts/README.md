# Atlas Planner Prompts

This directory holds versioned planner prompt templates. Templates are
artifacts with the same status as code: they are reviewed, versioned, and
referenced by every `PlanRun` via `prompt_version`.

## Versioning rules

- Filename and front matter carry the version: `planner-vMAJOR.MINOR.PATCH`.
- The current release is declared explicitly in `CURRENT` (one line
  naming the version, e.g. `planner-v1.1.0`); releasing a new version
  updates `CURRENT` in the same reviewed change. The renderer never
  infers the current release from the directory listing.
- Never edit a released template in place. Copy to a new version, change,
  review, release. Old versions are retained so any historical `PlanRun`
  can be reproduced exactly.
- PATCH: wording fixes with no behavioural intent. MINOR: guidance or
  output-contract additions that remain backward compatible with the
  reconciler. MAJOR: anything the reconciler or validation gates must
  change to accommodate.

## Rendering contract

- Engine: Jinja2 with `StrictUndefined`. Unknown variables and filters
  fail rendering; a render failure fails the `PlanRun` (never silently
  falls back), matching the strict-template behaviour in the Symphony spec.
- `proposal_json_schema` is generated from the Proposal Pydantic models
  (Proposal, ProposalEpic, ProposalTicket, ProposalDependency; ATLAS-23)
  at render time — the schema in the prompt can never drift from the
  code. The canonical contract is data-model-and-schemas.md §3.11. The
  renderer (ATLAS-22) takes the schema as a caller-supplied parameter,
  validated for presence like any other variable; since ATLAS-23 it is
  generated from the Proposal models (atlas/planning/proposal.py).
- Required variables are listed in each template's front matter; the
  renderer validates presence before calling the model.

## Staged templates (ATLAS-103)

Alongside the single-call `planner-v*` templates there are three staged
templates — projections of the §3.11 proposal contract, one per
generation stage (planning-large-corpora.md §4/§4.1, ADR-0010):

- `planner-stage-epics-v1.0.0.md.j2` — stage 1, emits the epics-only
  slice;
- `planner-stage-tickets-v1.0.0.md.j2` — stage 2, emits one epic's
  tickets (rendered once per epic);
- `planner-stage-dependencies-v1.0.0.md.j2` — stage 3, emits the
  `depends_on` edges.

The orchestration that sequences these calls and assembles their slices
into one full-state proposal is ATLAS-104 (`atlas/planning/staged.py`,
run via `atlas plan --staged`); this directory holds the template
artifacts only.

- **Separate version lineage and naming.** Staged templates are named
  `planner-stage-<stage>-vMAJOR.MINOR.PATCH` and version independently of
  the single-call lineage. The same never-edit-in-place and PATCH/MINOR/
  MAJOR rules apply.
- **The environment owns identity (ADR-0007, §4.1).** Each template
  instructs the model to REFERENCE the indices it is given and never mint
  them: stage 1 does not number its epics (the environment assigns
  `new_epic:<n>` by emission order); stage 2 carries the given
  `epic_ref` and does not number its tickets (the environment assigns
  `new:<n>` by assembled position); stage 3 references the assembled
  `new:<n>` and mints no ticket identity.
- **Projection schema.** Each template embeds a caller-supplied
  `stage_output_schema` — the per-stage projection of §3.11, a distinct
  object from the full-envelope `proposal_json_schema`. ATLAS-104 derives
  these schemas from the §3.11 field models (`StageEpicsOutput` wraps
  `ProposalEpic`, and so on), so they cannot drift from the contract; the
  renderer takes the schema as a variable (the same D2 seam as the
  single-call template).
- **CURRENT is unchanged.** `CURRENT` still names the live single-call
  release (`planner-v1.1.0`); the staged set is selected only by explicit
  `version=`. The renderer's CURRENT version pattern structurally rejects
  staged names, so they can never repoint the live release. A staged
  "current" pointer, if ever needed, is ATLAS-104's decision, not this
  ticket's.

## Evaluation

Acceptance tests AT-1..AT-7 in the Planning Engine Specification run
against a pinned prompt version. A new prompt version is releasable only
when it passes the full suite, including AT-7 (≥90% anchor-match coverage
of the hand-written reference roadmap) and AT-2 (empty reconciled diff on
unchanged docs across three consecutive runs, to surface nondeterministic
rewording the temperature setting does not fully suppress). The
three-consecutive-empty-diff criterion is deliberately stricter than
AT-2's acceptance wording (a single stable re-run). Release gating
applies from ATLAS-29 onward; versions released before the AT suite
exists (v1.0.0, v1.1.0) are bootstrap releases.

## v1.1.0

Four changes from v1.0.0 (pre-Phase-2 contract session): rule 4 gains
the `definition_of_done` (≥1) requirement; the output-shape ticket
example gains `definition_of_done`; the vestigial `"tickets": null` is
dropped from the epic example (grouping flows through `epic_ref`); the
version is bumped in filename and front matter. MINOR — no reconciler
exists to be incompatible with.

## Known sensitivities (v1.0.0)

- Rule 8 (verbatim re-emission) is the main defence for AT-2. If AT-2
  flakes, tighten this rule before touching the similarity threshold.
- `new:<n>` index references are positional; the parser must validate
  index bounds before the reconciler runs.
- Frozen-ticket echo depends on the frozen list being rendered correctly;
  the renderer must fail if `frozen_ticket_keys` is missing rather than
  rendering an empty section.

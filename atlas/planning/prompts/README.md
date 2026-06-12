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
  validated for presence like any other variable; generation belongs to
  the Proposal models (ATLAS-23).
- Required variables are listed in each template's front matter; the
  renderer validates presence before calling the model.

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

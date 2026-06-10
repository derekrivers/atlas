# Atlas Planner Prompts

This directory holds versioned planner prompt templates. Templates are
artifacts with the same status as code: they are reviewed, versioned, and
referenced by every `PlanRun` via `prompt_version`.

## Versioning rules

- Filename and front matter carry the version: `planner-vMAJOR.MINOR.PATCH`.
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
- `proposal_json_schema` is generated from the Pydantic models at render
  time — the schema in the prompt can never drift from the code.
- Required variables are listed in each template's front matter; the
  renderer validates presence before calling the model.

## Evaluation

Acceptance tests AT-1..AT-7 in the Planning Engine Specification run
against a pinned prompt version. A new prompt version is releasable only
when it passes the full suite, including AT-7 (≥90% anchor-match coverage
of the hand-written reference roadmap) and AT-2 (empty reconciled diff on
unchanged docs across three consecutive runs, to surface nondeterministic
rewording the temperature setting does not fully suppress).

## Known sensitivities (v1.0.0)

- Rule 8 (verbatim re-emission) is the main defence for AT-2. If AT-2
  flakes, tighten this rule before touching the similarity threshold.
- `new:<n>` index references are positional; the parser must validate
  index bounds before the reconciler runs.
- Frozen-ticket echo depends on the frozen list being rendered correctly;
  the renderer must fail if `frozen_ticket_keys` is missing rather than
  rendering an empty section.

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

- `planner-stage-epics-v1.1.0.md.j2` — stage 1, emits the epics-only
  slice (v1.0.0 retained for historical `PlanRun` reproduction);
- `planner-stage-tickets-v1.3.0.md.j2` — stage 2, emits one epic's
  tickets (rendered once per epic); the live staged tickets template
  (v1.0.0/v1.1.0/v1.2.0 are retained for historical `PlanRun` reproduction);
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
- **CURRENT names a single-call release.** `CURRENT` names the live
  single-call release (`planner-v1.2.0` since ATLAS-111; was `planner-v1.1.0`);
  the staged set is selected only by explicit `version=`. The renderer's
  CURRENT version pattern structurally rejects staged names, so they can never
  repoint the live release. A staged "current" pointer, if ever needed, is
  ATLAS-104's decision, not this ticket's.

### Staged tickets v1.1.0 (ATLAS-109)

`planner-stage-tickets-v1.1.0.md.j2` is the live staged tickets template.
It adds one declared variable, `correction`, and an additive
`{% if correction %}` block; every v1.0.0 instruction is unchanged and the
§3.11 bounds are enforced, never relaxed. MINOR — the projection schema and
the assembled-proposal contract are unchanged, so the reconciler and gates
are unaffected.

The orchestrator (`atlas/planning/staged.py`) supplies `correction` on every
render: `None` on the first attempt (the block renders empty), and a directed
correction string on a retry. When a tickets stage emits valid JSON that
grazes a §3.11 field bound (the measured case: 8 `acceptance_criteria` against
the ≤7 cap), the orchestrator re-renders this template with a correction
naming exactly what was violated and re-calls, up to `MAX_STAGE_ATTEMPTS`
total attempts, then fails honestly. Truncation and non-JSON output do NOT
retry. v1.0.0 is retained unchanged so any historical `PlanRun` pinned to it
reproduces exactly; only v1.1.0 carries the `correction` variable.

### Staged tickets v1.2.0 (ATLAS-110)

`planner-stage-tickets-v1.2.0.md.j2` is the live staged tickets template. Two
changes from v1.1.0, both targeting a single observed failure: a live staged
run cleared all three stages but failed gate 6 (`GATE6_UNKNOWN_KEY`, ~50 times)
because the model emitted tickets carrying `ATLAS-<n>` keys it had transcribed
from the roadmap in the corpus, instead of `"key": null` for new tickets. The
template's prose was already correct; its JSON output-contract **example**
contradicted it by showing a concrete `"key": "ATLAS-24 or null"`, which the
model pattern-matched over the prose.

- The output-contract example's key value is corrected to `null` — the safe
  default every new ticket needs on a greenfield plan. The "existing ticket
  echoes its key" case stays in prose; it is never modelled with a real-looking
  key (a concrete `ATLAS-<n>` in the example is exactly what the model copied).
- Rule 2 and the final self-check gain an explicit anti-copy instruction: the
  source documents contain `ATLAS-<n>` keys that are NOT the model's to assign;
  a new ticket uses `"key": null` regardless of any key shown in any document.

MINOR — every other instruction (including the ATLAS-109 `correction`/retry
block) is carried verbatim; the projection schema, reconciler, and gates are
unchanged. v1.0.0 and v1.1.0 are retained for historical `PlanRun`
reproduction. The same example-key contradiction is latent in
`planner-stage-epics-v1.0.0` (line 99, `"key": "ATLAS-E1 or null"`) and the
single-call `planner-v*` templates; it has not surfaced there (the corpus
carries ticket keys, not epic keys, and the single-call path has stronger
holistic prose) and is tracked as a near-term follow-up, not fixed
speculatively here.

### Anchor selection from the index — all paths (ATLAS-111)

Three new template versions land together so every path that emits a
`source_anchor` SELECTS it from an environment-supplied list instead of
CONSTRUCTING a slug from a rule:

- `planner-v1.2.0.md.j2` (single-call) — **CURRENT is bumped to this**, so the
  fix reaches the documented default path;
- `planner-stage-epics-v1.1.0.md.j2` (`STAGE_EPICS_VERSION`);
- `planner-stage-tickets-v1.3.0.md.j2` (`STAGE_TICKETS_VERSION`) — carries the
  ATLAS-109 `correction`/retry block and the ATLAS-110 key instruction verbatim.

A live `--staged` run failed gate 4 (`GATE4_UNRESOLVED_ANCHOR`): a ticket
anchored into `planning-large-corpora.md`, whose headings are the most
slug-hostile in the corpus (numbered `## 4.`, lettered `### A.`, em-dashed
`— chosen`), with a slug that matched no heading. The model cannot reliably
reverse-engineer the `slugify` algorithm (ingestion.py §2.3) for such headings —
and need not: the environment already holds the authoritative slug→heading map
in the `AnchorIndex` before the prompt renders. Each template now declares a
`valid_anchors` variable and renders a **Valid source anchors** section (one
`` `path#slug` — heading `` per line); the anchoring rule, source-docs prose,
example anchor (a select-from-list placeholder, per the ATLAS-110 example
lesson), and self-check all say select-not-construct. The list is derived from
`AnchorIndex.anchor_choices()` — the single slug implementation — so the list
the model selects from is the exact list gate 4 validates against; there is no
second slug computation. The single-call and both affected staged paths share
this fix; the dependencies stage carries no `source_anchor` and is untouched.

MINOR each — the §3.11 schema, reconciler, and gates are unchanged. Priors are
retained for historical `PlanRun` reproduction. **Forced consequence:** bumping
CURRENT makes `planner-v1.2.0` the live single-call release, subject to the
post-ATLAS-29 release gate below (AT-2 + AT-7); those legs are operator-run
(skipped in CI), so the operator runs the live AT suite to ratify the release —
the AT-7 number doubles as the gate-4-resolution evidence.

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

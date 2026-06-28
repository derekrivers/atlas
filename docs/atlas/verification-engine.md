# Verification Engine Design (Phase 7)

Status: Active design document for Phase 7. Consumes the evidence pipeline
(Phase 6); the VerificationCheck schema lives in `data-model-and-schemas.md`
§5.

## Principle

Verification answers one question: is the "no evidence = no completion"
rule satisfied for this ticket at this commit? It is a pure evaluator — it
never creates evidence, never transitions tickets itself (the PM Engine
acts on its verdict), and never accepts agent-tier evidence for a
machine-checkable requirement.

## Required-check matrix

Which `VerificationCheckType`s are required depends on `ticket_type`:

| ticket_type    | tests | lint | acceptance_criteria | documentation | scope | human_approval |
| -------------- | ----- | ---- | ------------------- | ------------- | ----- | -------------- |
| feature        | ✓     | ✓    | ✓                   | ✓*            | ✓     | risk ≥ high    |
| bug            | ✓     | ✓    | ✓                   | –             | ✓     | –              |
| tech_debt      | ✓     | ✓    | ✓                   | –             | ✓     | –              |
| infrastructure | ✓     | ✓    | ✓                   | ✓*            | ✓     | risk ≥ high    |
| documentation  | –     | ✓    | ✓                   | ✓             | –     | –              |
| spike/research | –     | –    | ✓                   | ✓ (findings)  | –     | ✓              |

`✓*` = required when the ticket has `documentation_requirements`. A SECURITY
check is surfaced for any ticket whose risk_level is `critical`, but v1
surfaces it as non-gating (`required=False`, status `not_applicable`) with
the evaluator deferred — it never blocks completion until a later Phase 7
ticket implements it. The matrix is configuration (YAML in repo), not code,
so tightening it is a doc change.

## Evaluation semantics

For each required check on ticket T with PR head commit C:

- **tests / lint / build / coverage:** the latest system-tier evidence of
  the matching type with `commit_sha == C`. Older commits never satisfy a
  check — a new push resets machine checks to PENDING. Agent-tier
  evidence is ignored for these checks entirely.
- **documentation:** a DOCUMENTATION_UPDATE record for C covering at least
  one path named in `documentation_requirements`.
- **acceptance_criteria (v1, honest):** operator-confirmed.
  `atlas verify <KEY>` presents each criterion as a checklist; the
  operator's confirmations are recorded as human-tier evidence per
  criterion. Each confirmation is a human-tier MANUAL_APPROVAL record
  pinned to the head commit C and scoped to the ticket, carrying
  `raw_payload["acceptance_criterion_hash"]` — the SHA-256 hex digest of
  the criterion's text after `.strip()` — as the discriminator that
  identifies which criterion it confirms (a blanket `human_approval`
  MANUAL_APPROVAL carries no such key and is ignored). The check PASSES
  only when every criterion is so confirmed at C; rewording a criterion
  changes its hash, so a stale confirmation no longer matches and
  re-confirmation is required. LLM-assisted pre-assessment (agent proposes
  pass/fail with cited diff lines, operator confirms) is a later
  enhancement, not v1.
- **scope (v1, heuristic):** the PR file list is compared against the
  declared in-scope set — the union of `relevant_docs` and the path part of
  `source_anchor` (`source_anchor.split("#", 1)[0]`); there is NO
  `allowed_paths` ticket field in v1 (OP-2), so scope derives from those two
  alone. Matching is exact normalised path equality; directory/prefix/glob
  matching is deferred. Out-of-scope files are presented to the operator for
  waive/fail; fully automatic scope verdicts are explicitly out of scope for
  v1. An operator decision is a human-tier MANUAL_APPROVAL record pinned to
  the head commit C and scoped to the ticket, carrying
  `raw_payload["scope_decision_path"]` — the exact normalised file path it
  decides — with its `status` carrying the choice: `PASSED` = waive (the
  out-of-scope file is acceptable), `FAILED` = fail (the file should not be
  here — a scope dispute). For a file with both, the latest by
  `(created_at, id)` decides. The check PASSES when every changed file is
  in scope or every out-of-scope file is waived at C; it FAILS when any
  out-of-scope file's latest decision is a fail (routing to
  `needs_human_decision`); it stays PENDING while any out-of-scope file is
  undecided (an undecided file is unresolved, not rejected). A non-human
  MANUAL_APPROVAL carrying a matching path does not decide a file.
- **human_approval:** a MANUAL_APPROVAL evidence record from the operator.

## Verdict and completion

`verify(T)` returns PASSED only when every required check has a PASSED
evaluation at the current head commit. The PM Engine performs
`review_required → done` only on a PASSED verdict
(`symphony-integration.md#ticket-transitions-one-writer-per-state-edge`).
A FAILED verdict routes to `changes_requested` (machine-check failures)
or `needs_human_decision` (criteria/scope disputes).

## Reports

`atlas verify <KEY>` prints the per-check table with evidence IDs and
commit pins; `--json` for automation. Verdicts persist as
VerificationCheck rows; there is no dashboard (Revision 1).

## Open items

- Coverage minimums: start with "coverage evidence must exist", add a
  threshold once a baseline exists.
- LLM-assisted acceptance-criteria assessment: design when Phase 8 makes
  the operator the bottleneck, not before.
- SECURITY verification (ATLAS-71 gap): no evaluator exists in v1. The rule
  resolver surfaces a SECURITY check only for `risk_level == critical`, as
  `required=False` so it never gates; the real evaluator is a later ticket.
  When implemented, reconsider moving the risk→security threshold into the
  matrix YAML so tightening it stays a doc change.

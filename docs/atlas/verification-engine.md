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

`✓*` = required when the ticket has `documentation_requirements`. Security
checks are added to any ticket whose risk_level is `critical`. The matrix
is configuration (YAML in repo), not code, so tightening it is a doc
change.

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
  criterion. LLM-assisted pre-assessment (agent proposes pass/fail with
  cited diff lines, operator confirms) is a later enhancement, not v1.
- **scope (v1, heuristic):** the PR file list is compared against the
  union of paths implied by `relevant_docs`, `source_anchor`, and an
  optional `allowed_paths` ticket field; out-of-scope files are presented
  to the operator for waive/fail. Fully automatic scope verdicts are
  explicitly out of scope for v1.
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

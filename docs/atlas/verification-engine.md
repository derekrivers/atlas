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

- **tests / lint:** append-only observations sharing the same non-null
  `external_run_id` are lifecycle snapshots of one execution. Within that
  execution, any ordered snapshot supersedes its unordered snapshots; the
  greatest GitHub lifecycle `source_event_at` is current, and observations tied
  there are folded together without UUID precedence. An execution containing no
  ordered snapshot remains PENDING, while unrelated executions and records
  without an `external_run_id` are never collapsed. After this lifecycle
  consolidation, the latest execution is resolved independently for each
  matching CI `job_name` at `commit_sha == C`. FAILED has precedence across
  current jobs; all current jobs must be PASSED for the check to pass, and every
  other combination is PENDING. Missing job metadata fails closed until evidence
  is re-pulled. UUIDs, payload hashes, and Atlas ingest timestamps never
  determine recency. Older commits never satisfy a check — a new push resets
  machine checks to PENDING. Agent-tier evidence is ignored entirely.
  BUILD/COVERAGE evidence is ingested but is not a v1
  `VerificationCheckType`.
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
  The governed acceptance-session action preserves this evaluator contract; it
  does not introduce a session-only approval model. Its caller selects the
  complete pinned snapshot by stable integer indexes and fingerprint, while the
  server resolves the live criterion text, ticket identity, exact head and
  `human/operator` actor. It delegates to the shared confirmation-record service
  used by `atlas confirm`, so both paths write the same criterion hash and
  human-tier shape through the same evidence write guard. Old-head records stay
  append-only history and cannot satisfy a session or evaluator at a new head.
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
- **human_approval:** a blanket human-tier MANUAL_APPROVAL record from the
  operator, pinned to the head commit C and scoped to the ticket, carrying in
  `raw_payload` NEITHER `acceptance_criterion_hash` NOR `scope_decision_path` —
  that double absence is what distinguishes a blanket PR approval from an
  acceptance-criterion confirmation (which carries the criterion hash) or a
  scope decision (which carries the literal path). The latest such record by
  `(created_at, id)` decides, with its `status` passing through: `PASSED` =
  approved, `FAILED` = the operator rejected the PR (a dispute routing to
  `needs_human_decision`, mirroring scope's waive/fail). None at C → PENDING (an
  unapproved PR is unproven, not failing). A non-human MANUAL_APPROVAL carrying
  neither discriminator does not approve a PR.

## Verdict and completion

`verify(T)` composes the per-check evaluations into one ticket verdict over the
gating checks (`required=True`): it returns PASSED only when every required
check has a PASSED evaluation at the current head commit; FAILED if any required
check is FAILED (fail precedence — a single failing required check sinks the
ticket); otherwise PENDING (a required check that is PENDING, WARNING, or
NOT_APPLICABLE is non-passing but not failing). A required check whose type has
no evaluator wired does not silently pass — it holds the verdict at PENDING.
Non-required checks (`required=False`, like the deferred SECURITY surface) appear
in the breakdown but do not gate. The PM Engine performs
`review_required → done` only on a PASSED verdict
(`symphony-integration.md#ticket-transitions-one-writer-per-state-edge`).
A FAILED verdict routes to `changes_requested` (machine-check failures)
or `needs_human_decision` (criteria/scope disputes).

PR completion aggregates one level up: a PR is verified by composing the
per-ticket verdicts of the tickets it closes — `verify(PR)` is PASSED only when
every closed ticket is PASSED, FAILED if any closed ticket is FAILED, otherwise
PENDING; an empty close-set is PENDING (never a vacuous PASS over no tickets).
This is the same fold rule as the per-ticket verdict, applied to ticket verdicts
instead of check evaluations. Each closed ticket is evaluated independently at the
PR head commit (the validator passes each ticket its own identity, so evidence
scoped to one ticket never satisfies another). Resolving WHICH tickets a PR closes
(from its body, GitHub) is the CLI's / Phase 8's job, not the validator's — the
ticket set is an input.

## Reports

`atlas verify --pr <N> --repo <OWNER/REPO>` (ATLAS-80) is verify + record +
report for one PR: it resolves the PR head commit C and changed files from
GitHub, resolves which tickets the PR closes (the `(ATLAS-NN)` key in the PR
title is the primary source; `--tickets ATLAS-a,ATLAS-b` overrides), reads the
stored evidence, runs the pure `evaluate_pr`, and prints the PR/per-ticket
verdict with the per-check breakdown (check_type, required, status, the
evaluator's reason, and evidence IDs); `--json` emits the serialised
PRVerification for automation. The JSON payload preserves those canonical fields
and adds top-level `blocking_checks`: the ordered required checks whose status is
not PASSED, each carrying `ticket_id`, `ticket_key` when known, `head_commit`,
`check_type`, `required`, `status`, `evidence_ids`, and the evaluator's typed
`reason`. Each run PERSISTS the verdict as append-only VerificationCheck rows
(one per check; a re-run appends a fresh set, never mutating prior rows); there
is no dashboard (Revision 1). The command is NON-interactive and writes NO
evidence: the separate `atlas confirm` command writes the human-tier
acceptance/scope/human-approval confirmations pinned to C. The human report's
confirmation note is derived from the current check outcomes and appears only
when human-tier confirmation checks remain PENDING at C; it must not claim
operator confirmations are absent once matching human-tier records exist.
Exit-code contract: a produced report is exit 0 for any verdict
(PASSED/PENDING/FAILED); only a precondition (a malformed `--repo`, a missing
token, an unknown PR or transport error, a cold database) is a non-zero exit. A
future `--strict` mode (FAILED → non-zero, for CI gating) is a follow-up — until
it exists, `verify` does not block a merge on a FAILED verdict.

`atlas confirm` consumes the structured pending-capture result and reports
positive decisions (confirmed/waived/approved), negative decisions
(failed/rejected), and skipped actions separately. It reports skipped actions
even when other decisions were recorded, so a partial session cannot hide
outstanding work. A resolved close-set with no pending decisions is the only
zero-action success and prints `No outstanding confirmations ...` with exit 0.
An empty close-set or one made entirely of unknown ticket keys performs no
assessment, prints `No confirmation assessment performed ...`, and exits with
the precondition code; it cannot be mistaken for exhausted confirmation work.
The governed session action is stricter but compatible: it permits no partial
save, negative criterion ruling or caller-supplied record fields, and advances
only after every criterion plus blanket approval is explicitly positive. It
reuses the CLI's confirmation-record service and canonical evidence writer;
the existing CLI output and evaluator behaviour are unchanged.

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

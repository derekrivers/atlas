# Review Acceptance Console Design (Phase 14)

Status: Delivered and closed Phase 14 design authority. Defines the
authenticated, exact-head, stepwise browser workflow for PR acceptance proven
by `docs/closure/phase-14-closure-report.md`. It consumes Phase 12's
mainline-freshness guarantees and Phase 13's session, actor and action-receipt
framework.

## Purpose and milestone

Phase 14 turns the delivered review queue into an operator acceptance console.
For one Review Required PR, the operator can:

1. create an exact-head acceptance session;
2. pull evidence for that head;
3. inspect and confirm the live acceptance criteria;
4. run verification;
5. see whether the exact head is currently eligible for a manual GitHub merge.

The console stops at that boundary. Merge remains a deliberate hand motion in
GitHub. Post-merge proof, schema upgrade and the two-sync completion tail remain
outside the browser workflow in this phase.

## Binding invariants

- The acceptance session pins one repository, PR, close-set, head SHA, base SHA,
  head/base refs and criteria fingerprint.
- Only Phase 12's shared exact-head assessment decides whether the PR is
  current with main. The API and UI never recreate that classifier.
- Evidence, confirmations and verification are authoritative only for the
  session's exact head.
- Any PR head, live main/base SHA, repository identity, eligibility, close-set
  ticket existence/status or criteria movement closes the live readiness gate.
  A mutation that observes movement marks the session stale; a GET reports the
  mismatch without rewriting stored history. A historical PR `base.sha` from
  an ineligible assessment is not evidence that live `main` moved.
- A stale session is immutable history. The operator starts a new session;
  Atlas never retargets an old session to a new head.
- The final readiness result is advisory authority for the operator's manual
  merge, not merge authority for Atlas.

## Position in the architecture

`atlas.orchestration` owns the acceptance-session state machine and coordinates
the existing GitHub assessment, evidence, confirmation and verification
services. Each HTTP action resolves authenticated command context, calls one
application operation and presents the typed result. The UI renders the state
machine and never decides readiness.

The console reuses the Phase 13 operator-action/idempotency framework for every
write. GitHub credentials remain server-side runtime configuration; the
browser never sends or receives them.

## Durable acceptance session

An `AcceptanceSession` is append-oriented operational history with:

- session ID and lifecycle state;
- repository and PR number;
- resolved Atlas close-set;
- initial exact-head assessment;
- pinned head/base SHA and refs;
- acceptance-criteria fingerprint;
- server-resolved operator actor;
- evidence, confirmation and verification step summaries;
- stored verification-time readiness result and reasons as historical evidence;
- created/updated/staled timestamps.

Lifecycle:

```text
preflight_passed
  → evidence_ready
  → confirmations_ready
  → verification_passed
  → merge_ready
```

`stale`, `blocked` and `failed` are terminal for that session. Step retries with
the same idempotency key replay their stored outcome. A recoverable transport
failure does not advance the session and can be retried with a new key; it
cannot erase prior history.

The first version permits one non-terminal session per repository/PR, and that
session pins exactly one head.
Creating it again with the same command key replays the existing result.
Creating a new session after head movement creates a new record and leaves the
old record intact.

### Delivered durable-session foundation

The durable foundation stores one canonical `AcceptanceSession` row. Its
pinned fields are repository owner/name, PR number, sorted close-set, head and
base refs/SHAs/repository identities, the structured initial assessment, the
server-read criterion snapshot and fingerprint, the hashed creation-command
identity and the `human/operator` actor. Pinned-field updates are rejected by
the model/repository boundary and by database triggers; no operation can
retarget a session.

Creation first invokes the shared Phase 12 assessment. Only an `eligible`,
`current`, open, non-draft, same-repository PR targeting literal `main`
continues to close-set and ticket reads. Every key must resolve to a current
stored ticket in `review_required`. The service then reads each ticket's
canonical `acceptance_criteria`; no cached or caller-supplied criterion content
is an input. It snapshots in sorted ticket-key and stored-index order and
fingerprints canonical JSON with SHA-256. The session insert is the final
operation.

The database permits one non-terminal session per repository/PR. Only the same
creation-command identity replays its original row, including during a
concurrent create; a different command colliding with an identical active
session returns `active_session_exists`. Before returning a collision, creation
compares the live repository/PR, close-set, head/base refs, SHAs and repository
identities, eligibility, ticket existence/status and criteria fingerprint with
the active row. Movement atomically marks that row terminal `stale` and returns
every typed mismatch; this also applies when creation's ticket preflight finds
a formerly eligible close-set ticket missing or no longer `review_required`.
The caller must retry to create the new exact-head lifecycle. Both histories
remain queryable, and no command retargets the old row.

`compare_acceptance_session_freshness` is pure and returns all typed
repository, PR, close-set, head/base ref/SHA/repository, eligibility,
integration, ticket existence/status and criteria mismatches from supplied
assessment and ticket values. Missing, non-`review_required` or indeterminate
external state is never fresh. It compares a base SHA only when the assessment
labels it `live_branch`, never when it is a `historical_pr_snapshot`. Mutation
callers compose those reasons with one atomic `mark_stale`; read callers use
the comparison result without changing the stored row.

`stored_acceptance_session_status` is also pure. It projects pinned identity,
criteria, lifecycle, every step summary, receipt IDs, blocking reasons and
timestamps from the supplied model only. Its readiness member is named
`historical_readiness`, carries `authority: historical_only`, and fixes
`is_current_merge_authority` to false. It performs no GitHub, ticket, evidence,
confirmation, verification or storage operation. The later live-readiness
service remains a separate composition boundary.

This foundation adds no acceptance HTTP route and performs no evidence pull,
confirmation, verification, readiness evaluation, Git operation, GitHub or
Linear write, merge or background work.

## Preflight

Session creation performs the Phase 12 exact-head assessment before any
evidence, confirmation or operator approval write. It succeeds only for an
open, non-draft, same-repository PR targeting `main`, current with exact main,
with a close-set whose tickets are all `review_required`.

Preflight also snapshots the live ticket acceptance criteria and stores a
canonical criteria fingerprint. It does not treat the UI's previously cached
ticket body as authority.

Behind, diverged, conflicted or otherwise rebase-eligible states return the
named Phase 12 recovery command but do not start a session. Draft, fork,
non-main, closed, unknown and indeterminate states fail closed without writes
other than the authenticated action outcome.

The typed preflight result keeps `behind`, `diverged` and `conflicted`
distinct and returns exactly
`atlas pr rebase prepare --pr <N> --repo <owner>/<repo>` as bounded recovery
data for those three states only. Merged, closed, draft, fork-head, non-main,
unknown PR and indeterminate external state have separate reason codes and no
recovery command. Foreign exception text is never returned.

## Evidence action

The API-independent evidence operation accepts exactly an acceptance-session ID
and authenticated operator command context. Repository owner/name, PR number,
close-set, product and pinned head are resolved from the stored session and its
canonical close-set tickets; the operation accepts no caller SHA, repository,
ticket key, product, GitHub token or raw payload. Only a `preflight_passed`
session is retryable. A completed, stale or otherwise out-of-order session is
refused before GitHub or evidence work.

The operation invokes the shared `drive_evidence_pull` service directly with
the injected GitHub client and canonical `EvidenceRepo`. It does not execute or
parse the CLI and contains no second GitHub mapper. Consequently conditional
requests, bounded rate-limit handling, trust-tier mapping, the system pin
triple and `(external_run_id, payload_hash)` source idempotency remain owned by
the existing evidence path. The call is synchronous and bounded; there is no
partial session state or background-job protocol.

Immediately before that external pull, the action runs
`compare_acceptance_session_freshness` over a new shared PR assessment and live
close-set tickets. Every mismatch is retained. Stale or indeterminate state
atomically marks the evidence step blocked and the session `stale`, records the
action receipt, and performs no evidence pull. Immediately after the pull, the
same assessment and comparison run again. Movement then marks the session
stale; evidence already appended at the observed commit remains canonical
history but no evidence summary or readiness is attached to the moved session.

On success the action re-reads canonical evidence for the session product and
exact head. The evidence step stores only bounded source counts (checks,
reviews and documentation, plus the newly appended exact-head count), trust-tier
counts, status counts, complete-pin and exact-head-pin counts/booleans,
oldest/latest source-event timestamps and the receipt ID. A historical review
remains valid canonical evidence at the commit it attested but is excluded from
both that exact-head aggregate and its new count. Every current-head record
returned by the pull must be present in the canonical projection. Evidence IDs,
summaries, source URIs, raw payloads, job logs, tokens and foreign error text are
absent. A source-idempotent no-op therefore records `new_count: 0` while
summarising the already stored exact-head records; evidence from another head is
excluded.

The Phase 13 gateway commits the action-key reservation before bounded external
work, then commits the session transition and terminal receipt atomically. A
receipt failure rolls back the session advance without deleting evidence that
the canonical pull already appended. Same-key replay and altered replay invoke
neither GitHub nor the evidence service. A per-session server-side guard admits
one synchronous action at a time, so a concurrent tab cannot duplicate the
pull or overwrite the first transition.

Transport, authentication, exhausted rate-limit and malformed-source failures
are separate receipt result codes and return the complete bounded external-read
reason set; timeout, malformed and failed reads also identify external state as
indeterminate. They leave the session at
`preflight_passed`; exact same-key replay returns the stored failure without
external work. After a new freshness assessment, the operator may retry with a
new action key. No failure code is accepted as `evidence_ready`.

## Confirmation action

The console renders every live criterion from the session snapshot. The
operator must explicitly confirm each criterion and the manual approval gate.
The API-independent request contains only the session ID, exact pinned criteria
fingerprint, a tuple of integer criterion indexes and an explicit manual
approval boolean. The stable indexes enumerate the canonical session snapshot,
whose ordering is itself derived from sorted close-set ticket key and each
ticket's stored list index; browser display order is not an identity source.
The request cannot carry criterion text, actor, repository, ticket key or head
SHA. Strict request validation rejects every such extra field.

Every snapshot index must appear exactly once and manual approval must be
literal true. A missing, duplicate, unknown or extra index, or a fingerprint
that differs from the pinned value, is a validation result: it reserves no
action key and writes no confirmation, receipt or session advance. A valid
action runs only from `evidence_ready`.

Inside the Phase 13 gateway transaction, the action locks and re-reads the
session, invokes the shared Phase 12 exact-head assessment, and re-reads every
close-set ticket definition. The shared freshness comparator checks repository,
PR, close-set, head/base refs and SHAs, repository identities, eligibility,
integration status, ticket existence/status and the live criteria fingerprint.
Any mismatch advances the locked session only to terminal `stale`, records the
typed reasons and commits that change with a refused action receipt; it appends
no human-tier record.

On a fresh session, the action delegates to the same confirmation domain
service and evidence writer as `atlas confirm`. It appends one acceptance
confirmation per criterion and one blanket `MANUAL_APPROVAL` per close-set
ticket, all pinned to the session head and attributed by the server as
`human/operator`. Those records, the `confirmations` step receipt reference and
the `confirmations_ready` lifecycle advance commit in the gateway's one
transaction. A confirmation-write or receipt failure rolls the complete set
back. The action does not invoke verification.

The gateway locks the session row for the transition as well as reserving the
idempotency key. An exact same-key replay returns the original receipt without
new records. Reusing the key with an altered ordered request conflicts, while a
concurrent different key observes the completed locked transition and cannot
append a second set or replace the recorded receipt reference.

## Verification and merge readiness

The delivered verification action accepts only a session ID plus the
authenticated `human/operator` command context. Repository, PR, close-set and
head are resolved from the session. Before invoking verification it requires
the evidence and confirmation summaries to be complete, re-runs the shared
Phase 12 assessment, re-reads every close-set ticket and criteria definition,
and compares all live identities with the pinned session. It then resolves the
PR context and performs a second just-before-verifier assessment so head, main
or close-set movement during the changed-file read cannot enter the engine.
Every failed prerequisite is returned and stored on the verification step; no
verifier call occurs in those cases.

The action calls the canonical `run_verify` orchestration in process over the
session close-set and PR context. It never executes `scripts/close_ticket.py`,
invokes the CLI or parses CLI JSON. Only an explicit top-level `PASSED` verdict
whose `head_commit` is a valid 40-hex SHA exactly equal to the session head and
whose ticket identities equal the complete close-set can advance. Exit code,
green CI, append-only checks from an earlier run and old-head evidence or
confirmations are not verdict authority. Pending, failed, warning,
not-applicable, malformed, wrong-close-set, invalid-head and mismatched-head
outcomes have distinct typed blockers.

Immediately after PASSED, the action performs a third fresh shared assessment
and live criteria read. Repository owner/name, PR number, close-set, head and
base refs/SHAs/repositories, eligibility, integration status, ticket status and
criteria fingerprint must still reproduce the session. Movement or
indeterminate external state stores the exact-head PASSED verdict as history,
marks readiness blocked and makes the session terminal `stale`; it never emits
current authority.

Success stores `merge_ready: true` only with a generated verdict UUID, verified
head, ticket/blocking-check counts, the complete final assessment identity,
criteria fingerprint and the shared verification/readiness receipt reference.
The session mutation and terminal operator-action receipt commit in one
transaction. A receipt or session-store failure rolls back readiness and the
lifecycle advance; append-only verification checks already written by the
canonical engine may remain historical. Same-key replay returns the original
receipt without another assessment or verifier call, and a per-session guard
prevents concurrent verifier work in the supported single-process server.

The stored verification-time result remains historical evidence, not current
merge authority. `AcceptanceSessionLiveReadinessService` is the single bounded,
read-only operation for each later
`GET /api/v1/acceptance-sessions/{session_id}`. It first validates that stored
true has the PASSED exact-head verdict UUID, matching final identity and shared
receipt, then makes one fresh Phase 12 assessment and re-reads the current
close-set ticket definitions and criteria fingerprint. Any stored-history
defect, head/main/repository/eligibility/criteria movement, indeterminate
assessment, timeout, malformed response or other external-read failure returns
`merge_ready: false` with every typed reason. It performs no session, receipt,
evidence, verification-check, ticket or external-system write and never exposes
cached true after a failed live read.

The console displays the exact verified head and a clear instruction to merge
that head manually in GitHub. It does not expose a merge button.

## HTTP contract

The delivered API adapter adds one authenticated acceptance-session resource:

```http
POST /api/v1/reviews/{pr_number}/acceptance-sessions
GET  /api/v1/acceptance-sessions/{session_id}
POST /api/v1/acceptance-sessions/{session_id}/evidence
POST /api/v1/acceptance-sessions/{session_id}/confirm
POST /api/v1/acceptance-sessions/{session_id}/verify
```

The creation body contains the repository slug only. The server validates it
against supported/configured repository policy. Step routes use strict JSON,
Phase 13 authentication/CSRF/origin controls and an `Idempotency-Key`.

The GET is authenticated and non-mutating. Its route dependency makes exactly
one call to the bounded live-readiness service; the route itself contains no
GitHub, criteria or readiness logic. The response distinguishes stored
verification history from current `merge_ready` and all live blocking reasons.

`ATLAS_ACCEPTANCE_REPOSITORIES` configures the comma-separated server-side
repository allowlist. The request is parsed only as owner/name components;
URLs, ports, queries, fragments, additional path components and unconfigured
repositories are rejected before external I/O. The application factory accepts
the equivalent tuple and an injected GitHub client for deterministic tests.

All response state uses the canonical Phase 14 enums. Successful actions return
the updated safe session and receipt. Creation returns the session plus the
stored hashed creation-command identity; it never returns the raw idempotency
key. Typed validation, unknown resource, stale/refused action, altered replay,
in-progress command, storage failure, external failure and timeout map to
bounded status-specific responses without foreign exception text.
Receipt-backed errors retain their canonical action `result_code`; stale
refusals also retain every movement or blocking `reason`. Gateway-level
altered-key and in-progress conflicts remain distinct through `conflict_code`.

Operations are synchronous and bounded in the first version. The phase adds no
job queue, websocket, background worker or progress protocol. Transport timeout
is a named non-advancing outcome; the operator refreshes the session before
retrying.

The concrete GitHub transport now applies a finite positive per-request
deadline (15 seconds by default; application-factory configurable). A create
or step timeout does not advance the session. A GET timeout keeps HTTP 200 for
an existing session but returns `merge_ready: false`,
`external_read_timeout` and `external_state_indeterminate`, and does not alter
stored history.

## UI workflow

The Review queue links to a focused acceptance panel showing:

- preflight and exact-head identity;
- close-set and live criteria;
- evidence status and pin completeness;
- per-criterion confirmation state and manual approval;
- verification matrix and explicit verdict;
- merge-readiness status with every blocking reason;
- action receipts and timestamps relevant to the session.

Only the next valid action is primary. Completed steps remain inspectable.
Blocked/stale states explain the recovery route. The UI never jumps a step,
constructs readiness locally, changes ticket status or retries a mutation
silently. Initial load and every later refresh use the GET's live-readiness
result; a failed live read closes the displayed merge gate rather than leaving
a cached `merge_ready: true` visible as authority.

## Security and failure rules

- All Phase 13 session, CSRF, origin, actor and receipt controls remain binding.
- Repository and PR identity are server validated; external URLs are never
  accepted as fetch targets.
- GitHub token, raw evidence payloads and unbounded logs never reach the UI.
- Lesson or PR text is rendered as inert text, never trusted markup.
- Refresh/read operations may perform bounded external reads but do not mutate
  the session or any external system.
- Audit/receipt failure prevents step advancement.
- One action in flight per session is enforced server-side, not only in the UI.
- A second tab may observe state but cannot overwrite a completed or stale
  transition.

## Explicit non-goals

- GitHub merge, auto-merge, merge queue or branch update.
- PR rebase controls in the UI.
- Automatic conflict resolution.
- Linear status mutation, Changes Requested routing or Symphony resume.
- Post-merge `PR_MERGED` proof, schema migration or PM sync.
- Replacing the CLI acceptance driver.
- Background jobs, push notifications or remote hosting.
- Multi-user approval or delegated review.

## Milestone test

The closed milestone uses the built UI, live FastAPI application services and
canonical SQLite repositories with deterministic injected GitHub, evidence and
verification boundaries. A seeded Review Required PR current at exact main
creates a session, pulls evidence, confirms every live criterion and the
manual gate, records a PASSED exact-head verification and reaches current
`merge_ready: true` for the displayed head.

The same suite seeds PR-head and live-main movement before evidence, during the
evidence seam, before confirmation, before verification and after PASSED. It
also covers criteria drift, old-head evidence and confirmations, missing gates,
every non-PASSED verdict, same/altered replay, duplicate submission, two-tab
concurrency, timeout, malformed data and receipt/store failure. A post-PASSED
movement or failed GitHub read makes the next GET return current false with all
typed reasons while the stored session, receipts and verification history stay
unchanged.

Process, network and client spies plus repository assertions prove the workflow
performs no PR merge, branch/rebase/push, Linear write or transition, Symphony
action, schema upgrade or PM sync. The Phase 13 hostile-Origin, CSRF, session
and redaction matrix applies to every Phase 14 POST. The delivered guard is
synchronous and one-process only, and readiness remains advisory: the operator
must preserve the one-PR freeze across the residual final-GET-to-manual-GitHub-
merge race.

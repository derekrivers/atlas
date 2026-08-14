# Governed Operator Actions Design (Phase 13)

Status: Delivered Phase 13 design authority. Defines the first writable
Operator API and UI slice, the single-operator authentication boundary,
server-owned actor context, idempotent action receipts, and lesson
promotion/rejection. Phase 14 acceptance commands and the Phase 15 complete
delivery-policy replacement reuse this authority boundary. Closure evidence is
recorded in `docs/closure/phase-13-closure-report.md`.

## Purpose and milestone

Phase 13 turns the delivered Lessons draft queue into Atlas's first browser
control surface. An authenticated operator can open a DRAFT lesson, promote it
with an operator-assigned confidence, or reject it. The mutation is performed
through the existing lesson domain behaviour, attributed by the server,
concurrency-safe, idempotent and durably audited.

The phase closes the loop:

`delivery outcome → DRAFT lesson → operator ruling → ACTIVE or ARCHIVED → future context packs`

This is the smallest useful write because the decision already belongs to the
human under ADR-0009, the CLI already owns the domain behaviour, and the
mutation is local to the Atlas store. It introduces no GitHub or Linear write.

## Architectural boundary

`atlas.api` authenticates, validates the HTTP command envelope and maps typed
outcomes to HTTP. It does not implement lesson rules. An
`atlas.orchestration` command service owns the transaction across the lesson
operation, idempotency record and operator-action receipt. `atlas.learning`
continues to own valid lesson lifecycle transitions. The UI displays and
confirms commands but contains no lifecycle or actor-attribution rules.

The existing contains-no-logic rule remains binding for reads. Write routes add
one equally strict rule: a route may resolve the authenticated command context,
make exactly one application-service call and present its result. Branching on
lesson state, replay state or actor identity belongs below `atlas.api`.

The CLI and HTTP surfaces call the same lesson disposition service. Phase 13
must not create a second promotion implementation. The service accepts only
the server-owned ADR-0009 actor (`human` / `operator`); agent, system and
alternate-human contexts fail before idempotency reservation or mutation.

## Threat model and supported topology

The supported deployment remains one operator on one machine with the API
bound to `127.0.0.1` and the Vite UI bound separately to `127.0.0.1`. The
browser sends same-origin `/api` requests through the Vite development proxy
to the API. Remote binding, direct cross-origin browser use, multi-user
identity and externally hosted operation remain unsupported.

The protected assets are:

- authority to admit a lesson into future context packs;
- authority to revise Atlas delivery-admission policy;
- operator identity and action history;
- the bootstrap operator credential and live session;
- the integrity of idempotency and action receipts;
- the Atlas store.

The phase explicitly defends against:

- a hostile webpage attempting to mutate a localhost API;
- CSRF and permissive CORS;
- token disclosure through URLs, logs, generated JavaScript or browser storage;
- actor spoofing in request JSON or headers;
- duplicate submission and replay;
- reuse of one idempotency key for a different command;
- stale tabs racing another CLI or browser ruling;
- clickjacking and injected active content around a confirmation;
- accidental non-loopback serving;
- audit failure being treated as mutation success.

The phase does not claim to defend a machine already compromised as the
operator account.

## Authentication and session contract

The bootstrap credential is provided at runtime through
`ATLAS_OPERATOR_TOKEN`. Writable routes are enabled explicitly with
`atlas api serve --enable-writes`; the read-only loopback API remains startable
without the variable. Startup of writable routes fails closed when the token is
missing, shorter than 43 printable non-whitespace ASCII characters, longer than
512 characters, or below the 128-bit estimated entropy floor. Operators should
generate it with a cryptographic random source such as
`python -c 'import secrets; print(secrets.token_urlsafe(32))'`; the server-side
estimator is a guardrail, not proof of true randomness. The token is never
accepted in a URL or query parameter, embedded in the UI bundle, written to the
Atlas store or logged.

`POST /api/v1/session` accepts the bootstrap credential only in an exact
`Content-Type: application/json` body and compares it in constant time. Success
creates a 30-minute server-side session and returns a CSRF token once. The
browser receives an opaque, high-entropy, host-only, HttpOnly, SameSite=Strict
`atlas_session` cookie with no `Domain` attribute. The cookie is not marked
`Secure` on loopback HTTP. The UI keeps the CSRF token in memory only and sends
it in `X-Atlas-CSRF` for every mutation. `GET /api/v1/session` reports
authenticated/expiry state without returning the credential or CSRF secret.
`DELETE /api/v1/session` revokes the exact live session.

Every mutation additionally requires:

- a loopback Host and an exact Origin of `http://<Host>`;
- `Content-Type: application/json`;
- the per-session CSRF header;
- a non-expired server-side session;
- an `Idempotency-Key`;
- the configured API/UI same-origin contract.

CORS is not a substitute for these checks and is deny-by-default. Mutation
responses and session responses use `Cache-Control: no-store`. The UI is
protected against framing and receives a restrictive content-security policy.
Authentication failures are bounded/throttled and return one non-secret error.

Because the first version supports loopback HTTP, the cookie's Secure flag
cannot be claimed as protection on that topology. Remote or HTTPS serving is a
separate design gate and must make Secure cookies and transport security
mandatory before it becomes supported.

## Actor context

The authenticated server session always resolves:

```text
created_by_type: human
created_by_id: operator
```

Mutation request schemas contain no actor field. Unexpected actor-shaped fields
are rejected rather than ignored. Multi-user roles, permissions and delegation
remain deferred under ADR-0009.

## Operator action and idempotency model

Every accepted write uses one generic command envelope:

- authenticated actor context;
- action name;
- target type and stable target ID;
- idempotency key;
- canonical request fingerprint;
- request timestamp and correlation ID.

The action store enforces uniqueness of the idempotency key within the operator
action namespace by storing a SHA-256 `idempotency_key_identity`, never the raw
key. The gateway inserts this reservation before invoking the command. The
canonical request fingerprint covers the action name, target type/ID and the
complete validated command payload; it is deterministic across JSON key order
and rejects unsupported or non-finite values.

The first terminal outcome is retained. Repeating the same key with the same
fingerprint returns the stored outcome and never repeats the domain mutation.
Reusing the key with a different fingerprint returns `409 Conflict`. A
committed key reservation with no terminal receipt is treated as an explicit
in-progress owner and returns a named in-progress conflict without an unbounded
polling loop; recovery must never treat a missing receipt as permission to
rerun a possibly started command. A storage `OperationalError` is reported as
in-progress only after a separate read proves that reservation; an error with
no visible owner is a typed storage failure.

The gateway loads command-declared domain inputs inside its transaction and
detaches them before invoking the command. The command receives only a
session-free context containing those detached values, the server-resolved
actor and returns a mutation plan of detached ORM values. It never receives a
facade, callable or other object that retains the gateway's SQLAlchemy session.
The gateway validates and applies the plan after the command returns, without
attaching the command-owned values, so `inspect(row).session` and
`object_session(row)` cannot recover the transaction. A plain plan retains the
generic merge behaviour; a compare-and-set plan declares its observed values
and exact update fields, and the repository emits one conditional SQL update.
It never falls back to an unconditional save when the predicate misses. Only
the gateway can flush, commit, roll back or close that transaction. It flushes
planned mutations and receipt insertion separately for classification, then
catches failure from the actual transaction commit as a receipt-commit failure.
Any such failure rolls back the reservation, mutation and receipt together.

Successful lesson dispositions include one purpose-specific, append-only safe
result snapshot in that mutation plan. It contains the complete disposition-time
`Lesson` projection, is keyed by the hashed idempotency identity, and commits in
the same transaction. On successful replay the gateway loads that immutable
snapshot alongside its receipt; it does not rebuild the response from the
mutable current lesson. This snapshot is not generic receipt metadata and has
no public write surface.

Commands that must append through an independently transactional canonical
store use the gateway's bounded-external variant. It commits the same
idempotency reservation before external work, loads a detached command input
without retaining a database transaction, and then atomically commits only the
domain transition and receipt. A missing receipt remains an explicit
in-progress owner. Receipt failure rolls back the domain transition but does
not delete or rewrite independently appended canonical history. The acceptance
evidence action is the first consumer; its per-session synchronous guard also
prevents two different keys from entering external work concurrently.

An append-only `OperatorActionReceipt` records:

- receipt and correlation IDs;
- action and target;
- server-resolved actor;
- idempotency key identity and request fingerprint reference;
- before and after `EntityStatus` values where applicable;
- a server-controlled result code from `action_succeeded`, `action_refused`,
  `stale_state`, `action_failed`, the four bounded acceptance-evidence failure
  codes (`evidence_transport_failed`, `evidence_authentication_failed`,
  `evidence_rate_limit_failed`, `evidence_malformed_source`) or
  `action_conflict`;
- default-deny result metadata limited to `changed` (boolean),
  `affected_count` (integer `0..1000000`) and `confidence` (finite float
  `0.0..1.0`);
- created/completed timestamps.

The lesson mutation, immutable success snapshot and successful receipt commit
atomically. If either durable result record cannot be persisted, the lesson is
not changed. Secrets, full request bodies, raw evidence payloads, lesson content
and exception traces are not copied into receipts or rendered receipt JSON.
Result codes and before/after states are
closed enums rather than free-form command strings. Outcomes and result codes
also form one enforced matrix: success uses `action_succeeded`; refusal uses
`action_refused` or `stale_state`; failure uses `action_failed` or one of the
four acceptance-evidence failure codes; and conflict uses `action_conflict`.
The database, gateway, canonical model, presentation
path and public repository writers all enforce that matrix. The gateway
discards every unapproved metadata field without inspecting its value, so
callers cannot bypass either the terminal-claim invariant, controlled
vocabularies or the metadata default-deny boundary.

## Lesson disposition contract

Phase 13 adds exactly two domain commands:

```http
POST /api/v1/lessons/{lesson_id}/promote
POST /api/v1/lessons/{lesson_id}/reject
```

Promote accepts only:

```json no-schema
{"confidence": 0.8}
```

Confidence is finite and in the inclusive range `0.0..1.0`. An accepted value
is canonicalised before the domain mutation to the PostgreSQL `NUMERIC(4,3)`
scale using decimal round-half-up (`0.0004` becomes `0.0`; `0.9999` becomes
`1.0`). The canonical lesson, immutable result snapshot, receipt metadata and
first response therefore agree exactly. The fingerprint retains the submitted
value, so distinct confidence values remain altered replays even when their
canonical values match. Reject accepts an empty strict JSON object. Actor,
status, content and unknown fields are rejected with `422` before the service
runs. Both commands require the current stored
lesson to be DRAFT. The state transition is a compare-and-set operation; if
another browser or CLI command has already changed the lesson, the loser returns
`409 Conflict`
with the safe current lesson representation and performs no mutation. The
loser's transaction, including its idempotency reservation, is rolled back, so
the concurrent ruling produces no second receipt.

`LessonDispositionService` is independent of FastAPI and is the only service
used by the CLI and future HTTP presenters for these commands. It loads the
target once inside the gateway-owned unit of work, delegates the transition
decision to `atlas.learning`, and returns a typed outcome plus updated or safe
current `Lesson`. The command context supplies the actor; promote/reject payloads
cannot override it. The public lesson repository exposes no direct promote or
reject writer, and its separate archive operation accepts ACTIVE lessons only,
so the governed service is the sole persistence path for DRAFT dispositions.
Invalid confidence is rejected before reservation or write.
Receipt persistence failure rolls back the lesson CAS, reservation and receipt
together.

Outcomes:

| Command | Before | After |
| --- | --- | --- |
| Promote | DRAFT | ACTIVE with operator confidence |
| Reject | DRAFT | ARCHIVED |

Success returns `200` with the updated safe lesson representation and its
bounded action receipt. Receipt attribution is server-owned `human` /
`operator`; credentials, the raw idempotency key, session and CSRF values, raw
request bodies and internal exceptions are never returned. An unknown lesson
returns `404`. Validation failure returns `422`; a non-DRAFT lesson, stale
compare-and-set, altered replay or in-progress idempotency owner returns `409`.
Unauthenticated requests return `401`; authenticated requests failing
origin/CSRF policy return `403`, and a non-strict content type returns `415`.
A replay with the same key and fingerprint returns the byte-equivalent semantic
success and receipt from the immutable disposition-time snapshot without a
second mutation, even if a later archive, citation or metadata change updates
the canonical lesson. Reusing the key with a different confidence, target or
action returns `409` without mutation. No generic
`PATCH`/`PUT`, extra lesson action or unversioned duplicate route exists.

## UI workflow

The existing Lessons drawer gains:

- Promote, which requires a valid confidence and a confirmation summarising
  that the lesson will become eligible for future context packs.
- Reject, which requires a confirmation summarising that the lesson will be
  archived and retained for audit.

Buttons are available only for a DRAFT lesson and an authenticated session.
Submission is disabled while one command is in flight. Success replaces the
cached lesson from the server response and removes it from the draft queue when
the active filter requires that. A `409` displays the safe current state and
requires the operator to review it; the UI never silently retries with a new
idempotency key.

Keyboard operation, focus return, error announcement, confidence labelling and
destructive-action confirmation are release gates.

The release suite also treats the writable drawer as a scrollable keyboard
region, returns focus to the bootstrap-token field after refused login, and
checks every confirmation, busy, success and typed failure state in light and
dark modes at `1366x768` and `1024x768`.

## Phase 15 delivery-policy replacement

Phase 15 adds one purpose-specific command inside this same authenticated and
idempotent boundary:

```http
POST /api/v1/delivery-control/policy
```

The strict request contains the complete proposed policy and
`expected_revision`; it contains no actor, product, action, current-state or
runtime-discovery fields. The policy comprises mode, approved policy ceiling,
working budget, review budget, protected Changes Requested reserve, and the
complete risk and component lane-limit arrays. Unknown top-level and nested
fields are rejected. There is no partial policy patch, automatic optimiser,
automatic reconciliation or automatic **1 → 3 → 5 → 7 → 10** ramp command.

The server resolves the local product and operator actor, performs one atomic
policy revision through the Phase 15 application service, and returns the
authoritative policy plus its action receipt. The command changes Atlas
admission authority only: it neither reads nor edits `WORKFLOW.md`, discovers
the configured or occupied Symphony state, changes ticket status, nor starts or
terminates Symphony sessions. `approved_symphony_ceiling` is policy state;
`WORKFLOW.md.agent.max_concurrent_agents` remains the separately governed
configured Symphony ceiling.

The browser confirms the full proposal and expected revision before minting a
fresh idempotency key for a new command. It keeps proposal state separate from
the server snapshot, displays the returned revision and receipt on success, and
refetches before treating policy as current. Stale revision and altered replay
return `409` without mutation: the proposal remains available for inspection,
but the operator must load the current policy and explicitly confirm a new
command with a new key. An ambiguous network or server failure preserves the
unchanged command and key for an explicit same-command retry; it never silently
retries an altered payload. Once a successful revision and receipt have been
returned, a failed refetch blocks another command until authoritative refresh
and never reclassifies the confirmed success as retryable. Session expiry and
security refusal clear write authority while preserving the proposal where safe.

The companion `GET /api/v1/delivery-control` is observational and `no-store`.
Its server-returned policy, truthful sync timestamp, occupancy, decisions, rank
inputs, reasons and indeterminate state remain authoritative. Refetch retains
the last truthful snapshot as visibly stale until replacement. The UI does not
use a client clock, compute admission or occupancy, or infer review availability
from working or presumed Symphony capacity.

Executable component inventory and live-API browser tests keep the boundary
closed: the delivery view has no ticket promote/demote, dispatch, worker
terminate/cancel, Symphony configuration, `WORKFLOW.md` edit, merge, rebase,
optimiser or automatic-ramp control. Store and external-boundary probes prove
that policy revision is the only intended write.

## Explicit non-goals

- Editing or merging lessons.
- Archiving an ACTIVE lesson or re-promoting a stale lesson.
- Generic resource PATCH/PUT endpoints or partial policy patches.
- GitHub writes, Linear writes, plan approval, PR rebase or PR merge.
- Remote deployment, TLS termination, teams, roles, multiple operators,
  password recovery or external identity providers.
- Long-lived browser tokens, localStorage credentials or client-supplied actor
  identity.

## Milestone test

The seeded milestone builds the UI, starts a live writable FastAPI process over
an isolated store, and uses browser, HTTP, CLI and repository observables. It
promotes and rejects through the UI and proves ACTIVE/ARCHIVED storage,
`human` / `operator` receipts and ACTIVE-only context retrieval. It also
exercises hostile Origin and Host, missing/wrong CSRF, strict-content-type
bypass, unauthenticated/expired/revoked sessions, actor injection, duplicate
submission, same/altered replay, ambiguous response retry, browser/CLI and
two-browser races, and receipt failure across restart.

Every refused or failed path proves zero unintended lesson or receipt success.
The Phase 13 route-inventory test fixes that milestone's writable HTTP surface
at session creation, session revocation, lesson promotion and lesson rejection.
Later purpose-specific acceptance-session and complete delivery-policy commands
extend the inventory under this same boundary; they do not create a generic
write route. CI retains no Playwright screenshot, trace or video, and the
canary scan covers browser storage, URLs, generated assets, process output,
response errors and receipts. The full Python,
OpenAPI/client-drift, UI, accessibility and browser gates remain binding.

## Residual risks

Supported operation still uses loopback HTTP, so transport confidentiality and
a `Secure` session cookie cannot be claimed. A process or browser extension
already running as the operator, a compromised operator account, and malware on
the host remain outside this boundary. The milestone is a deterministic
release acceptance suite, not a penetration-test claim, and it does not add
remote deployment, HTTPS termination, multi-user identity, bulk actions,
GitHub writes, Linear writes, acceptance-console actions or merge authority.

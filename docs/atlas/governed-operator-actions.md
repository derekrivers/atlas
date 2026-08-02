# Governed Operator Actions Design (Phase 13)

Status: Planned Phase 13 design authority. Defines the first writable Operator
API and UI slice, the single-operator authentication boundary, server-owned
actor context, idempotent action receipts, and lesson promotion/rejection.

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
must not create a second promotion implementation.

## Threat model and supported topology

The supported deployment remains one operator on one machine with the API
bound to loopback. Remote binding, multi-user identity and externally hosted
operation remain unsupported.

The protected assets are:

- authority to admit a lesson into future context packs;
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

Injected commands receive a transaction-scoped unit-of-work facade with only
domain `get` and `add` operations. `get` detaches every ORM value loaded in the
gateway session before returning the requested row. `add` merges and flushes a
copy without attaching the command-owned object, then detaches the gateway's
loaded values again. A command therefore cannot recover the gateway session
through `inspect(row).session` or `object_session(row)`. The facade exposes no
session and no `flush`, `commit`, `rollback` or `close`, so only the gateway can
end its transaction. The gateway flushes command mutations and receipt
insertion separately for classification, then catches failure from the actual
transaction commit as a receipt-commit failure. Any such failure rolls back
the reservation, mutation and receipt together.

An append-only `OperatorActionReceipt` records:

- receipt and correlation IDs;
- action and target;
- server-resolved actor;
- idempotency key identity and request fingerprint reference;
- before and after `EntityStatus` values where applicable;
- a server-controlled result code from `action_succeeded`, `action_refused`,
  `stale_state` or `action_conflict`;
- default-deny result metadata limited to `changed` (boolean),
  `affected_count` (integer `0..1000000`) and `confidence` (finite float
  `0.0..1.0`);
- created/completed timestamps.

The lesson mutation and successful receipt commit atomically. If the receipt
cannot be persisted, the lesson is not changed. Secrets, full request bodies,
raw evidence payloads, lesson content and exception traces are not copied into
receipts or rendered receipt JSON. Result codes and before/after states are
closed enums rather than free-form command strings. The gateway discards every
unapproved metadata field without inspecting its value; the canonical model,
presentation path and public repository writers revalidate every receipt, so
callers cannot bypass either controlled vocabulary or the metadata default-deny
boundary.

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

Confidence is finite and in the inclusive range `0.0..1.0`. Reject accepts an
empty JSON object. Both commands require the current stored lesson to be
DRAFT. The state transition is a compare-and-set operation; if another browser
or CLI command has already changed the lesson, the loser returns `409 Conflict`
with the safe current lesson representation and performs no mutation.

Outcomes:

| Command | Before | After |
| --- | --- | --- |
| Promote | DRAFT | ACTIVE with operator confidence |
| Reject | DRAFT | ARCHIVED |

An unknown lesson returns `404`. Validation failure returns `422`.
Unauthenticated requests return `401`; authenticated requests failing
origin/CSRF policy return `403`. A replay with the same key and fingerprint
returns the original success response. No generic `PATCH /lessons/{id}` route
exists.

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

## Explicit non-goals

- Editing or merging lessons.
- Archiving an ACTIVE lesson or re-promoting a stale lesson.
- Generic resource PATCH/PUT endpoints.
- GitHub writes, Linear writes, plan approval, PR rebase or PR merge.
- Remote deployment, TLS termination, teams, roles, multiple operators,
  password recovery or external identity providers.
- Long-lived browser tokens, localStorage credentials or client-supplied actor
  identity.

## Milestone test

Against a seeded live API and UI, log in as the configured operator, promote
one DRAFT lesson with confidence and reject another. Prove the final states,
server-owned actor, append-only receipts and context-retrieval effect. Seed a
hostile Origin, missing/wrong CSRF token, duplicate submission, altered replay,
expired/revoked session, stale CLI race and audit-write failure; each must fail
closed with no unintended lesson mutation. The full Python, OpenAPI/client
drift, UI, accessibility and browser suites must pass.

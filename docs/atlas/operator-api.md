# Operator API Design (Phase 10)

Status: Delivered design document for Phase 10, amended by Phase 11 OP-2 for
exactly two additive read routes and by the closed Phase 13 loopback operator
session and governed lesson disposition commands, then by the Phase 14
authenticated acceptance-session resource closed by
`docs/closure/phase-14-closure-report.md`. Describes the HTTP projection surface
delivered by ATLAS-187..191 and the additive governed local resources.

## Purpose and scope

The operator API exposes Atlas operational state to a local operator through a
small, versioned HTTP contract. Its current resources are the ticket board,
ticket count, ticket detail, ticket evidence, epics, lessons, dependency
readiness, critical path, review queue, system status, and local browser
session state. Phase 14 adds exact-head acceptance-session creation, live
readiness, evidence, confirmation and verification. It is a projection of
existing state plus a server-owned browser session boundary, not a new source
of truth and not a second place to implement domain behaviour.

The original Phase 10 operator API was a read-only projection surface. Phase 13
adds authenticated session lifecycle routes, the shared mutation-security
dependency, and exactly two governed lesson commands over the shared lesson
disposition service. No generic resource update route is introduced.

Phase 14 reuses that exact authentication and mutation boundary. Its GET is an
authenticated, no-store observational read; each POST uses the shared
`MutationContextDependency` and `Idempotency-Key`. The API performs no
acceptance state-machine logic: every route dependency calls one typed Phase 14
application service and then one presenter.

## Position in the architecture

`atlas.api` is the highest layer in the package spine: the HTTP entry point and
route-wiring layer. It may depend on lower layers, but no lower layer may depend
on it. FastAPI application construction owns the database lifespan and schema
precondition; resource routers own HTTP paths; dependencies select one
operation; presenters translate returned domain values into response schemas.

`atlas.orchestration` is the home for front-end-shared assembly. The review
queue already follows that boundary: the API calls the orchestration operation
once and presents its `TicketReviewState` results. The Phase 11 board
projection follows the same boundary because `epic_key` requires reading
tickets and epics together. Ticket count, epics, and lessons remain
single-repository projections and therefore do not require orchestration.

A read projection stays a single-source repository read wherever its field set
allows. A field requiring a second source moves the whole projection into an
atlas.orchestration coordinating service, as the review queue already is.
Ticket detail is single-source and therefore carries no epic, evidence,
verification or dependency state.

The API contains no logic: a route dependency makes exactly one service or repository call, then presents. Anything requiring more than one call, a branch on domain state, or cross-layer assembly moves to atlas.orchestration.

For the ticket board, the route dependency makes one call to the board
coordinating service, passing the optional `status` query parameter through.
That service may select one of two ticket repository operations
(`list_by_status` or `list`) before adding the owning epic key. The count route
receives one repository dependency and calls `count` once. These are operation
selections, not domain decisions.

The lessons collection follows the same single-source pattern: the optional
`status` query parameter selects either `LessonRepo.list_by_status` or
`LessonRepo.list`, and the dependency still makes exactly one repository call.
Its response exposes stored `Lesson` fields only. It does not resolve lesson
ticket UUIDs to ticket keys; that would require `TicketRepo` as a second source
and move the whole projection into `atlas.orchestration`.

## Ruled constraints

Merge is always an operator-manual step in GitHub's UI. Atlas never gains PR-merge capability, through this API or otherwise.

Linear status moves remain manual in this phase; the API does not write to Linear.

The server binds to loopback for supported operation. Writable serving is
enabled only with `atlas api serve --enable-writes`; it refuses startup unless
`ATLAS_OPERATOR_TOKEN` satisfies the Phase 13 length and entropy contract and
the bind host is loopback. Remote writable serving is unsupported.

The supported `atlas api serve` default is therefore `127.0.0.1`. The generic
`--host` launch option does not make remote binding a supported deployment in
this phase; using it remotely is outside this design until the stated gate is
satisfied.

## The v1 contract

The HTTP contract is versioned under /api/v1. The version represents the HTTP contract, not the Atlas package version. Response schema fields with a closed value set are typed with the canonical domain StrEnums directly — no parallel API enum copies. A duplicated enum is a maintained copy on a drift timer.

`create_app` mounts the version prefix (`/api/v1`) exactly once, as a
module-level constant in `atlas/api/app.py`. Each implemented router then adds
its resource-local prefix. The route table, including the two Phase 11 OP-2
additions as phase authority, is:

| Method | Path                    | Input            | Response                  | Phase authority |
| ------ | ----------------------- | ---------------- | ------------------------- | --------------- |
| GET    | `/api/v1/tickets`       | optional `status` query parameter | `TicketBoardResponse` | Phase 10; amended by Phase 11 |
| GET    | `/api/v1/tickets/count` | none             | `TicketCountResponse`     | Phase 10 |
| GET    | `/api/v1/tickets/{key}` | ticket key       | `TicketDetailResponse`    | Phase 10 |
| GET    | `/api/v1/tickets/{key}/evidence` | ticket key | `TicketEvidenceResponse`  | Phase 10 |
| GET    | `/api/v1/tickets/{key}/dependencies` | ticket key | `TicketDependenciesResponse` | Phase 10 |
| GET    | `/api/v1/epics`         | none             | `EpicsResponse`           | Phase 11 OP-2 |
| GET    | `/api/v1/lessons`       | optional `status` query parameter | `LessonsResponse` | Phase 10 |
| POST   | `/api/v1/lessons/{lesson_id}/promote` | strict `PromoteLessonRequest` plus `Idempotency-Key` | `LessonDispositionResponse` | Phase 13 |
| POST   | `/api/v1/lessons/{lesson_id}/reject` | strict empty `RejectLessonRequest` plus `Idempotency-Key` | `LessonDispositionResponse` | Phase 13 |
| GET    | `/api/v1/dependencies/critical-path` | none | `DependencyCriticalPathResponse` | Phase 10 |
| GET    | `/api/v1/dependencies/graph` | none | `DependencyGraphResponse` | Phase 11 OP-2 |
| GET    | `/api/v1/reviews`       | none             | `ReviewQueueResponse`     | Phase 10 |
| POST   | `/api/v1/reviews/{pr_number}/acceptance-sessions` | strict repository-slug request plus `Idempotency-Key` | `AcceptanceSessionCreationResponse` | Phase 14 |
| GET    | `/api/v1/acceptance-sessions/{session_id}` | live session cookie | `AcceptanceSessionReadResponse` | Phase 14 |
| POST   | `/api/v1/acceptance-sessions/{session_id}/evidence` | strict empty request plus `Idempotency-Key` | `AcceptanceSessionActionResponse` | Phase 14 |
| POST   | `/api/v1/acceptance-sessions/{session_id}/confirm` | strict minimal confirmation plus `Idempotency-Key` | `AcceptanceSessionActionResponse` | Phase 14 |
| POST   | `/api/v1/acceptance-sessions/{session_id}/verify` | strict empty request plus `Idempotency-Key` | `AcceptanceSessionActionResponse` | Phase 14 |
| GET    | `/api/v1/session`       | session cookie if present | `SessionStateResponse` | Phase 13 |
| POST   | `/api/v1/session`       | strict JSON `SessionLoginRequest` | `SessionLoginResponse` | Phase 13 |
| DELETE | `/api/v1/session`       | live cookie + `X-Atlas-CSRF` | `SessionStateResponse` | Phase 13 |
| GET    | `/api/v1/status`        | none             | `SystemStatusResponse`    | Phase 10 |

An executable FastAPI route-inventory test asserts the complete method/path
set. Beyond session login/revocation and the two lesson commands, the only
writes are the four named acceptance POST actions above. The inventory rejects
acceptance merge, rebase, arbitrary action/command, `PATCH` and `PUT` routes.

Phase 11, by authority of `docs/atlas/operator-ui.md` OP-2, permits exactly
two additive read routes beyond the Phase 10 surface:
`GET /api/v1/epics` and `GET /api/v1/dependencies/graph`. No other v1 routes
enter Phase 11. Ticket results are ordered by plain lexicographic key. Ticket
board items carry `epic_key`, which is null when the ticket has no epic. Epic
results are natural-key ordered (`ATLAS-E1` before `ATLAS-E10`). All lesson
results are creation-ordered, with ID as the deterministic tie-breaker, by
`LessonRepo.list` or `LessonRepo.list_by_status`. Ticket evidence results
preserve the oldest-first order returned by storage. Review results preserve
the order established by the orchestration operation. Dependency graph nodes
are natural-key ordered; `depends_on` edges are ordered by source key, then
target key, using the same natural-key ordering.

`DependencyGraphNodeSchema.status` and `.node_type` deliberately remain plain
strings. A node status spans the ticket, epic, and ADR status enums, while
`node_type` also carries the model's open `target_entity_type` string; inventing
a parallel API enum would narrow the dependency model and create a second
authority.

The acceptance-session create request contains only `repository` in
`owner/repository` form. `ATLAS_ACCEPTANCE_REPOSITORIES` is a comma-separated
server-side allowlist; application construction can inject the equivalent
tuple for tests. Atlas parses and compares owner/name components
case-insensitively while preserving configured spelling. URLs, ports, query
strings, fragments, extra path components and unconfigured slugs are rejected
before a GitHub call, so the field never becomes an SSRF target.

Evidence and verification accept strict empty JSON objects. Confirmation
accepts only `criteria_fingerprint`, `criterion_indexes` and literal
`manual_approval`; actor, token, repository, PR override, ticket key, SHA,
criterion text and unknown fields are rejected. Success returns the updated
safe session plus its receipt. Create returns the safe session plus its durable
hashed creation-command identity. Receipt-backed action errors expose their
canonical `result_code`; stale refusals also return every movement or blocking
`reason`. Evidence timeout, malformed-source, transport, authentication and
rate-limit failures additionally return the complete bounded external-read
reason set without foreign exception text. Gateway-level altered-key and
in-progress conflicts remain distinct through `conflict_code`.

`GET /api/v1/acceptance-sessions/{session_id}` requires the same live session
cookie without mutation-only CSRF or Origin requirements. It calls
`AcceptanceSessionLiveReadinessService.evaluate` exactly once and is always
`Cache-Control: no-store`. Stored history is returned separately from current
`merge_ready`; movement, indeterminate state, timeout, malformed external data
or another read failure closes the current gate with every canonical reason
and performs no session, evidence, receipt, ticket or external-system write.

Acceptance external reads use the server-owned GitHub client with a finite,
positive request deadline (15 seconds by default). Timeout is the named
`external_read_timeout` reason plus `external_state_indeterminate`, and an
`external_timeout` non-advancing action result, never a job or hidden
background continuation. The resource exposes no job ID, polling state,
websocket, server-sent event or merge operation.

`GET /api/v1/tickets` returns the lexicographic-key-ordered operator board. Its
items expose the lean ticket fields needed for board scanning plus `epic_key`,
the owning epic's store key. `epic_key` is null when the stored ticket has no
epic.

`GET /api/v1/epics` returns stored epic records as a single-repository
projection over `EpicRepo.list`. Results are natural-key ordered using
`atlas.core.keys.natural_key`, so `ATLAS-E1` precedes `ATLAS-E10`; the route has
no pagination in Phase 11.

`GET /api/v1/tickets/{key}` returns the stored operator-facing definition and
execution state for one ticket. It is a single-repository projection over
`TicketRepo.get_by_key`; it does not assemble evidence, verification,
dependency, lesson, or epic state.

`GET /api/v1/tickets/{key}/evidence` returns one ticket's stored evidence
records with evidence type, trust tier, status, and a derived system pin-triple
completeness flag; it never exposes raw evidence payloads.

`GET /api/v1/lessons` returns stored lesson records, optionally filtered by the
canonical `EntityStatus`. It is a single-repository projection over
`LessonRepo.list` or `LessonRepo.list_by_status`; it is read-only and never
promotes, rejects, archives, or merges a lesson.

`POST /api/v1/lessons/{lesson_id}/promote` accepts exactly one finite numeric
`confidence` in the inclusive range `0.0..1.0`. Before the domain mutation,
Atlas canonicalises an accepted value to the PostgreSQL `NUMERIC(4,3)` scale
using decimal round-half-up (`0.0004` becomes `0.0`; `0.9999` becomes `1.0`).
The canonical value is used by the lesson row, immutable replay snapshot,
receipt metadata and first success response. The request fingerprint retains
the submitted value, so two different confidence values still conflict even
when they canonicalise to the same stored value.
`POST /api/v1/lessons/{lesson_id}/reject` accepts exactly an empty JSON object.
Both reject actor, status, content and unknown request fields, require
`Idempotency-Key`, and call `LessonDispositionService` once with the actor from
the authenticated mutation context. Success returns the updated
`LessonItemSchema` and a bounded `OperatorActionReceiptSchema`. The receipt
contains only server-owned action, target and `human` / `operator` actor data,
hashed idempotency identity, request fingerprint, bounded result metadata,
before/after status and timestamps; it never returns the raw key, session,
CSRF value, credential, request body or internal exception.

The command presenter maps an unknown lesson to `404`, invalid command input to
`422`, a non-DRAFT or compare-and-set stale lesson to `409`, and a changed
idempotency fingerprint or in-progress owner to `409`. A stale conflict includes
the safe current lesson when the disposition service supplies one. Repeating
the same key and command returns the original `200` lesson and receipt from the
immutable disposition-time safe projection without a second mutation. Later
archive, citation or metadata changes remain in canonical lesson storage but do
not alter that replay. A different confidence, target or action under that key
is a conflict. There is no generic lesson `PATCH` or `PUT`, no extra action
route and no unversioned duplicate.

`GET /api/v1/tickets/{key}/dependencies` returns one ticket's dependency
blockers, reverse dependency readiness impact, and all readiness reasons from
the dependency projection.

`GET /api/v1/dependencies/critical-path` returns the graph-wide critical path
in execution order with per-step and total effort.

`GET /api/v1/dependencies/graph` is the second Phase 11 OP-2 additive read
route. It returns the validated projected dependency graph in one response:
nodes carry `key`, `status`, and `node_type`; edges carry `source`, `target`,
and the canonical `DependencyType`. The graph is built and validated by
`atlas.dependencies`, assembled by `atlas.orchestration`, and presented by the
API. It returns `depends_on` edges only and does not include layout,
coordinates, effort weighting, or rendering hints. It does not authorize any
additional v1 route beyond the two OP-2 additions named here.

`GET /api/v1/status` returns the singleton operator system snapshot: package
version, store schema revision, ticket and evidence counts, the latest
successful PM-sync receipt timestamp, and the latest evidence-pull timestamp.
The response field remains `last_linear_sync_at`, but its value is
`PmSyncReceipt.finished_at` from the latest successful receipt
(`success_definition_changed`, `success_status_only` or `success_zero_action`).
That finish time is sampled after the successful tick body completes, not
copied from its entry time. It is null before the first successful receipt and
ignores `Ticket.linear_synced_at`, which is only a definition-push cursor.

`POST /api/v1/session` accepts only a strict JSON body containing the
bootstrap operator token. Success returns authenticated state, an expiry
timestamp and one CSRF token, while setting the opaque host-only HttpOnly
SameSite=Strict `atlas_session` cookie. `GET /api/v1/session` returns only
authenticated state and expiry metadata. `DELETE /api/v1/session` uses the
same mutation-security dependency as future writes and revokes the exact live
session. Session responses use `Cache-Control: no-store`.

Every mutation route, including session revocation, lesson commands and
acceptance commands, must
resolve `MutationContextDependency`. That dependency requires a
loopback Host, an exact `http://<Host>` Origin, strict
`Content-Type: application/json`, a live `atlas_session` cookie and a matching
`X-Atlas-CSRF` value. The resolved actor is always
`created_by_type: human, created_by_id: "operator"` and cannot be supplied or
overridden by request JSON or headers. Lesson and acceptance commands additionally
require a non-blank `Idempotency-Key` before their application-service
dependency can run.

The API installs no CORS middleware. Session and mutation responses are
`no-store`; all API responses include `X-Frame-Options: DENY` and the CSP:

```text
default-src 'self'; base-uri 'none'; frame-ancestors 'none'; object-src 'none'
```

Closed-value response fields use the canonical domain `StrEnum` types directly:
`TicketStatus`, `TicketType`, `RiskLevel`, `EvidenceType`, `ActorType`,
`EpicStatus`, `EntityStatus`, `LessonCategory`, `VerificationCheckType`,
`EvidenceStatus`, `NotReadyCode`, `DependencyType`,
`AcceptanceSessionBlockingReason`, `AcceptanceSessionLifecycle`,
`AcceptanceSessionStep`, the confirmation validation vocabulary, and the
operator-action outcome vocabularies. FastAPI's OpenAPI
document publishes the members of those canonical enums as the allowed HTTP
values; this contract was delivered by ATLAS-194 and extended by ATLAS-200,
ATLAS-199, ATLAS-201, ATLAS-207, and ATLAS-208.

A keyed v1 resource that does not exist returns `404 Not Found` using FastAPI's
native error body: `{"detail": "<Resource> <key> not found"}`. Collection routes
continue to return successful empty collections. Atlas does not define a
bespoke error envelope in this phase.

Phase 13 release acceptance drives these routes through a live FastAPI process,
including hostile Host/Origin, CSRF, content type, session, actor-injection,
replay, concurrent browser/CLI and receipt-commit failures. Refused envelopes
reach no lesson action. Receipt or store commit failure rolls back the lesson,
reservation and receipt together; restart and repository reads cannot infer an
unaudited success. The complete evidence is recorded in
`docs/closure/phase-13-closure-report.md`.

## Deferred capabilities

- **Additional resource writable commands** — enter only behind the Phase 13
  session, CSRF, origin, idempotency and server-owned actor boundary.
- **Remote authentication and hosting** — remain unsupported; HTTPS, Secure
  cookies, remote origins and deployment topology require a later design gate.
- **Pagination** — enters when measured collection size or response cost shows
  that complete ticket or review projections are no longer operationally
  suitable, with ordering and continuation semantics designed as one contract.
- **Health endpoints** — enter when a managed runtime needs a defined
  liveness/readiness contract rather than relying on process and startup
  preconditions.

## Open questions

A browser-based agent-review step is under operator consideration and is not yet designed. Nothing in this document precludes it; nothing in this phase implements it.

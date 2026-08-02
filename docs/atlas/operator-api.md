# Operator API Design (Phase 10)

Status: Active design document for Phase 10, amended by Phase 11 OP-2 for
exactly two additive read routes and by Phase 13 for loopback operator session
authentication. Describes the HTTP projection surface delivered by
ATLAS-187..191, the Phase 11 additions that follow it, and the Phase 13 session
boundary that gates later writes.

## Purpose and scope

The operator API exposes Atlas operational state to a local operator through a
small, versioned HTTP contract. Its current resources are the ticket board,
ticket count, ticket detail, ticket evidence, epics, lessons, dependency
readiness, critical path, review queue, system status, and local browser
session state. It is a projection of existing state plus a server-owned browser
session boundary, not a new source of truth and not a second place to implement
domain behaviour.

The original Phase 10 operator API was a read-only projection surface. Phase 13
adds authenticated session lifecycle routes and the shared mutation-security
dependency, but no resource mutation. Lesson or other writable commands remain
absent until they can depend on that shared boundary.

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
| GET    | `/api/v1/dependencies/critical-path` | none | `DependencyCriticalPathResponse` | Phase 10 |
| GET    | `/api/v1/dependencies/graph` | none | `DependencyGraphResponse` | Phase 11 OP-2 |
| GET    | `/api/v1/reviews`       | none             | `ReviewQueueResponse`     | Phase 10 |
| GET    | `/api/v1/session`       | session cookie if present | `SessionStateResponse` | Phase 13 |
| POST   | `/api/v1/session`       | strict JSON `SessionLoginRequest` | `SessionLoginResponse` | Phase 13 |
| DELETE | `/api/v1/session`       | live cookie + `X-Atlas-CSRF` | `SessionStateResponse` | Phase 13 |
| GET    | `/api/v1/status`        | none             | `SystemStatusResponse`    | Phase 10 |

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
It is null before the first successful receipt and ignores
`Ticket.linear_synced_at`, which is only a definition-push cursor.

`POST /api/v1/session` accepts only a strict JSON body containing the
bootstrap operator token. Success returns authenticated state, an expiry
timestamp and one CSRF token, while setting the opaque host-only HttpOnly
SameSite=Strict `atlas_session` cookie. `GET /api/v1/session` returns only
authenticated state and expiry metadata. `DELETE /api/v1/session` uses the
same mutation-security dependency as future writes and revokes the exact live
session. Session responses use `Cache-Control: no-store`.

Every mutation route, including session revocation and future lesson
commands, must resolve `MutationContextDependency`. That dependency requires a
loopback Host, an exact `http://<Host>` Origin, strict
`Content-Type: application/json`, a live `atlas_session` cookie and a matching
`X-Atlas-CSRF` value. The resolved actor is always
`created_by_type: human, created_by_id: "operator"` and cannot be supplied or
overridden by request JSON or headers.

The API installs no CORS middleware. Session and mutation responses are
`no-store`; all API responses include `X-Frame-Options: DENY` and the CSP:

```text
default-src 'self'; base-uri 'none'; frame-ancestors 'none'; object-src 'none'
```

Closed-value response fields use the canonical domain `StrEnum` types directly:
`TicketStatus`, `TicketType`, `RiskLevel`, `EvidenceType`, `ActorType`,
`EpicStatus`, `EntityStatus`, `LessonCategory`, `VerificationCheckType`,
`EvidenceStatus`, `NotReadyCode`, and `DependencyType`. FastAPI's OpenAPI
document publishes the members of those canonical enums as the allowed HTTP
values; this contract was delivered by ATLAS-194 and extended by ATLAS-200,
ATLAS-199, ATLAS-201, ATLAS-207, and ATLAS-208.

A keyed v1 resource that does not exist returns `404 Not Found` using FastAPI's
native error body: `{"detail": "<Resource> <key> not found"}`. Collection routes
continue to return successful empty collections. Atlas does not define a
bespoke error envelope in this phase.

## Deferred capabilities

- **Resource writeable commands** — enter only behind the Phase 13 session,
  CSRF, origin and server-owned actor boundary.
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

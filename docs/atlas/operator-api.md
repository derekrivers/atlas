# Operator API Design (Phase 10)

Status: Active design document for Phase 10, amended by Phase 11 OP-2 for
exactly two additive read routes. Describes the read-only HTTP projection
surface delivered by ATLAS-187..191 and the Phase 11 additions that follow it.

## Purpose and scope

The operator API exposes Atlas operational state to a local operator through a
small, versioned HTTP contract. Its current resources are the ticket board,
ticket count, ticket detail, ticket evidence, epics, lessons, dependency
readiness, critical path, review queue, and system status. It is a projection
of existing state, not a new source of truth and not a second place to
implement domain behaviour.

The operator API is a read-only projection surface in this phase. It serves GET endpoints only. Writeable actions are a future phase and do not begin until authentication, actor context, and a threat model land together in the same phase.

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

The server binds to loopback. Any remote binding is gated on the writeable-phase entry criteria (D-1), plus startup refusal when authentication is not configured.

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
| GET    | `/api/v1/status`        | none             | `SystemStatusResponse`    | Phase 10 |

Phase 11, by authority of `docs/atlas/operator-ui.md` OP-2, permits exactly
two additive read routes beyond the Phase 10 surface:
`GET /api/v1/epics` and `GET /api/v1/dependencies/graph`. No other v1 routes
enter Phase 11. Ticket results are ordered by plain lexicographic key. Ticket
board items carry `epic_key`, which is null when the ticket has no epic. Epic
results are natural-key ordered (`ATLAS-E1` before `ATLAS-E10`). Unfiltered
lesson results preserve repository order;
status-filtered lesson results are creation-ordered by
`LessonRepo.list_by_status`. Ticket evidence results
preserve the oldest-first order returned by storage. Review results preserve
the order established by the orchestration operation. Dependency graph nodes
are natural-key ordered; `depends_on` edges are ordered by source key, then
target key, using the same natural-key ordering.

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
version, store schema revision, ticket and evidence counts, and the latest
Linear-sync and evidence-pull timestamps.

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

- **Writeable commands** — enter only when authentication, actor context, and a
  threat model are designed and land together in the writeable phase.
- **Authentication** — enters with the writeable phase as part of that same
  authentication, actor-context, and threat-model boundary.
- **Pagination** — enters when measured collection size or response cost shows
  that complete ticket or review projections are no longer operationally
  suitable, with ordering and continuation semantics designed as one contract.
- **Health endpoints** — enter when a managed runtime needs a defined
  liveness/readiness contract rather than relying on process and startup
  preconditions.

## Open questions

A browser-based agent-review step is under operator consideration and is not yet designed. Nothing in this document precludes it; nothing in this phase implements it.

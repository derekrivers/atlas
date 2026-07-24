# Operator API Design (Phase 10)

Status: Active design document for Phase 10. Describes the read-only HTTP
projection surface delivered by ATLAS-187..191 and sets the constraints for
work that follows it.

## Purpose and scope

The operator API exposes Atlas operational state to a local operator through a
small, versioned HTTP contract. Its current resources are the ticket board,
ticket count, and review queue. It is a projection of existing state, not a new
source of truth and not a second place to implement domain behaviour.

The operator API is a read-only projection surface in this phase. It serves GET endpoints only. Writeable actions are a future phase and do not begin until authentication, actor context, and a threat model land together in the same phase.

## Position in the architecture

`atlas.api` is the highest layer in the package spine: the HTTP entry point and
route-wiring layer. It may depend on lower layers, but no lower layer may depend
on it. FastAPI application construction owns the database lifespan and schema
precondition; resource routers own HTTP paths; dependencies select one
operation; presenters translate returned domain values into response schemas.

`atlas.orchestration` is the home for front-end-shared assembly. The review
queue already follows that boundary: the API calls the orchestration operation
once and presents its `TicketReviewState` results. The ticket board and count
are single-repository projections and therefore do not require orchestration.

The API contains no logic: a route dependency makes exactly one service or repository call, then presents. Anything requiring more than one call, a branch on domain state, or cross-layer assembly moves to atlas.orchestration.

For the existing ticket board, the optional `status` query parameter selects
one of two single repository operations (`list_by_status` or `list`) before the
result is presented. The count route receives one repository dependency and
calls `count` once. These are transport-level operation selections, not domain
decisions.

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
module-level constant in `atlas/api/app.py`. Each router then adds its
resource-local prefix:

| Method | Path                    | Input            | Response                  |
| ------ | ----------------------- | ---------------- | ------------------------- |
| GET    | `/api/v1/tickets`       | optional `status` query parameter | `TicketBoardResponse` |
| GET    | `/api/v1/tickets/count` | none             | `TicketCountResponse`     |
| GET    | `/api/v1/reviews`       | none             | `ReviewQueueResponse`     |

There are no other v1 routes in this phase. Ticket results are ordered by key.
Review results preserve the order established by the orchestration operation.

Closed-value response fields use the canonical domain `StrEnum` types directly:
`TicketStatus`, `TicketType`, `RiskLevel`, `VerificationCheckType`, and
`EvidenceStatus`. FastAPI's OpenAPI document publishes the members of those
canonical enums as the allowed HTTP values; this contract was delivered by
ATLAS-194.

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

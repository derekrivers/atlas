---
title: "Authenticated acceptance-session workflow API"
objective: >-
  Expose the acceptance session, evidence, confirmation and verification
  operations as one authenticated v1 resource whose routes preserve exact-head
  ordering, idempotency and Atlas's thin-API architecture.
context: >-
  The Phase 14 application operations are now typed and API-independent.
  Pre-ruled decisions: D-1 the route set is exactly session create/read plus
  evidence, confirm and verify POST actions under `/api/v1`; there is no merge,
  rebase, generic PATCH or arbitrary command endpoint. D-2 every POST uses the
  Phase 13 authenticated mutation context and idempotency gateway; the GET is
  authenticated, read-only and calls one bounded live-readiness application
  service without a refresh write. D-3 create accepts a repository slug
  validated against configured policy; step routes accept a session ID and
  their minimal strict body. GitHub token, actor, ticket keys and SHAs are never
  browser inputs. D-4 each route dependency makes exactly one
  application-service call and presents typed results. D-5
  operations are synchronous and bounded; timeout is a named non-advancing
  outcome, not a hidden job. D-6 OpenAPI/generated-client drift is binding.
ticket_type: feature
epic_ref: ATLAS-E12
risk_level: high
component: atlas.api
tags:
  - api
  - acceptance
  - authentication
  - exact-head
  - openapi
relevant_docs:
  - "docs/atlas/review-acceptance-console.md"
  - "docs/atlas/operator-api.md"
  - "docs/atlas/governed-operator-actions.md"
depends_on:
  - "inbox-stub-01-operator-session-security.md"
  - "inbox-stub-11-acceptance-verification-readiness.md"
acceptance_criteria:
  - >-
    The v1 route inventory adds exactly:
    `POST /reviews/{pr_number}/acceptance-sessions`,
    `GET /acceptance-sessions/{session_id}`, and POST
    `/acceptance-sessions/{session_id}/{evidence|confirm|verify}`; tests reject
    merge/rebase/PATCH/PUT/arbitrary-action routes.
  - >-
    All POST routes require the shared session/CSRF/Host/Origin/content-type
    security dependency and `Idempotency-Key`; the GET requires a live session,
    is no-store and performs exactly one bounded live-readiness service call
    with no session or external-system mutation.
  - >-
    Create accepts only a configured-policy repository slug. Step schemas
    accept only their documented minimal fields; actor, GitHub token, ticket,
    PR identity override, SHA, criterion text and unknown fields are rejected.
  - >-
    Every route dependency resolves context, makes exactly one typed
    application-service call and presents. No route directly reads
    repositories, invokes GitHub, branches on session state or computes
    freshness/readiness; the GET's service owns its fresh Phase 12 assessment,
    current criteria fingerprint and stored-history composition.
  - >-
    Typed unknown, validation, unauthenticated, security, stale, altered replay,
    external timeout/failure and blocked outcomes map consistently to the
    documented HTTP statuses and bounded body. Movement, indeterminate state or
    GET external-read failure returns `merge_ready=false` with all reasons;
    successful writes return the updated session plus receipt.
  - >-
    Synchronous external operations have configured finite deadlines and
    cancellation-safe non-advancing outcomes. The contract contains no job ID,
    polling state, websocket, server-sent event or background completion claim.
  - >-
    OpenAPI publishes canonical enums and all-reasons schemas, generated
    TypeScript/client runtime metadata regenerate deterministically, and
    existing read routes remain backwards compatible.
non_goals:
  - >-
    No PR merge, rebase, conflict resolution, GitHub token input/write, Linear
    write, ticket status change, generic command endpoint, background job,
    remote hosting or browser UI.
test_requirements:
  - >-
    Fixture-driven API tests cover each route, success and every typed mapping,
    strict request schemas, policy repository validation, security,
    idempotency and bounded timeout with `ATLAS_LIVE_TESTS=0`; after PASSED,
    head/main/criteria movement and GitHub failure before GET return current
    `merge_ready=false` without changing stored session history.
  - >-
    Extend executable route-inventory/no-logic/security dependency tests and
    OpenAPI/client drift tests; seeded extra service/repository calls or a
    forbidden route must fail; seeded Python defects use `assert 1 == 2`
    (B011).
implementation_notes:
  - >-
    Reuse the Phase 13 mutation dependency and presenters. Do not introduce a
    second session/authentication path for acceptance commands.
  - >-
    Keep the repository allowlist/configuration server-side and compare parsed
    owner/repository components; never accept a URL that could become an SSRF
    target.
documentation_requirements:
  - "docs/atlas/operator-api.md"
  - "docs/atlas/review-acceptance-console.md"
definition_of_done:
  - >-
    All seven acceptance criteria are evidenced by named tests; Python,
    OpenAPI/TypeScript drift, UI compile compatibility and doc-linter gates are
    green; canonical API docs land in the same change; the PR title carries
    the minted ticket key.
---

# Authenticated acceptance-session workflow API

The browser receives a narrow state-machine API, not a remote shell over Atlas.

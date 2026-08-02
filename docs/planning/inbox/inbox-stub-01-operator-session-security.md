---
title: "Loopback operator session security and server-owned actor context"
objective: >-
  Establish Atlas's first authenticated browser session so every writable API
  command is bound to the single operator by the server and protected against
  hostile-webpage, CSRF, token-disclosure and accidental-remote-binding risks.
context: >-
  Phase 10 and Phase 11 deliberately delivered read-only surfaces and made
  authentication, actor context and a threat model one indivisible entry gate
  for writes. Phase 13 now opens that gate for lesson disposition only.
  Pre-ruled decisions: D-1 the supported topology remains loopback and
  `atlas api serve` refuses to enable writable routes without a configured
  `ATLAS_OPERATOR_TOKEN`; remote serving remains unsupported. D-2 login accepts
  the token only in strict JSON, compares it in constant time, and creates a
  short-lived server-side session with an opaque host-only HttpOnly
  SameSite=Strict cookie; the credential never enters a URL, log, store,
  generated bundle or browser storage. D-3 the login response returns a
  per-session CSRF token once; the UI keeps it in memory and every mutation
  requires it in `X-Atlas-CSRF`, an allowed Host/Origin, strict
  `application/json` and a live session. D-4 authenticated actor context is
  always `human/operator`, resolved server-side; request models contain no
  actor field and reject unexpected actor-shaped input. D-5 CORS is
  deny-by-default, session/mutation responses are no-store, framing is denied
  and a restrictive CSP applies. D-6 this ticket lands the complete security
  boundary and session lifecycle but no resource mutation.
ticket_type: infrastructure
epic_ref: ATLAS-E12
risk_level: high
component: atlas.api
tags:
  - authentication
  - security
  - operator
  - csrf
  - api
relevant_docs:
  - "docs/atlas/governed-operator-actions.md"
  - "docs/atlas/operator-api.md"
  - "docs/decisions/0009-single-operator-governance.md"
  - "docs/runbooks/operator-environment.md"
depends_on:
  - "ATLAS-187"
  - "ATLAS-221"
acceptance_criteria:
  - >-
    Writable-route startup fails with a named, secret-free precondition error
    when `ATLAS_OPERATOR_TOKEN` is absent or violates the documented
    length/entropy contract, while the existing read-only loopback API remains
    startable; non-loopback writable serving is refused.
  - >-
    `POST /api/v1/session` accepts only strict JSON, compares the configured
    token in constant time, throttles bounded failed attempts, and on success
    creates a short-lived server-side session plus an opaque host-only
    HttpOnly SameSite=Strict cookie and one CSRF token; neither credential nor
    session secret appears in responses beyond the one intended CSRF value,
    logs, URLs, OpenAPI examples, generated code, store fields or errors.
  - >-
    `GET /api/v1/session` returns only authenticated state and expiry metadata,
    and `DELETE /api/v1/session` revokes the exact session; expiry and
    revocation are enforced server-side and subsequent mutation-context
    resolution returns 401.
  - >-
    The mutation security dependency requires an allowed loopback Host and
    exact Origin, `application/json`, the matching `X-Atlas-CSRF` value and a
    live session; missing, cross-origin, null-origin, malformed-content-type
    and wrong-token requests fail before an application-service call.
  - >-
    Successful authentication resolves one immutable command actor with
    `created_by_type=human` and `created_by_id=operator`; request schemas reject
    `actor`, `created_by_type`, `created_by_id` and other unexpected fields,
    and no header can override the actor.
  - >-
    Session and mutation responses use `Cache-Control: no-store`; CORS is
    deny-by-default; framing is denied and the documented CSP is present,
    asserted by executable response-header tests.
  - >-
    The OpenAPI contract declares the session routes and mutation security
    requirements without embedding a usable secret, and generated TypeScript
    client/runtime metadata regenerate without unexplained drift.
non_goals:
  - >-
    No lesson or other resource mutation, role/permission model, multiple
    operators, OAuth/OIDC, password recovery, remote hosting, TLS termination,
    Secure-cookie claim over loopback HTTP, GitHub write, Linear write or PR
    merge.
test_requirements:
  - >-
    Deterministic API/security tests cover successful login, bad-token
    throttling, missing configuration, expiry, revocation, hostile Origin,
    null Origin, Host confusion, CSRF mismatch, simple-form/content-type bypass,
    actor injection and secret-redaction; `ATLAS_LIVE_TESTS=0`.
  - >-
    Add an architecture test proving writable routes cannot bypass the shared
    security dependency and session resolution, while existing read-route
    contains-no-logic tests remain green; seeded defects use `assert 1 == 2`
    (B011).
implementation_notes:
  - >-
    Keep credential comparison and session persistence behind injected
    services. Store only a one-way digest of the opaque session identifier and
    CSRF secret. Use cryptographic randomness and explicit expiry; do not use
    localStorage, a query-string token or client-created actor context.
  - >-
    Make the loopback-HTTP limitation explicit. Do not set a Secure cookie and
    then claim a working HTTP session; remote/HTTPS support is a later design
    that must require transport security and Secure cookies together.
documentation_requirements:
  - "docs/atlas/governed-operator-actions.md"
  - "docs/atlas/operator-api.md"
  - "docs/runbooks/operator-environment.md"
definition_of_done:
  - >-
    All seven acceptance criteria have named tests; the full Python, OpenAPI
    drift, TypeScript and doc-linter gates are green; canonical security docs
    land in the same change; the PR title carries the minted ticket key.
---

# Loopback operator session security and server-owned actor context

One authenticated server session becomes the only source of browser operator
identity.

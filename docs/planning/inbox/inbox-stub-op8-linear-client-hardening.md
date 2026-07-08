---
title: "Linear client hardening: error-body capture, timeout, rate-limit backoff"
objective: "LinearGraphQLClient failures are diagnosable from their own message and a rate-limited tick backs off until the window resets instead of retry-starving the budget."
context: "2026-07-07 incident: the pm scheduler crash-looped for over an hour on an opaque 'HTTP Error 400: Bad Request'. Three live probes were needed to learn what one preserved response body would have said on tick one: Linear returns rate-limit rejections (2500 req/h personal keys) as transport HTTP 400 with the RATELIMITED/429 detail only in the discarded body. Each naive-interval retry then burned ~110 requests before failing, pinning the budget at zero indefinitely. Two pre-existing debt-register findings land here too: _execute uses urlopen with no timeout, and the error body is swallowed."
ticket_type: "tech_debt"
epic_ref: "ATLAS-E6"
acceptance_criteria:
  - "LinearAPIError raised from an HTTPError includes the HTTP status and the response body (truncated to a pinned max length) in its message; proven by a test injecting a fake HTTPError with a JSON body."
  - "Every urlopen call in LinearGraphQLClient passes an explicit timeout from a single module-level constant; proven by a test asserting the call, and by grep: no bare urlopen(request) remains in atlas/linear/."
  - "A response whose GraphQL errors carry extensions.code == RATELIMITED (whether transport 400 or 200-with-errors) raises a typed LinearRateLimitError (subclass of LinearAPIError) exposing the reset duration parsed from extensions.meta.rateLimitResult; a non-rate-limited 400 still raises plain LinearAPIError (the negative case)."
  - "On LinearRateLimitError the scheduler waits until the parsed reset (capped by a pinned maximum) before the next tick, instead of retrying at the base interval; proven with a fake clock — a rate-limited tick followed by a base-interval sleep is the seeded-defect form that must fail."
  - "TickFailure recording and dedup behaviour for non-rate-limit errors is unchanged (existing tests still pass)."
non_goals:
  - "No pull batching, comments-scan scoping, or per-tick request-count reduction — that is the sync request-budget ticket (its own stub, same inbox batch)."
  - "No LINEAR_STATE_MAP or workflow-state query changes."
  - "No retry semantics beyond the rate-limit backoff; Symphony owns intra-session retries, the scheduler owns tick cadence only."
  - "No SDK adoption; the raw urllib client stays."
test_requirements:
  - "Unit tests with fake HTTP layers only (no live Linear calls; ATLAS_LIVE_TESTS=0 at CI parity); at least one negative per new behaviour; the rate-limit parse tested against the verbatim body captured in the 2026-07-07 incident."
definition_of_done:
  - "All acceptance criteria evidenced by named tests; full gate sweep green; the debt-register 'no timeout' finding updated to closed-by-this-ticket in the same change (deletion over annotation for its 'to close' clause)."
---

# Linear client hardening (OP-8)

Preserve the error body, pin a timeout, and give rate limiting a typed,
backoff-aware path. The 2026-07-07 crash-loop incident is the motivation
section: an undiagnosable one-line error, a self-starving retry loop, and
two probes' worth of avoidable forensics. The client must fail closed AND
fail legible.

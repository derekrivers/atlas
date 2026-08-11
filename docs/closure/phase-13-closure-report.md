# Phase 13 Closure Report — Governed Operator Actions

**Status: CLOSED.** Phase 13's first browser-to-store write boundary is
implemented and its release milestone is executable against a built UI, a live
writable FastAPI process and an isolated seeded store. This report becomes the
canonical closure record when the ATLAS-236 closure change lands; Linear
completion and merge remain owned by Atlas verification and the operator.

The closed authority is intentionally narrow: one local operator may promote
or reject a DRAFT lesson. No other domain write, remote topology or external
system authority enters the phase.

---

## 1. What Phase 13 delivered

| Atlas key | Linear key | PR | Delivered |
| --- | --- | --- | --- |
| ATLAS-231 | ATL-422 | #300 | Loopback session security and server-owned actor context |
| ATLAS-232 | ATL-426 | #301 | Atomic idempotency gateway and append-only action receipts |
| ATLAS-233 | ATL-416 | #305 | Shared CLI/HTTP lesson disposition service and compare-and-set transitions |
| ATLAS-234 | ATL-411 | #309 | Authenticated lesson promote/reject API commands and immutable replay snapshots |
| ATLAS-235 | ATL-408 | #312 | Lessons UI confirmation, typed recovery and receipt workflow |
| ATLAS-236 | ATL-424 | closure change | Hostile, concurrent, failure, secret, accessibility and live milestone proof |

ATLAS-231 through ATLAS-235 are `Done` in Linear and their changes are merged.
ATLAS-236 is the closure change and does not mark itself `Done` or merge itself.

## 2. Release milestone evidence

`apps/operator-ui/tests/e2e/writable-surface-milestone.spec.ts` owns the
release-level seam. Its harness:

1. seeds a new named SQLite store through the repository seeder;
2. builds the UI and serves the built bundle through Vite preview on loopback;
3. starts the production writable `atlas api serve` path for ordinary cases;
4. starts a test-only FastAPI factory only when a deterministic clock or
   receipt-failure dependency seam is required;
5. drives browser, HTTP and real `atlas lessons` CLI clients against that one
   store;
6. observes outcomes through the UI, HTTP responses and public repositories;
   and
7. stops the processes and removes only that harness's temporary directory.

The primary browser scenario signs in, reads the full lesson, promotes one
DRAFT with confidence and rejects another. Repository probes then prove:

- the promoted lesson is ACTIVE with the submitted canonical confidence;
- the rejected lesson is ARCHIVED;
- both successes have exactly one receipt attributed to `human` / `operator`;
- the promoted lesson is available to ACTIVE-only context retrieval; and
- the rejected lesson is absent from that retrieval.

The test never rewrites the database to manufacture an outcome. Seed creation
is fixture setup; every disposition is performed through the delivered browser,
HTTP or CLI command path.

## 3. Threat, replay, race and failure matrix

| Case | Real boundary exercised | Required observable |
| --- | --- | --- |
| hostile Origin | live HTTP middleware/dependency | `403`; DRAFT; no action success |
| hostile Host | live HTTP middleware/dependency | refused request; DRAFT; no action success |
| missing and wrong CSRF | live authenticated mutation | `403`; DRAFT; no action success |
| form and wrong content type | live request parsing/security | `415`; DRAFT; no action success |
| unauthenticated request | live mutation dependency | `401`; DRAFT; no action success |
| expired session | deterministic live clock | `401`; DRAFT; no action success |
| revoked session | live session deletion then mutation | `401`; DRAFT; no action success |
| actor field/header injection | strict request schema/server actor | `422` or ignored header authority; no injected attribution |
| concurrent duplicate with one key | live idempotency gateway | one transition and one success receipt |
| same-key replay | immutable stored snapshot | equivalent original success; no second transition or receipt |
| altered replay | fingerprint boundary | typed `409`; no second transition or receipt |
| lost browser response | committed live request then aborted response | explicit same-key retry returns the original success |
| CLI versus browser | real CLI wins between review and submit | browser shows typed stale conflict; one transition and receipt |
| two browser contexts | independent cookies and simultaneous rulings | one success, one typed conflict, one transition and receipt |
| receipt/store commit failure | injected receipt persistence exception | failure response; lesson remains DRAFT; no success receipt |
| restart after receipt failure | same store, restarted live process | still DRAFT and unaudited; no inferred success |
| explicit retry after restart | original command submitted again | one atomic transition paired with one success receipt |

Rejected envelopes assert both lesson state and receipt count. The race cases
assert exactly one terminal domain state and exactly one successful receipt,
not merely that one response happened to succeed.

## 4. Writable route inventory

The executable route inventory in `tests/test_lesson_disposition_api.py`
enumerates every mounted FastAPI method other than read/preflight methods. It
permits exactly:

| Method | Route | State affected |
| --- | --- | --- |
| `POST` | `/api/v1/session` | server-side operator session |
| `DELETE` | `/api/v1/session` | exact live operator session |
| `POST` | `/api/v1/lessons/{lesson_id}/promote` | DRAFT → ACTIVE plus receipt |
| `POST` | `/api/v1/lessons/{lesson_id}/reject` | DRAFT → ARCHIVED plus receipt |

The two lesson commands are the only domain writes. There is no generic
`PATCH`/`PUT`, lesson edit/merge/archive command, unversioned duplicate, GitHub
write, Linear write, plan action, acceptance-console action or merge route.

## 5. Secret and artifact boundary

The milestone registers bootstrap-token, CSRF, actor-injection and injected
failure canaries, then scans all retained or externally visible surfaces
available to the harness:

- localStorage and sessionStorage;
- browser URLs and captured browser console/page-error/request output;
- API process output and CLI output;
- response error bodies and repository receipt projections; and
- generated/built UI assets.

Playwright screenshots, traces and video are disabled in the repository-owned
configuration, including on failure. The suite asserts that no such attachment
is retained. API errors remain typed and secret-free; receipt metadata remains
default-deny and never contains credentials, CSRF, raw idempotency keys, request
bodies, lesson content or exception traces.

## 6. Accessibility and responsive evidence

`apps/operator-ui/tests/e2e/writable-state-accessibility.spec.ts` adds live-API
coverage for the complete writable state machine:

- login and refused-login recovery;
- promotion/rejection confirmation and validation;
- in-flight busy state and duplicate suppression;
- success receipt and polite live announcement;
- security refusal and assertive error announcement;
- concurrent stale conflict and re-review recovery;
- revoked/expired session restoration;
- atomic receipt-failure ambiguity; and
- API-unreachable recovery.

The tests run axe-core's WCAG 2.2 AA tag set, keyboard and focus assertions,
live-region assertions, contrast checks and horizontal-overflow checks at the
established `1366x768` laptop and `1024x768` tablet viewports across light and
dark modes. The closure pass corrected three defects found by this evidence:
failed login now refocuses the token input, confirmation descriptions meet the
contrast threshold under nested overlays, and the lesson drawer's scrollable
viewport is keyboard-focusable.

## 7. Verification contract

The closure worktree runs the same independently named gates as CI:

```bash
ATLAS_LIVE_TESTS=0 uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy atlas tests
uv run lint-imports
uv run python -m atlas.tools.doc_linter
npm --prefix apps/operator-ui run api:check
npm --prefix apps/operator-ui run lint
npm --prefix apps/operator-ui run typecheck
npm --prefix apps/operator-ui run test:acceptance
npm --prefix apps/operator-ui run test:browser
npm --prefix apps/operator-ui run build:bundle
npm --prefix apps/operator-ui run test:e2e
npm --prefix apps/operator-ui run test:a11y
```

The browser commands use the seeded live process and isolated store; they are
not request-mock substitutes. GitHub CI repeats these gates at the exact PR head
and remains the system-tier evidence authority under ADR-0008.

## 8. Residual risks and non-goals

- **Loopback HTTP remains plaintext.** The supported topology cannot claim TLS
  transport confidentiality or a `Secure` session cookie. Remote/HTTPS serving
  requires a later design gate.
- **The host account is trusted.** Malware, a compromised browser extension or
  another process already acting as the operator is outside this boundary.
- **Identity remains single-operator.** There are no accounts, roles,
  delegation, recovery or multi-user audit identities.
- **The suite is deterministic release evidence, not a penetration test.** It
  proves the named threats and invariants and makes no general security claim.
- **Failure artifacts are intentionally sparse.** Disabling credential-bearing
  screenshots/traces/video reduces post-failure visual diagnostics; safe text
  diagnostics and explicit invariant assertions remain available.
- **The supported UI server remains local tooling.** Vite preview proves the
  built asset and same-origin proxy contract; production hosting and HTTPS
  termination remain undesigned.

Phase 13 adds no lesson editing/merging, ACTIVE archival, bulk action, generic
resource update, acceptance console, GitHub write, Linear write, PR merge,
remote deployment, HTTPS termination or multi-user authentication. Phase 14
work is not implemented by this closure change.

## 9. Acceptance-criteria self-assessment

1. **PASS — live promote/reject and retrieval.** The UI produces stored
   ACTIVE/ARCHIVED lessons, confidence, `human` / `operator` receipts and the
   ACTIVE-only retrieval effect through repository observables.
2. **PASS — hostile HTTP and session failures.** Origin, Host, CSRF, content
   type, authentication, expiry and revocation are live-stack cases with zero
   action success.
3. **PASS — replay and concurrency.** Duplicate, same-key, altered-key,
   ambiguous response, two-browser and CLI/browser races prove one transition,
   one success receipt and typed recovery.
4. **PASS — atomic receipt failure.** Injected persistence failure and restart
   prove DRAFT/no receipt until an explicit atomic retry.
5. **PASS — secret-free retained surfaces.** Canary scans and disabled
   Playwright media cover the named storage, URL, output, error, receipt and
   asset boundaries.
6. **PASS — accessibility and responsive states.** Keyboard, focus,
   announcement, contrast and overflow checks cover the complete writable
   state matrix and established viewports.
7. **PASS — canonical agreement and route closure.** The design, API, UI,
   runbook, roadmap and this report state one loopback topology, threat
   boundary, residual HTTP risk and non-goal set; executable inventory proves no
   additional write route.

The phase is closed at this boundary. Any wider authority belongs to a later
governed design and cannot be inferred from the existence of these commands.

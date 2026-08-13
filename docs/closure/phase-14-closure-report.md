# Phase 14 Closure Report — Review Acceptance Console

**Status: CLOSED.** Phase 14's exact-head acceptance workflow is implemented
and its release milestone is executable against the built Operator UI, a live
writable FastAPI process, production application services and canonical
repositories. This report becomes the canonical closure record when the
ATLAS-244 closure change lands; Linear completion and the GitHub merge remain
owned by Atlas verification and the operator.

The closed authority is deliberately advisory. Atlas can prove that one exact
PR head currently satisfies preflight, evidence, human gates and PASSED
verification. It cannot merge, update a branch, move a ticket or continue the
post-merge completion spine.

---

## 1. What Phase 14 delivered

| Atlas key | PR | Delivered |
| --- | --- | --- |
| ATLAS-237 | #299 | Acceptance and confirmation zero-action diagnostics |
| ATLAS-238 | #304 | Immutable exact-head acceptance session and safe historical projection |
| ATLAS-239 | #308 | Canonical exact-head evidence action with freshness checks |
| ATLAS-240 | #307 | Atomic per-criterion confirmation and manual approval action |
| ATLAS-241 | #314 | Exact-head verification and live manual-merge readiness |
| ATLAS-242 | #319 | Authenticated synchronous acceptance-session API |
| ATLAS-243 | #320 | Review queue acceptance console UI |
| ATLAS-244 | closure change | Security, concurrency, live-API and accessibility milestone |

ATLAS-237 through ATLAS-243 are merged. ATLAS-244 is the closure change and
does not mark itself Done or merge itself.

## 2. Release milestone evidence

`apps/operator-ui/tests/e2e/phase-14-milestone.spec.ts` owns the release-level
seam. Its harness:

1. seeds a fresh named SQLite store through the repository seeder;
2. builds and serves the Operator UI on loopback;
3. starts a live FastAPI process with production acceptance services;
4. injects deterministic read-only GitHub, evidence and verification
   boundaries, never a production bypass flag;
5. drives browser and authenticated HTTP clients through the delivered routes;
6. observes sessions, evidence, confirmations, verification checks, receipts,
   ticket state, schema revision and PM-sync history through canonical
   repository probes; and
7. tears down only its temporary process and store.

The primary browser scenario creates an immutable session for seeded PR 412 at
the displayed 40-hex head and live-main base. It pulls current-head evidence,
uses the keyboard to confirm every live criterion and the separate manual gate,
runs the canonical verification operation and receives `merge_ready: true`
only after the stored top-level verdict is PASSED at that exact head.

The success assertions prove the ticket remains `review_required`, no ticket
transition or PM-sync receipt appears, the schema revision and table inventory
are unchanged, no merge evidence exists, and the only new action receipts are
evidence, confirmation and verification successes.

## 3. Movement and old-authority matrix

Both PR-head movement and live-main movement are injected at each seam:

| Seam | Required observable |
| --- | --- |
| before evidence | action refused with every mismatch; session terminal stale; no later reuse |
| during evidence | second freshness assessment observes movement; no step advance |
| before confirmation | no human record is appended; session terminal stale |
| before verification | verifier cannot create current authority; session terminal stale |
| after PASSED, before GET | current `merge_ready: false`; stored session, receipts and checks unchanged |

Criteria drift, old-head machine evidence, old-head confirmations and a missing
human gate are also exercised through the real API and UI. None can satisfy the
new session. Pending, failed, warning, not-applicable, malformed, old-head and
wrong-close-set verification results each return their typed blocker and leave
stored readiness false.

The live-readiness GET is intentionally observational. Post-PASSED movement,
timeout, malformed GitHub data or a failed read returns current false with the
complete bounded reason set and does not rewrite the historical PASSED record.

## 4. Replay, race and failure matrix

| Case | Required observable |
| --- | --- |
| duplicate browser click | one HTTP evidence command and one successful transition |
| same-key replay | original receipt returned; no repeated external work or receipt |
| altered-key replay | typed idempotency conflict; no step advance |
| two browser contexts | one action owner, one success and one refusal; one step receipt reference |
| external timeout | typed timeout plus indeterminate reasons; no session advance; bounded retry |
| malformed evidence source | typed malformed result and reasons; no session advance |
| session/store failure | HTTP failure; no partial lifecycle transition or unaudited success |
| terminal receipt failure | lifecycle/readiness rollback; canonical append-only verifier history may remain historical |
| failed GitHub read after PASSED | current false with typed reasons; no session, receipt or check mutation |

The action-owner guard is server-side. Browser disabled states improve the
interaction but are not the concurrency authority. The delivered exclusion is
synchronous and process-local; distributed ownership and asynchronous jobs are
not claimed.

## 5. Mechanical no-external-mutation proof

The live test factory installs fail-fast traps around every forbidden boundary:

- GitHub merge, pull-request update and ref update;
- Git child-process, rebase and publish entry points;
- Linear create, update and state-transition methods;
- Symphony/PM tick entry points;
- Alembic schema upgrade; and
- child processes or remote network connections from the live API process.

Every attempted call would append a named external-mutation event and fail the
scenario. The successful milestone and all adversarial cases assert that the
event ledger stays empty. Canonical store probes independently assert unchanged
ticket transitions, ticket status, schema revision and PM-sync receipts. Atlas
therefore reaches readiness without acquiring merge or adjacent external
mutation authority.

## 6. Phase 13 security and retained-artifact boundary

The Phase 13 hostile Origin, session, CSRF and strict-JSON contract remains
binding. `tests/test_acceptance_session_api.py` applies the same adversarial
matrix to all four Phase 14 POST forms: create, evidence, confirmation and
verification. Unauthenticated and revoked sessions return `401`; missing or
hostile Origin and missing/wrong CSRF return `403`; form bodies return `415`;
and the typed application services are never called.

Bootstrap-token, CSRF, raw-evidence and unbounded-external-error canaries are
asserted absent from browser text, acceptance API responses, safe repository
projections and API output. Raw evidence remains canonical storage data only;
it never enters the session projection. Playwright trace, screenshot and video
retention is disabled, so a failing writable test cannot create a credential-
bearing media artifact.

## 7. Accessibility and responsive evidence

The live milestone drives the confirmation matrix by keyboard, checks focus
restoration and polite action announcements, verifies the complete canonical
check matrix remains readable, and renders an intentionally long head-ref
identity. Axe-core's WCAG 2.2 AA tag set reports no violations on the ready
state.

Horizontal-overflow assertions cover `1366x768`, `1024x768` and `390x844`.
These release assertions complement the existing Operator UI accessibility
suite across light/dark modes and all delivered views.

## 8. Route and verification contract

The executable acceptance inventory permits exactly:

| Method | Route |
| --- | --- |
| `POST` | `/api/v1/reviews/{pr_number}/acceptance-sessions` |
| `GET` | `/api/v1/acceptance-sessions/{session_id}` |
| `POST` | `/api/v1/acceptance-sessions/{session_id}/evidence` |
| `POST` | `/api/v1/acceptance-sessions/{session_id}/confirm` |
| `POST` | `/api/v1/acceptance-sessions/{session_id}/verify` |

There is no acceptance merge, rebase, arbitrary command, `PATCH` or `PUT`
route. OpenAPI/client drift is checked mechanically. The closure worktree runs
the same independently named gates as CI:

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

The browser commands use a built UI and isolated live process/store. GitHub CI
repeats these gates at the exact PR head and remains the system-tier evidence
authority under ADR-0008.

## 9. Residual risks and non-goals

- **Freeze-to-manual-merge race remains.** The final GET cannot lock GitHub.
  The operator must merge the displayed exact head manually before another PR
  lands; the runbook's one-PR freeze remains binding.
- **Action ownership is process-local.** The synchronous guard proves one owner
  across tabs served by one process. Multiple API workers and distributed locks
  are outside Phase 14.
- **The workflow is synchronous.** There is no queue, job ID, polling protocol,
  websocket or background recovery. The operator refreshes after a bounded
  outcome before retrying.
- **Identity remains local and single-operator.** Phase 13's loopback topology,
  plaintext HTTP risk and host-account trust assumptions remain unchanged.
- **This is deterministic release evidence, not a penetration test.** It proves
  the named threats and invariants and makes no general security claim.

Phase 14 adds no PR merge/rebase, post-merge completion, deployment,
multi-user approval, asynchronous jobs, generic write surface or Phase 15
delivery-control feature.

## 10. Acceptance-criteria self-assessment

1. **PASS — exact-head live milestone.** The built UI and live API/store reach
   current readiness only after exact-head evidence, every human gate and an
   explicit PASSED verdict for the displayed head.
2. **PASS — no external mutation.** Fail-fast client/process traps and
   repository assertions prove no merge, branch, Linear, Symphony, schema or
   PM-sync action.
3. **PASS — movement at every seam.** Head and main movement before/during each
   step fail closed; post-PASSED GET revocation preserves stored history.
4. **PASS — stale authority and non-PASSED results.** Criteria drift, old-head
   records, missing gates and every non-PASSED class remain non-ready.
5. **PASS — replay, concurrency and failure recovery.** Duplicate, same/altered
   replay, two contexts, timeout/malformed data and receipt/store failure prove
   one owner, bounded retry and no partial success.
6. **PASS — inherited security and artifact redaction.** Every Phase 14 POST
   retains Phase 13's hostile-Origin/session/CSRF boundary, and retained
   browser/API observables contain no secret or raw-evidence canary.
7. **PASS — accessibility and canonical agreement.** Keyboard, focus,
   announcement, matrix, long-identity, responsive and WCAG checks pass; the
   roadmap, design, API/UI inventories, runbook, Symphony boundary and this
   report agree on manual merge, synchronous operation and the residual race.

The phase is closed at this advisory boundary. Wider authority requires its own
governed design and cannot be inferred from current `merge_ready: true`.

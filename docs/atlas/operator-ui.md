# Operator UI Design (Phase 11)

Status: Delivered Phase 11 read surface extended by the closed, governed Phase
13 lesson disposition workflow and the Phase 14 review-acceptance console.
Defines the browser surface over the Operator API, the framework adoption
boundary, the bounded authentication/write entries, and the testing contract.
Phase 11 operator rulings recorded in the reviewer session of 2026-07-26 remain
binding where a later governed workflow does not explicitly supersede them.

## Purpose and scope

The operator UI is a browser instrument for reading Atlas operational state and
for two bounded governed workflows: an authenticated operator may promote or
reject a DRAFT lesson, and may drive an exact-head acceptance session through
evidence, confirmation, and verification. It is not a second source of truth
and holds no domain lifecycle, verdict, freshness, or readiness logic; the
generated HTTP contract and the server services remain authoritative.

Every other operator action — approving a plan gate, editing or merging a
lesson, archiving an ACTIVE lesson, merging a PR, or moving a Linear status —
continues to happen in its existing CLI, GitHub, or Linear surface. The UI must
not present affordances that imply otherwise.

## Position in the architecture

The UI lives at `apps/operator-ui/` in this repository (ruled: OP-1). It
is a build-time artifact with no Python import relationship to the
`atlas` package. Its only coupling to Atlas is the HTTP contract at
`/api/v1`, and that coupling is mechanical: TypeScript types are
generated from the FastAPI OpenAPI document, committed, and re-generated
in CI, where any diff fails the build. A UI that compiles is a UI whose
assumptions about the contract are current.

`bootstrap-guide.md` reserves `apps/` for "the phases that need them".
This is that phase.

The UI is where cross-projection assembly happens. `operator-api.md`
rules that a route dependency makes exactly one call and that anything
requiring more moves to `atlas.orchestration`. The ticket detail view
therefore issues three independent requests and composes them
client-side. That is the intended shape, not a workaround: it keeps the
composition in the layer that owns presentation and leaves the API's
contains-no-logic sensor intact.

## The v1 contract this phase adds

Phase 10 ruled "There are no other v1 routes in this phase." Phase 11
adds exactly two read routes, and no more (ruled: OP-2). Both are
read-only, both respect the contains-no-logic rule, and both exist
because a named view is otherwise unbuildable rather than merely
inconvenient.

| Method | Path | Input | Response |
| ------ | ---- | ----- | -------- |
| GET | `/api/v1/epics` | none | `EpicsResponse` |
| GET | `/api/v1/dependencies/graph` | none | `DependencyGraphResponse` |

`GET /api/v1/epics` returns stored epic records as a single-repository
projection over `EpicRepo`. Alongside it, `TicketBoardItemSchema` gains
`epic_key`. That field is the one deliberate exception to the
single-source rule in this phase and it is why the board projection —
not ticket detail — carries it: the board is the view that needs to group,
and `operator-api.md` already permits a projection to move into
`atlas.orchestration` when a field requires a second source. The board
moves; ticket detail does not.

`GET /api/v1/dependencies/graph` returns the projected dependency graph
once — nodes with key, status and node type, and `depends_on` edges. It
is a projection over the existing `atlas.dependencies` graph builder,
assembled by an `atlas.orchestration` coordinating service. It
reimplements no graph logic. Without it a whole-graph view over the live
store is 162 requests, which is not a view.

Nothing else entered v1 in Phase 11. In particular: no pagination, no search
route, no lesson-to-ticket key resolution, no writes, no authentication, and no
health endpoints entered that read-surface milestone.

Phase 13 subsequently adds `GET`, `POST`, and `DELETE /api/v1/session` plus the
two purpose-specific commands `POST /api/v1/lessons/{lesson_id}/promote` and
`POST /api/v1/lessons/{lesson_id}/reject`. Their security, idempotency, response,
and error contracts are canonical in `governed-operator-actions.md`. This is a
bounded extension, not a generic browser mutation capability.

Phase 14 consumes the acceptance-session HTTP contract delivered in Phase 13:
`POST /api/v1/reviews/{pr_number}/acceptance-sessions`, `GET
/api/v1/acceptance-sessions/{session_id}`, and the purpose-specific `evidence`,
`confirm`, and `verify` subcommands. Their exact request/response models,
security boundary, bounded live-readiness evaluation, and error semantics are
canonical in `review-acceptance-console.md`. The browser imports those generated
types and enum values; it does not maintain parallel response models or compute
a transition from them.

## Views

Eight routes, ratified. Each names what it cannot show, because the
absences are contract facts, not backlog items.

### Overview — `/`

Consumes `/status`, `/tickets`, `/reviews`, `/dependencies/critical-path`.

Stat tiles (ticket count, evidence count, review-queue depth, critical
path total effort); a status distribution derived client-side from the
complete board; staleness indicators over `last_linear_sync_at` and
`last_evidence_pull_at`; the head of the critical path.

`last_linear_sync_at` is consumed as the Operator API's truthful
last-successful PM-sync receipt completion timestamp, sampled after the tick
body finishes rather than at tick entry. The Overview must not infer Linear
freshness from ticket definition cursors; before the first successful receipt
the indicator shows the API's null value.

Every aggregate here is derived in the browser from complete
collections. That is a direct consequence of the API having no
aggregation routes and no pagination, and it is the view's stated
fragility: if pagination ever lands, this view breaks first and loudest.

`/status` gets no route of its own. Six scalars do not justify one; they
become this page's header and a persistent footer indicator.

### Board — `/tickets`

Consumes `/tickets` unfiltered, once, and `/epics` once for epic labels
and group metadata.

A sortable, filterable table over key, title, status, ticket type,
priority and risk level — the entire board projection. Faceted
client-side filters, including epic, text search over key and title, and
URL-synced filter state so a filtered board is a linkable artifact.

Two binding behaviours:

- **Natural key sort.** Storage orders by `TicketRow.key`
  lexicographically, which yields `ATLAS-1, ATLAS-10, ATLAS-100, …,
  ATLAS-2`. The UI sorts numerically on the key's numeric segment.
- **Terminal statuses are hidden by default** (ruled: OP-5), with a
  one-interaction reveal. At the time of this design 156 of 162 records
  are `done` or `rejected`; a default that shows all of them is a log
  file rather than an instrument.

The flat table remains the default. `mode=epic` switches the same board route
into a grouped-by-epic board; the mode is carried in the URL like every other
board state. Filters, terminal-status defaults, and natural key sort apply
inside every group. Tickets with no `epic_key` render under an explicit
Unassigned group. Group headings show only metadata returned by `/epics`, and
group counts are derived from the already-fetched, currently visible board rows.

A kanban mode is deliberately absent. Eleven statuses across a board
that is overwhelmingly terminal is a worse instrument than a filtered
table; it may be reconsidered once the board is live and observed.

### Ticket detail — `/tickets/$key`

Consumes `/tickets/{key}`, `/tickets/{key}/evidence`,
`/tickets/{key}/dependencies` as three independent requests.

Tabs: **Definition** (objective, context, acceptance criteria, non-goals,
implementation notes, test and documentation requirements, definition of
done); **Metadata** (type, risk, priority, effort, component, tags,
`source_anchor`, Linear and GitHub external identifiers, timestamps);
**Evidence**; **Dependencies**.

The evidence tab surfaces `has_system_pin_triple` prominently rather than
as a trailing column. Under ADR-0008 that boolean is the difference
between evidence that can close a ticket and evidence that cannot, and
the view's job is to make that legible at a glance.

The dependencies tab renders the readiness verdict, every `NotReadyCode`
reason as human text (the API returns all failing conditions, not the
first), and blockers and `blocked_by` as links.

An unknown key renders the API's native 404 body,
`{"detail": "Ticket <key> not found"}`, without a bespoke error envelope.

### Review queue — `/reviews`

Consumes `/reviews`.

Per item: the verdict, a checks matrix over the seven
`VerificationCheckType` values, and `has_system_evidence` and
`has_pr_merged_evidence` as explicit, prominent pass/fail states. Those
two gates are what strand tickets in practice — the Phase 10 incident
ledger is largely a record of that — so they are rendered as first-class
signals rather than as flags.

The queue itself remains read-only. Each Review Required row links to its
focused acceptance surface at `/reviews/$key/acceptance`; it does not acquire
inline commands or infer whether the row is eligible for a later step.

### Acceptance session — `/reviews/$key/acceptance`

Consumes `/tickets/{key}` and `/reviews` for display context, then creates or
loads one acceptance session through the generated acceptance-session client.
The panel displays the server-pinned repository, PR number, head and base refs
and SHAs, close-set, criteria fingerprint and snapshot, lifecycle, timestamps,
ordered step summaries, bounded evidence summary, verification summary,
canonical check matrix, receipts, and every blocking reason. Raw evidence
payloads are never rendered.

The server lifecycle determines the only primary action: pull evidence,
confirm criteria, run verification, or refresh live readiness. Completed steps
remain inspectable. A synchronous one-action guard disables conflicting
controls, and the browser does not advance until the mutation returns and a
fresh `GET /api/v1/acceptance-sessions/{session_id}` completes. Initial load,
manual refresh, and every post-command refresh use that GET; historical session
readiness and individual check rows never produce a local PASSED verdict or
merge-ready decision.

Confirmation renders every server-snapshot criterion as inert text with an
explicit stable-index checkbox, plus a separate manual-approval checkbox. The
request contains only `criteria_fingerprint`, `criterion_indexes`, and
`manual_approval`; criterion text and actor authority never return to the
server. Evidence rendering is similarly bounded to trust, status, count, and
pin-completeness fields from the generated summary.

Only a current successful GET with `merge_ready=true` opens the manual action
instruction. It names the exact server-verified SHA and tells the operator to
merge that SHA manually in GitHub. The panel has no GitHub action link or merge
button. Refresh-in-flight, head/base/criteria movement, an indeterminate
assessment, or an external-read failure closes the instruction immediately and
shows all server reasons. Movement requires refresh and a new exact-head
session; behind/diverged/conflicted recovery names the operator-owned Phase 12
rebase lane outside this UI. Old command keys are never silently reused.

Security refusal, stale state, replay conflict, timeout, blocked action,
external failure, and session expiry have distinct accessible alerts and
recovery. Only an ambiguous transport outcome retains its idempotency key for
an explicit same-key retry. An unambiguous timeout requires a fresh GET before
a newly keyed command. Sign-out or expiry clears the open session lifecycle;
another tab may observe a session only by loading its ID through a fresh GET.

### Critical path — `/critical-path`

Consumes `/dependencies/critical-path`.

The chain in execution order with per-step and cumulative effort, and the
total. The view states that the critical path is advisory and never gates
dispatch, because `atlas/dependencies/critical_path.py` is explicit that
it does not, and a visualisation that omits that reads as authority.

### Dependency graph — `/dependency-graph`

Consumes `/dependencies/graph` and `/dependencies/critical-path`.

The graph route renders the dependency projection as one client-laid-out
SVG. The API supplies nodes and edges only; coordinates, rank, dimensions
and rendering hints are computed in the browser. Ticket nodes link to
ticket detail. The critical path returned by the dedicated critical-path
route is highlighted in the graph, but the dedicated route remains the
operator's linear path view.

The default filter hides terminal ticket statuses, matching the board's
default. Revealing terminal statuses is a local view toggle. The view
does not cap, sample or paginate the projection; if a future render cap is
introduced, the cap must be stated on screen.

### Lessons — `/lessons`

Consumes `/lessons` unfiltered, with client-side `EntityStatus` facets, and the
two Phase 13 lesson disposition commands for an authenticated ruling.

A table with a detail drawer: category, title, problem, solution,
outcome, confidence, tags, creator, timestamps. Drafts are the default
filter, because DRAFT-until-operator-promotion (ADR-0009) makes the draft
pile the operator's real queue.

Only a lesson whose server-returned status is DRAFT exposes **Promote** and
**Reject**. Promote requires a labelled finite confidence in `0.0..1.0` and a
separate confirmation that ACTIVE lessons may enter future context packs.
Reject uses a distinct destructive confirmation explaining archival. Both
controls disable while the command is in flight. The browser generates one
cryptographic idempotency key for the command lifecycle; an ambiguous response
retains that key for an explicit safe retry, and starting over requires a lesson
refresh first.

Success consumes the server-returned lesson and receipt, updates the exact
Lessons query, and lets the DRAFT facet remove the row without a reload. The UI
never predicts ACTIVE or ARCHIVED. A `401`, timed expiry, or explicit sign-out
invalidates the open decision lifecycle: Atlas clears in-memory write authority,
closes the lesson drawer, resets its confirmation, confidence, mutation, and
command-key state, and refreshes the exact Lessons query. Signing in again never
restores that reviewed drawer; the operator must reopen the refetched lesson and
review it again. A `401` also opens the session-expired flow; `403` names the
security refusal; `409` renders the safe current lesson, refreshes the queue,
blocks overwrite, and requires the drawer to be closed and re-reviewed; `422`
is attached to confidence or the relevant confirmation. Success and failure are
announced through live regions.

Lessons carry `source_ticket_id` and `related_ticket_ids` as raw UUIDs.
Resolving them to ticket keys would require a second source and move the
whole projection into `atlas.orchestration`, which `operator-api.md`
rules out for this projection. The UUIDs are therefore displayed
literally. Fabricating a link would hide a contract gap; showing the
UUID keeps it visible.

### App shell

Sidebar navigation, header, theme toggle, command palette, route-level error
boundaries, a 404 page, an explicit API-unreachable state, and the local operator
session flow.

The header opens an accessible bootstrap-token dialog. The token is posted once
to `/api/v1/session`, then cleared from the form; it never enters web storage,
URL state, query state, logs, or generated configuration. The returned CSRF
token lives only in module memory. A refresh therefore loses browser write
authority even if the HttpOnly server cookie remains valid and presents a
restore-session flow before another ruling. Expiry does the same and requires
the governed lesson or acceptance session to be re-reviewed after sign-in. A
refused login returns focus to the token field, and governed detail viewports
are keyboard-scrollable.

The last is not decoration. A loopback API that is not running is the
most likely failure the operator will meet, and it must produce a named,
actionable message — that the API is not reachable at the configured URL
and that `atlas api serve` may not be running — rather than an empty
page or a generic network error.

### Not buildable in this phase

Agent-run history, PlanRun history, ticket status timelines, debt items, tick
failures, context packs, lesson-to-ticket navigation, plan approval, lesson
editing/merging/ACTIVE archival, bulk disposition, generic resource updates,
GitHub writes, Linear writes, rebase controls, post-merge completion, Symphony
resume, schema upgrade, and PM-sync controls remain absent.

## Framework adoption boundary

The UI adopts `satnaing/shadcn-admin` v2.2.1 (MIT): React 19, TypeScript,
Vite, Tailwind 4, TanStack Router, TanStack Query, TanStack Table, Radix
primitives, and `cmdk`. Recharts, Zustand, and Zod are intentionally omitted
until a shipped view needs them; the adoption boundary is capability-driven,
not a requirement to retain unused template dependencies.

Three rulings govern the adoption:

**It is a component collection, not a starter,** as its own README says. It
ships Clerk authentication and `users`, `chats`, `tasks`, `apps`, `help-center`
and `settings` demo domains built on `@faker-js/faker` fixtures. All of it was
deleted in the scaffold ticket. The later Phase 13 session flow uses Atlas's
loopback session contract directly and does not restore Clerk or any remote
identity dependency.

**Its theme is the theme** (ruled: OP-7). `src/styles/theme.css` is
vendored intact and becomes the single token contract: every colour,
radius and font token lives in that file, and no component carries a
hardcoded token value. There is no separate operator-supplied theme.

Light and dark modes are both part of that contract. The shell defaults to
`system` on first visit, resolves that through `prefers-color-scheme`,
persists explicit light/dark/system choices across reloads, and writes the
browser `theme-color` meta value from `--background` after the active mode is
resolved.

**Its Playwright is not end-to-end testing.** The template uses Playwright
as a browser provider for Vitest component tests. This phase additionally
requires `@playwright/test` end-to-end specs, which are a separate
runner, a separate configuration, and a separate CI job. The two coexist
and are never conflated; a component suite that passes is not acceptance.

## Open-source contribution boundary

The contributor-facing runbook for this phase is
`apps/operator-ui/README.md`. Its named "Operator UI contract limits" section is
the place an outside contributor should check before treating missing pagination,
missing ticket-detail epic data, literal lesson ticket UUIDs, or polling instead
of push as bugs.

That README also records the bounded contribution boundary: lesson disposition
and the acceptance-session step commands are the only browser writes. They
depend on the session, actor-context, idempotency, receipt, exact-head, and
threat-model contracts. No generic or remote operator surface follows from
those exceptions.

The upstream MIT attribution for retained `satnaing/shadcn-admin` source and the
vendored theme lives in `apps/operator-ui/THIRD_PARTY_NOTICES.md`.

## Testing contract

- **Component and unit tests** use the template's Vitest browser-mode
  setup.
- **End-to-end tests** use `@playwright/test` against a real
  built UI preview and a real `atlas api serve` process bound to loopback over
  a seeded SQLite store
  (ruled: OP-4). Fixture replay is not sufficient: the three data-shape
  facts that most affect these views — a board that is overwhelmingly
  terminal, lexicographic key ordering, and UUID-only lesson references —
  are all invisible to a suite that mocks its own expectations.
  The documented command is
  `npm --prefix apps/operator-ui run test:e2e`: the harness seeds a fresh
  SQLite store from
  `apps/operator-ui/tests/e2e/fixtures/live-api-seed.json`, starts
  `atlas api serve` on `127.0.0.1`, runs `@playwright/test`, then tears down
  the API process and temporary store.
  The harness enables the governed write routes with an isolated runtime token;
  browser tests cover promotion, rejection, hostile HTTP envelopes,
  unauthenticated/expired/revoked sessions, same/altered replay, ambiguous
  response retry, stale CLI and two-browser races, atomic receipt failure,
  memory-only session loss on refresh, and forbidden persistence. The milestone
  probes outcomes through the UI/API and repository interfaces rather than
  rewriting the database.
- **Acceptance-console browser evidence** uses a dedicated committed seed and a
  read-only, state-controlled GitHub boundary behind the real FastAPI acceptance
  services and canonical repositories. It covers the successful create/
  evidence/confirm/verify sequence; head and live-main movement before, during
  and after every seam; fresh-GET-only post-PASSED revocation; criteria drift;
  old-head records; every non-PASSED verdict; missing gates; same/altered replay;
  duplicate click; two-context concurrency; timeout/malformed responses; and
  receipt/store failure. Process and external-client traps prove no merge,
  branch, Linear, Symphony, schema or PM-sync action occurs. Component/query
  tests cover every lifecycle and typed error state, one-action-in-flight,
  completed-step inspection, and the prohibition on local readiness derivation.
- **Accessibility and responsive tests** use `@axe-core/playwright` in the
  same seeded live-API harness. The enforced automated standard is axe-core's
  WCAG 2.2 AA rule set, expressed by the `wcag2a`, `wcag2aa`, `wcag21a`,
  `wcag21aa`, and `wcag22aa` tags. CI runs every delivered view in light and
  dark modes, proves a seeded `image-alt` violation is detected, traverses the
  visible keyboard surface with focus assertions, checks data table and tab
  labels, and asserts no horizontal scrolling at the named laptop
  `1366x768` and tablet `1024x768` viewports.
- **Writable-state accessibility** covers login, confirmation, validation,
  busy, success, security refusal, concurrent conflict, revoked session,
  atomic receipt failure and API-unreachable states. It asserts keyboard focus,
  live-region announcement, WCAG contrast and responsive layout across the
  named viewports and both colour modes.
- **Acceptance-state accessibility** drives the live exact-head workflow by
  keyboard, asserts focus and polite state announcements, checks the complete
  verification matrix and long repository/branch identities, runs the WCAG 2.2
  AA rules, and proves no horizontal overflow at laptop, tablet and `390px`
  mobile widths.
- **Contract drift** is caught by regenerating the TypeScript client and
  runtime enum metadata from the running application's OpenAPI document in CI
  and failing on any diff against the committed outputs. The single
  regeneration command is `npm --prefix apps/operator-ui run api:generate`
  from the repository root; the generated files are committed and never
  hand-edited.
- Every view ticket carries at least one end-to-end specification as an
  acceptance criterion. A view without a spec is not delivered.

CI exposes the Operator UI contract as independent required checks:
`lint-operator-ui-openapi`, `lint-operator-ui`, `lint-operator-ui-types`,
`test-operator-ui-acceptance`, `test-operator-ui-components`,
`build-operator-ui`, `test-operator-ui-e2e`, and
`test-operator-ui-accessibility`. The end-to-end and accessibility jobs are
gates, not advisory, and run with the Playwright package and Chromium browser
metadata pinned by `apps/operator-ui/package-lock.json`.

## Development and serving

The Vite development server proxies `/api` to the loopback API (ruled: OP-3).
No CORS middleware is added. Phase 13 retains the same-origin proxy and adds the
exact loopback Host/Origin, HttpOnly SameSite cookie, CSRF, strict JSON, and
idempotency checks defined in `governed-operator-actions.md`.

The proxy target is build-time configurable through
`VITE_ATLAS_API_BASE_URL` and defaults to `http://127.0.0.1:8000`.
Browser requests remain same-origin under `/api`; the configured loopback
URL is surfaced to the operator only when the named API-unreachable state
renders. That state says the API is not reachable at the configured URL
and that `atlas api serve` may not be running.

`VITE_ATLAS_ACCEPTANCE_REPOSITORY` may prefill the server-allowlisted
`owner/repository` selector for acceptance-session creation. It is a display
input to the strict generated request, not a URL, token, or browser authority;
the API validates it against its runtime repository policy.

All Operator UI queries use the shared TanStack Query policy in
`apps/operator-ui/src/api/query-policy.ts`. The polling interval is
30,000 ms and views do not set their own `refetchInterval`; `/status`
timestamps remain the staleness signal rather than a bespoke real-time
transport.

How a production build is served is not designed in these phases. The
development proxy over loopback is the supported path; remote or HTTPS serving
remains a separate security and deployment decision.

Playwright retains no screenshot, trace or video in CI. This prevents a failing
writable test from turning a runtime credential or CSRF value into a retained
artifact; dedicated canaries additionally scan storage, URLs, browser/API
output, response errors, receipts and built assets. The remaining loopback-HTTP
risk is the same one stated by `governed-operator-actions.md`: transport
security and a `Secure` cookie are not claimed, and remote serving is not
supported.

The acceptance action guard is synchronous and process-local; Phase 14 claims
neither distributed exclusion nor asynchronous recovery. A current successful
GET also cannot eliminate movement between that read and the operator's manual
GitHub merge, so the runbook's one-PR freeze remains binding.

## Deferred

- **Other writes** — DRAFT lesson disposition and exact-head acceptance-session
  steps are the only admitted browser mutations. Lesson editing, merging,
  ACTIVE archival, generic updates, GitHub merge/rebase, Linear status,
  Symphony resume, schema upgrade, and PM-sync writes require their own governed
  designs.
- **Remote authentication and multi-operator use** — the delivered session is
  single-operator and loopback-only under ADR-0009.
- **Pagination-aware views** — enter when the API gains pagination. Every
  derived aggregate in the Overview and every client-side facet assumes
  complete collections and must be revisited together at that point.
- **Real-time updates** — polling only in this phase; there is no SSE or
  websocket surface to consume.
- **Remote deployment of the UI** — follows the API's binding decision,
  not ahead of it.

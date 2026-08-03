# Operator UI Design (Phase 11)

Status: Delivered Phase 11 read surface extended by the governed Phase 13
lesson disposition workflow. Defines the browser surface over the Operator API,
the framework adoption boundary, the bounded authentication/write entry, and the
testing contract. Phase 11 operator rulings recorded in the reviewer session of
2026-07-26 remain binding where Phase 13 does not explicitly supersede them.

## Purpose and scope

The operator UI is a browser instrument for reading Atlas operational state and
for the one governed write that Phase 13 admits: an authenticated operator may
promote or reject a DRAFT lesson. It is not a second source of truth and holds no
domain lifecycle logic; the generated HTTP contract and the server disposition
service remain authoritative.

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

## Views

Seven routes, ratified. Each names what it cannot show, because the
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

Read-only. The page answers "what is waiting, and what would block
acceptance"; the operator then acts elsewhere.

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
never predicts ACTIVE or ARCHIVED. A `401` clears in-memory write authority and
opens the session-expired flow; `403` names the security refusal; `409` renders
the safe current lesson, refreshes the queue, blocks overwrite, and requires the
drawer to be closed and re-reviewed; `422` is attached to confidence or the
relevant confirmation. Success and failure are announced through live regions.

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
the lesson to be re-reviewed after sign-in.

The last is not decoration. A loopback API that is not running is the
most likely failure the operator will meet, and it must produce a named,
actionable message — that the API is not reachable at the configured URL
and that `atlas api serve` may not be running — rather than an empty
page or a generic network error.

### Not buildable in this phase

Agent-run history, PlanRun history, ticket status timelines, debt items, tick
failures, context packs, lesson-to-ticket navigation, plan approval, lesson
editing/merging/ACTIVE archival, bulk disposition, generic resource updates,
GitHub writes, Linear writes, and acceptance-console actions remain absent.

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

That README also records the bounded contribution boundary: lesson promote and
reject are the only browser writes, and they depend on the Phase 13 session,
actor-context, idempotency, receipt, and threat-model contracts. No generic or
remote operator surface follows from that exception.

The upstream MIT attribution for retained `satnaing/shadcn-admin` source and the
vendored theme lives in `apps/operator-ui/THIRD_PARTY_NOTICES.md`.

## Testing contract

- **Component and unit tests** use the template's Vitest browser-mode
  setup.
- **End-to-end tests** use `@playwright/test` against a real
  `atlas api serve` process bound to loopback over a seeded SQLite store
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
  browser tests cover promotion, rejection, stale CLI races, duplicate-click
  suppression, memory-only session loss on refresh, and forbidden persistence.
- **Accessibility and responsive tests** use `@axe-core/playwright` in the
  same seeded live-API harness. The enforced automated standard is axe-core's
  WCAG 2.2 AA rule set, expressed by the `wcag2a`, `wcag2aa`, `wcag21a`,
  `wcag21aa`, and `wcag22aa` tags. CI runs every delivered view in light and
  dark modes, proves a seeded `image-alt` violation is detected, traverses the
  visible keyboard surface with focus assertions, checks data table and tab
  labels, and asserts no horizontal scrolling at the named laptop
  `1366x768` and tablet `1024x768` viewports.
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

All Operator UI queries use the shared TanStack Query policy in
`apps/operator-ui/src/api/query-policy.ts`. The polling interval is
30,000 ms and views do not set their own `refetchInterval`; `/status`
timestamps remain the staleness signal rather than a bespoke real-time
transport.

How a production build is served is not designed in these phases. The
development proxy over loopback is the supported path; remote or HTTPS serving
remains a separate security and deployment decision.

## Deferred

- **Other writes** — Phase 13 admits only DRAFT lesson promote/reject. Lesson
  editing, merging, ACTIVE archival, generic updates, approval, GitHub and
  Linear writes require their own governed designs.
- **Remote authentication and multi-operator use** — the delivered session is
  single-operator and loopback-only under ADR-0009.
- **Pagination-aware views** — enter when the API gains pagination. Every
  derived aggregate in the Overview and every client-side facet assumes
  complete collections and must be revisited together at that point.
- **Real-time updates** — polling only in this phase; there is no SSE or
  websocket surface to consume.
- **Remote deployment of the UI** — follows the API's binding decision,
  not ahead of it.

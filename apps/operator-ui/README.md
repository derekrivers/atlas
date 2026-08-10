# Atlas Operator UI

The Operator UI lives at `apps/operator-ui` and uses React 19, TypeScript,
Vite, Tailwind 4, TanStack Router, the shadcn-admin shell, cmdk, Radix UI
primitives, and TanStack Table primitives.

## Run against a real Atlas store

The supported local path is the Vite development server proxying to a loopback
Atlas API. From a cold checkout, use a seeded SQLite store so the UI reaches all
views without depending on private local state.

From the repository root, seed the store:

```bash
mkdir -p .atlas
export ATLAS_DATABASE_URL="sqlite:///$PWD/.atlas/operator-ui-dev.db"
uv run python -m atlas.tools.operator_ui_e2e_seed --db "$ATLAS_DATABASE_URL"
```

In a first terminal, serve the Atlas API over that store:

```bash
export ATLAS_DATABASE_URL="sqlite:///$PWD/.atlas/operator-ui-dev.db"
read -r -s -p 'Choose a 32+ character bootstrap token for the browser sign-in dialog: ' ATLAS_OPERATOR_TOKEN
printf '\n'
export ATLAS_OPERATOR_TOKEN
uv run atlas api serve --enable-writes --host 127.0.0.1 --port 8000
```

In a second terminal, install the UI dependencies and start Vite:

```bash
npm --prefix apps/operator-ui ci
VITE_ATLAS_API_BASE_URL=http://127.0.0.1:8000 npm --prefix apps/operator-ui run dev -- --port 4173 --strictPort
```

Open these routes at `http://127.0.0.1:4173`:

- `/` - Overview
- `/tickets` - Ticket Board
- `/tickets/ATLAS-1` - Ticket Detail
- `/reviews` - Review Queue
- `/critical-path` - Critical Path
- `/dependency-graph` - Dependency Graph
- `/lessons` - Lessons

Browser requests stay same-origin under `/api`; Vite forwards them to the API
configured by `VITE_ATLAS_API_BASE_URL`.

## Automated checks

From the repository root, the cold-checkout core verification command is:

```bash
./apps/operator-ui/scripts/ci.sh
```

The command installs the app dependencies from
`apps/operator-ui/package-lock.json`, regenerates the committed OpenAPI
TypeScript client and fails on drift, then runs lint, type-check, Vitest
acceptance tests, Vitest browser-mode component tests, and the production build.

The end-to-end job is intentionally separate:

```bash
./apps/operator-ui/scripts/ci-e2e.sh
```

That command installs the same pinned dependencies and runs
`npm run test:e2e` and `npm run test:a11y`. Playwright seeds a fresh SQLite
store from
`apps/operator-ui/tests/e2e/fixtures/live-api-seed.json`, starts
`atlas api serve` on loopback, runs the end-to-end specs against the live API,
and tears down the API process and temporary store.

The accessibility stage uses `@axe-core/playwright` with axe-core's WCAG 2.2 AA
tags across every delivered view in light and dark modes. It also holds the
keyboard traversal, table/tab semantics, and laptop `1366x768` plus tablet
`1024x768` no-horizontal-scroll checks.

Regenerate the OpenAPI TypeScript client from the live FastAPI application with:

```bash
npm --prefix apps/operator-ui run api:generate
```

The generated outputs are `apps/operator-ui/src/api/atlas-openapi.ts` and
`apps/operator-ui/src/api/atlas-openapi-runtime.ts`. Do not edit those files by
hand.

## Contributing to the Operator UI

The bounded Phase 13 write surface permits only authenticated promote/reject
rulings for DRAFT lessons. Contributions must not add lesson editing, merging,
ACTIVE archival, bulk disposition, generic mutations, Linear writes, GitHub
writes, approval controls, or disabled controls that imply those actions.

The delivered commands must continue to use the generated API contract, the
memory-only local session, server-owned actor context, strict confirmation,
stable command-lifecycle idempotency, server-returned lesson state and receipts,
and the typed error recovery defined in `docs/atlas/operator-ui.md` and
`docs/atlas/governed-operator-actions.md`.

`apps/operator-ui/THIRD_PARTY_NOTICES.md` records the upstream MIT attribution
for the vendored `satnaing/shadcn-admin` source and the vendored
`src/styles/theme.css` theme. Keep that notice current when touching vendored
source or theme tokens.

## Operator UI contract limits

This is the named contributor-facing record of the known contract limits for
the current bounded operator surface:

- **No pagination.** Collection views consume complete projections. Overview
  aggregates and board facets intentionally assume full `/tickets`, `/reviews`,
  `/lessons`, `/epics`, and `/dependencies/graph` responses.
- **No epic on ticket detail.** Ticket detail is a single-source projection and
  does not carry epic data. Epic labels belong to the board, where
  `epic_key` is already present.
- **Lesson ticket references are not resolved to keys.** Lessons display
  `source_ticket_id` and `related_ticket_ids` as UUIDs because resolving keys
  would require a second source for that projection.
- **Polling, not push.** Shared TanStack Query hooks poll through
  `src/api/query-policy.ts`. There is no server-sent event, websocket, or other
  push contract in this phase.
- **Refresh loses write authority.** The CSRF token is memory-only. A refreshed
  page may retain an HttpOnly server cookie but must complete the restore-session
  flow before another lesson ruling.

The Vite development server proxies same-origin `/api` requests to the Atlas API
URL configured by `VITE_ATLAS_API_BASE_URL`, defaulting to
`http://127.0.0.1:8000`. If that loopback API is not reachable, the shell
renders the named API-unreachable state with the configured URL and the
`atlas api serve` hint. Shared query hooks poll through the central
`src/api/query-policy.ts` interval; views do not set their own polling cadence.

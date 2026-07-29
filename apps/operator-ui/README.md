# Atlas Operator UI

The Operator UI lives at `apps/operator-ui` and uses React 19, TypeScript,
Vite, Tailwind 4, TanStack Router, the shadcn-admin shell, cmdk, Radix UI
primitives, and TanStack Table primitives.

From the repository root, the cold-checkout verification command is:

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
`npm run test:e2e`. Playwright seeds a fresh SQLite store from
`apps/operator-ui/tests/e2e/fixtures/live-api-seed.json`, starts
`atlas api serve` on loopback, runs the end-to-end specs against the live API,
and tears down the API process and temporary store.

Regenerate the OpenAPI TypeScript client from the live FastAPI application with:

```bash
npm --prefix apps/operator-ui run api:generate
```

The generated outputs are `apps/operator-ui/src/api/atlas-openapi.ts` and
`apps/operator-ui/src/api/atlas-openapi-runtime.ts`. Do not edit those files by
hand.

The Vite development server proxies same-origin `/api` requests to the Atlas API
URL configured by `VITE_ATLAS_API_BASE_URL`, defaulting to
`http://127.0.0.1:8000`. If that loopback API is not reachable, the shell
renders the named API-unreachable state with the configured URL and the
`atlas api serve` hint. Shared query hooks poll through the central
`src/api/query-policy.ts` interval; views do not set their own polling cadence.

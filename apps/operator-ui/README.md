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

Regenerate the OpenAPI TypeScript client from the live FastAPI application with:

```bash
npm --prefix apps/operator-ui run api:generate
```

The generated output is `apps/operator-ui/src/api/atlas-openapi.ts`. Do not edit
that file by hand.

This scaffold intentionally fetches no Atlas data. Routes render placeholders
until later Operator UI tickets wire them to the read-only `/api/v1` contract.

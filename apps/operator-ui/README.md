# Atlas Operator UI

The Operator UI lives at `apps/operator-ui` and uses React 19, TypeScript,
Vite, Tailwind 4, TanStack Router, the shadcn-admin shell, cmdk, Radix UI
primitives, and TanStack Table primitives.

From the repository root, the cold-checkout verification command is:

```bash
./apps/operator-ui/scripts/ci.sh
```

The command installs the app dependencies from
`apps/operator-ui/package-lock.json`, then runs lint, type-check, Vitest
acceptance tests, Vitest browser-mode component tests, and the production build.

This scaffold intentionally fetches no Atlas data. Routes render placeholders
until later Operator UI tickets wire them to the read-only `/api/v1` contract.

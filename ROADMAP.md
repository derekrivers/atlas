# ROADMAP.md

The canonical roadmap lives at `docs/atlas/implementation-roadmap.md`.

The immediate milestone is:

> Atlas can read its own docs and generate a dependency-aware backlog
> through the plan/apply loop with stable ticket identity.

The proof commands are:

```bash
atlas plan
atlas apply
```

Milestone acceptance tests AT-1..AT-7 are defined in
`docs/atlas/planning-engine-specification.md`.

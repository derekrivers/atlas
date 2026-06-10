# Knowledge Core Design (Phase 1)

Status: Active design document for Phase 1. Companion to
`data-model-and-schemas.md` (which owns the models); this document owns the
storage, serialisation, and enforcement decisions Phase 1 tickets anchor to.

## Storage architecture

- SQLAlchemy 2.0 typed ORM with Alembic migrations from the first table.
- Default database: SQLite at `.atlas/atlas.db` in the repo root
  (gitignored). Override with `ATLAS_DATABASE_URL`. All column types and
  constraints must be PostgreSQL-compatible; JSONB maps through a JSON
  type shim on SQLite. No SQLite-only features.
- One repository class per aggregate (TicketRepo, EvidenceRepo, …). All
  reads and writes go through repositories; no ad-hoc sessions in CLI or
  engine code.

## Append-only enforcement

`evidence` and `plan_runs` are append-only (ADR-0007/0008). Enforcement is
at the repository layer: their repositories expose `add` and query methods
only — no update, no delete. Tests assert the absence of mutating methods
and that a second record for the same logical event creates a new row.
Database triggers may harden this later; they are not Phase 1 scope.

## Trust-tier enforcement

A single function `evidence_tier(created_by_type) -> {system, human,
agent}` is the only place tier logic lives. `EvidenceRepo.add` rejects any
agent-tier record whose status is not `PENDING` with a typed error. There
is no bypass parameter.

## Planning render format

`docs/planning/` renders must be byte-stable so AT-2 can compare them:

- `epics.yaml`, `tickets.yaml`, `dependencies.yaml`: top-level key is the
  plural entity name; entries sorted by `key` ascending; field order fixed
  to the Pydantic model declaration order; block style; LF endings; UTF-8;
  no anchors/aliases.
- Each entry carries both `key` (human identity, used by the reconciler)
  and `id` (UUID, traceability into the database).
- A generated header comment records `plan_run_id` and the prompt version,
  and states the file is a render written only by `atlas apply`.
- `roadmap.mmd` is a Mermaid `graph TD` of ticket keys with `depends_on`
  edges, epic subgraphs, and status-based classes.

## Key counter

The monotonic ticket-key counter is operational state: a single-row
`key_counters` table (per product key prefix), incremented inside the
apply transaction. The current high-water mark is also written into the
render header comment for operator visibility, but the table is
authoritative.

## JSON Schema generation

`atlas schemas export` writes `model_json_schema()` output for every
canonical model to `docs/generated/schemas/*.json`. The doc linter (v2)
validates every JSON example in canonical docs against these files and
fails CI on drift. Hand-editing `docs/generated/` is banned, same rule as
`docs/planning/`.

## Testing strategy

- Round-trip property tests: model → YAML → model and model → DB → model
  are identity for every entity.
- Render determinism: serialising the same backlog twice is byte-identical.
- Polymorphic integrity: a dependency whose target id resolves to nothing
  is detected by the validation helper (consumed later by graph
  validation).

## Open items

- Whether ContextPack rows belong in the DB only or also as committed
  files (current position: DB only until the Symphony fallback in
  `symphony-integration.md#context-pack-delivery` requires committed
  packs).

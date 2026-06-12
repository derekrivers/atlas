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
- Datetime contract (ATLAS-18): repositories reject naive datetimes with
  a typed error; timezone-aware values are normalised to UTC on write and
  returned UTC-aware on read — round-trip identity is by instant, not by
  offset. The YAML layer (ATLAS-17) preserves offsets; storage owns
  normalisation.

## Append-only and finalise-once enforcement

`evidence` is strictly append-only (ADR-0008): its repository exposes `add`
and query methods only — no update, no delete. `plan_runs` is
insert-plus-single-finalisation (ADR-0007): its repository exposes `add`,
`finalize`, and query methods, where `finalize` rejects any row not in
`proposed` with a typed error and writes only `approved_by`, `applied_at`,
and `failure_reason`. Tests assert the absence of any other mutating method
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
  plural entity name; entries sorted ascending (collation below); field
  order fixed to the Pydantic model declaration order; block style; LF
  endings; UTF-8; no anchors/aliases.
- Keyed entities (epics, tickets) carry both `key` (human identity, used
  by the reconciler) and `id` (UUID, traceability into the database), and
  sort numerically within a shared key prefix (ATLAS-2 before ATLAS-10).
  Dependency entries are keyless (data-model §3.5): they carry `id` and
  sort by (source_ticket_id, target_entity_type, target_entity_id,
  dependency_type).
- A generated header comment records `plan_run_id` and the prompt version,
  and states the file is a render written only by `atlas apply`.
- `roadmap.mmd` is a Mermaid `graph TD` of ticket keys with `depends_on`
  edges, epic subgraphs, and status-based classes.

Scalar and entry conventions (ATLAS-17, `atlas/core/yaml_io.py`): UUIDs
and enums serialise as plain strings (enum values); datetimes as ISO 8601
(UTC as the `Z` suffix, other offsets as `±HH:MM`), preserving the stored
timezone exactly; floats in shortest-repr form. Optional fields are always present, `null` when
unset, so every entry carries the full declaration-order field set.
Multi-line strings emit as literal block scalars for diff readability;
mapping-valued fields emit with sorted keys. Deserialisation is
fail-closed: an unknown key in an entry is a typed error, never ignored.
The render header comment records `plan_run_id`, the prompt version, and
the key-counter high-water mark.

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

Convention: every JSON example in canonical docs is validated for key
existence and type correctness against the generated schemas;
required-field completeness is additionally enforced unless the code fence
is marked ` ```json partial `. Enforcement landed with doc linter v2
(ATLAS-16).

Every ` ```json ` fence in a canonical doc declares its schema in the
fence info string: ` ```json model=<ModelName> `, composing with
`partial` as ` ```json partial model=<ModelName> `. A json fence that is
not a model example must be marked ` ```json no-schema `; the linter
fails any unmarked fence and any `model=` naming a schema absent from
`docs/generated/schemas/`. Type correctness includes declared string
formats (`uuid`, `date-time`); schema constructs the linter does not
recognise fail closed, never skip. The linter also regenerates every
schema in memory and fails when `docs/generated/schemas/` differs from
the regeneration, making the hand-edit ban mechanical. Until the `atlas`
CLI lands (Phase 2), the export entry point is
`python -m atlas.tools.schemas_export`.

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

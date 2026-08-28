---
title: "Make documentation evidence path coverage survive raw-payload retention"
objective: >-
  Preserve exact documentation-path coverage as bounded structured system-tier
  evidence even when the upstream GitHub payload exceeds the raw-payload
  retention cap, including append-only recovery of already-capped observations.
context: >-
  GitHub documentation normalisation already computes the exact changed docs
  paths, but DOCUMENTATION_UPDATE persists them only inside
  raw_payload["files"]. EvidenceRepo replaces payloads over 64KB with a
  retention marker that has no files list, so documentation verification cannot
  prove required-path coverage and CI handoff classifies exact-head evidence as
  malformed_evidence. PR #368 for ATLAS-282 is blocked in CI Pending by this
  defect, and its legacy docs:<head> observation already deduplicates unchanged
  pulls.
ticket_type: "bug"
epic_ref: "ATLAS-E8"
risk_level: "high"
component: "atlas.evidence"
tags:
  - "evidence"
  - "documentation"
  - "retention"
  - "ci-handoff"
  - "append-only"
relevant_docs:
  - "docs/atlas/evidence-pipeline.md"
  - "docs/atlas/verification-engine.md"
  - "docs/architecture/data-model-and-schemas.md"
depends_on: []
acceptance_criteria:
  - >-
    DOCUMENTATION_UPDATE can durably retain a bounded, deterministic, exact set
    of changed repository-relative docs paths outside raw_payload, and invalid,
    contradictory, empty, or out-of-contract structured paths fail closed.
  - >-
    A GitHub documentation payload larger than 64KB may still be replaced by
    the existing retention marker while its structured docs-path projection
    remains available; the canonical full-payload hash, commit SHA, source
    provenance, system-tier actor, and raw-payload cap are unchanged.
  - >-
    New documentation observations use the versioned synthesized source identity
    docs:v2:<head>, so one fresh pull at an unchanged already-observed head can
    append a distinct structured record while repeated identical v2 pulls
    deduplicate normally by external_run_id and payload_hash.
  - >-
    Existing Evidence rows remain append-only and unmodified: legacy small
    DOCUMENTATION_UPDATE records without structured paths may prove coverage
    from a valid retained raw_payload["files"] projection, while legacy capped
    rows lacking both forms remain unprovable by themselves.
  - >-
    Documentation verification treats valid structured docs paths as authority
    for new-format evidence, preserves exact required-path matching and
    exact-head/system-tier pinning, and never fabricates unavailable coverage.
  - >-
    CI-handoff documentation classification passes when a valid current-head
    structured projection proves the requirement and does not call that record
    malformed merely because raw_payload was capped; genuinely malformed or
    absent structured and legacy path evidence still produces the existing
    fail-closed hold.
  - >-
    A regression reproduces the PR #368 class with docs file metadata or patch
    content exceeding 64KB, proves raw_payload is capped, persists exact
    structured path coverage, and obtains a PASSED documentation check and CI
    handoff result for the required path without rewriting the legacy row.
non_goals:
  - "Do not repair PlanningExecution semantics or change the implementation in PR #368."
  - "Do not change delivery policy, PM admission budgets, Symphony configuration, WORKFLOW.md, or Linear workflow ownership."
  - "Do not change PlanRun, semantic/vector/context work, ATLAS-253, or ramp work."
  - "Do not redesign generic raw-payload retention or require raw patch bodies for documentation evaluation."
  - "Do not delete, update, backfill, or reinterpret historical Evidence rows as containing paths they no longer retain."
  - "Do not weaken system-tier trust, exact-head pinning, payload provenance, or CI-handoff ownership."
test_requirements:
  - >-
    Extend tests/test_evidence_model.py and tests/test_storage_schema.py for the
    nullable bounded docs_paths contract, schema round-trip, and migration
    compatibility without historical-row rewrites.
  - >-
    Extend tests/test_evidence_mapping.py for deterministic structured path
    mapping, the docs:v2:<head> identity, preserved payload_hash/provenance, and
    first-pull append plus repeated-pull dedup.
  - >-
    Extend tests/test_storage_repos.py with an over-64KB DOCUMENTATION_UPDATE
    proving raw_payload is capped while docs_paths round-trips unchanged and the
    old capped record remains immutable.
  - >-
    Extend tests/test_documentation_check.py for new structured authority,
    legacy-small fallback, legacy-capped non-proof, exact matching, and
    malformed structured evidence that never raises or passes.
  - >-
    Extend tests/test_ci_handoff_reconciliation.py for a valid structured
    current-head pass beside capped legacy history and for malformed or absent
    path projections retaining the typed fail-closed hold.
implementation_notes:
  - >-
    Add a nullable, bounded docs_paths structured field to the Evidence model,
    evidence table, repository round-trip, and generated schema through the next
    Alembic migration. Null denotes legacy/unavailable projection; a new-format
    DOCUMENTATION_UPDATE requires a non-empty canonical projection. Do not
    backfill or mutate historical rows.
  - >-
    Carry NormalisedDocs.docs_paths through mapping and ingestion independently
    of raw_payload. Preserve payload_hash as the hash of the canonical full
    upstream documentation subset before retention and retain existing commit,
    source, product, and system-actor pins.
  - >-
    Change the synthesized documentation external_run_id from legacy
    docs:<head> to docs:v2:<head> for structured observations. The new identity
    plus the unchanged payload_hash permits one append-only recovery record at
    an already-observed head; subsequent identical v2 observations use the
    existing dedup contract.
  - >-
    Make documentation evaluation prefer and validate new-format docs_paths,
    with raw_payload["files"] used only as the explicitly supported fallback for
    legacy small evidence. Share that projection semantics with CI-handoff
    classification so a valid structured record is not poisoned by capped
    historical evidence at the same head.
  - >-
    Define explicit finite path-count and per-path length bounds using existing
    repository-relative path conventions. Reject malformed structured values
    rather than truncating, guessing, or estimating coverage.
documentation_requirements:
  - "docs/atlas/evidence-pipeline.md"
  - "docs/atlas/verification-engine.md"
  - "docs/architecture/data-model-and-schemas.md"
definition_of_done:
  - "The Evidence model, storage schema, mapper/ingest path, documentation evaluator, and CI-handoff projection implement one coherent structured docs-path contract."
  - "An already-capped exact-head documentation observation can be re-observed append-only through docs:v2:<head>, with the legacy row preserved and repeat pulls idempotent."
  - "Focused model, migration/storage, mapping, evaluator, and CI-handoff regressions pass, including the greater-than-64KB PR #368 failure class."
  - "Canonical evidence, verification, and data-model documents describe the new projection, legacy compatibility, retention, and versioned recovery identity."
---
<!-- atlas-source-comment-id: 960a5968-5699-4140-9f32-c358f80f49ac -->
# Make documentation evidence path coverage survive raw-payload retention

Source issue: ATLAS-282 (Linear ATL-461)

Source comment: `960a5968-5699-4140-9f32-c358f80f49ac`

PR #368 exposed a retention-boundary defect in the system-tier documentation
evidence contract. GitHub normalisation knew the required documentation paths,
but the only durable copy was discarded when the raw payload exceeded 64KB.
This planning input owns the bounded structured projection and the append-only
versioned re-observation needed to make that exact-head evidence evaluable.

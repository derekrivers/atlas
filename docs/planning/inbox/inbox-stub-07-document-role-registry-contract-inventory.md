---
title: Document Role Registry contract and compatibility inventory
objective: Define the deterministic registry schema/loader and commit a reviewed inventory that can represent Atlas document participation while exactly describing today's static planner corpus.
context: Today's ingestion policy is implicit in four root paths and broad globs, including the nested docs/atlas/playbooks path because fnmatch crosses directories. Wave 1 must make roles explicit without narrowing any corpus, anchor or context behavior. The refreshed corpus is 38 committed documents; active and processed planning stubs remain lifecycle inputs rather than permanent static registry entries.
ticket_type: feature
epic_ref: ATLAS-E3
risk_level: high
component: document-role-registry
tags:
  - knowledge-context-wave-1
  - planning
  - document-authority
  - registry
source_anchor: docs/atlas/knowledge-context-consolidation.md#8-compatibility-mode-first
relevant_docs:
  - docs/atlas/knowledge-context-consolidation.md
  - docs/atlas/planning-engine-specification.md
  - docs/atlas/context-renderer.md
depends_on: []
acceptance_criteria:
  - A versioned schema and loader represent authority_class, planner_profiles, new_anchor_allowed, anchor_resolution, context_resolution, historical_only and owner for each governed static document, with closed bounded values and named validation errors.
  - A committed compatibility inventory lists exactly the current 38-document static planner corpus in its current deterministic order, including accepted ADR filtering and the nested docs/atlas/playbooks/linear-sync.md path.
  - Every compatibility entry participates in the compatibility planner profile and preserves today's new-anchor, legacy-anchor and Context Renderer eligibility; no document is marked preservation-only or removed in Wave 1.
  - Registry version, canonical bytes and SHA-256 digest are deterministic, insensitive only to schema-declared set ordering, and change for any material role/path/order change.
  - Duplicate paths, ambiguous owners/profiles, contradictory historical/new-anchor settings, unsafe paths, unknown fields and unsupported schema versions fail closed.
  - The contract explicitly separates the static governed inventory from active inbox stubs and processed durable-stub anchors, whose existing lifecycle enumeration remains supported and uncatalogued per-file.
non_goals:
  - No planner, anchor or Context Renderer consumption; no PlanRun provenance; no corpus reduction; no programme-document move; no active/preservation split; no semantic freshness or current-state projection.
test_requirements:
  - Focused tests cover valid load, all field vocabularies, deterministic canonical bytes/digest, material-change sensitivity, duplicate/contradictory entries and the exact reviewed 38-path ordered inventory.
  - Tests prove registry parsing performs no Git, filesystem write, database, network, model or external-system mutation.
implementation_notes:
  - Expected paths are a small atlas/planning document-role contract/loader module, one packaged versioned registry data file and focused tests/test_document_role_registry.py.
  - The registry serialization/location is the bounded implementation decision owned here; prefer repository conventions for packaged deterministic registries.
documentation_requirements:
  - docs/atlas/planning-engine-specification.md
definition_of_done:
  - The schema and reviewed compatibility inventory are deterministic and fully tested, all 38 current static inputs are represented once in current order, and no runtime consumer or behavior change is included.
---

# Document Role Registry contract and compatibility inventory

This ticket classifies the current corpus but does not yet select from the registry.

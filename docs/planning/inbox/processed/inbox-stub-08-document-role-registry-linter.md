---
title: Closed-world Document Role Registry linter and compatibility proof
objective: Add deterministic validation that proves the registry inventory equals today's governed planner corpus and fails closed when a governed planning document is uncatalogued.
context: A reviewed registry file is insufficient if future broad-glob additions can enter planning silently or if inventory order drifts. This ticket gives compatibility mode a repository-owned linter/equivalence surface before any planner consumer changes.
ticket_type: infrastructure
epic_ref: ATLAS-E3
risk_level: high
component: document-role-registry-linter
tags:
  - knowledge-context-wave-1
  - planning
  - document-authority
  - lint
source_anchor: docs/atlas/knowledge-context-consolidation.md#8-compatibility-mode-first
relevant_docs:
  - docs/atlas/knowledge-context-consolidation.md
  - docs/atlas/planning-engine-specification.md
  - docs/runbooks/local-development.md
depends_on:
  - inbox-stub-07-document-role-registry-contract-inventory.md
acceptance_criteria:
  - The repository documentation/planning linter compares the registry compatibility inventory with the legacy committed corpus enumerator and requires exact path-set and order equality.
  - Adding or moving an otherwise governed root, accepted ADR, docs/atlas or docs/domain Markdown document without a registry entry fails with the exact uncatalogued path before any model call.
  - Removing, duplicating or reordering an entry, changing accepted-ADR participation, or omitting the currently nested playbook path produces a named deterministic failure.
  - The compatibility proof records the registry version/digest and verifies current static document blob bytes/identities, valid new-anchor choices and participation flags are unchanged.
  - Active inbox and processed stubs are checked through their existing lifecycle/integrity rules and are not falsely required as permanent static registry entries.
  - The linter is read-only, network-free and deterministic from one Git HEAD.
non_goals:
  - No planner/context runtime selection, PlanRun schema, database migration, corpus narrowing, anchor algorithm change, programme split, semantic freshness or automatic registry rewrite.
test_requirements:
  - Table-driven linter tests seed uncatalogued, missing, duplicate, reordered, nested and ADR-status cases and prove each failure names the exact cause.
  - A golden compatibility test pins the current path order, document identities and anchor-choice equivalence at a fixture HEAD without hard-coding current blob SHAs as timeless authority.
implementation_notes:
  - Expected paths include atlas/tools/doc_linter.py or a focused registry linter adapter, the document-role loader and tests/test_document_role_linter.py plus existing doc-linter contract tests.
  - "One seam: closed-world validation. It may compare to legacy enumeration but must not yet replace production selection."
documentation_requirements:
  - docs/atlas/planning-engine-specification.md
  - docs/runbooks/local-development.md
definition_of_done:
  - The normal repository linter proves exact compatibility and rejects an uncatalogued governed document, all seeded defects bite, and planner/context behavior remains unchanged.
---

# Closed-world Document Role Registry linter and compatibility proof

Compatibility is mechanically proven before the registry becomes a runtime selector.

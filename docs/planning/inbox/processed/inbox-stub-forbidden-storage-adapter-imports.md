---
title: "Forbidden import contracts: storage must not import the Linear or GitHub adapters"
objective: "Close a permitted edge in the layer spine mechanically: the single layers contract places atlas.storage above atlas.linear and atlas.github, so persistence code is currently allowed to import the external-service adapters. No such import exists today; make the absence a live sensor before one appears."
context: "Surfaced by the API-phase external assessment and verified against the tree: the layers contract permits storage -> linear and storage -> github edges that would invert the desired ports-and-adapters relationship. Pre-ruled decisions: D-1 exactly two [[tool.importlinter.contracts]] entries of type 'forbidden' — 'Storage must not import Linear adapter' (source atlas.storage, forbidden atlas.linear) and 'Storage must not import GitHub adapter' (source atlas.storage, forbidden atlas.github) — added after the layers contract; the spine order itself is untouched. D-2 tests/test_import_linter_contract.py is extended following its own ATLAS-114 pattern: a kept-on-current-tree guard and a demonstrably-fires guard per new contract, so a contract that never fails cannot count as a sensor. D-3 no ports/adapters restructuring, no atlas.ports package, no code moves — contract-only. D-4 no further forbidden contracts beyond the two named; other candidates are either already enforced by the layers stack or belong to future phases."
ticket_type: "tech_debt"
epic_ref: "ATLAS-E5"
acceptance_criteria:
  - "`uv run lint-imports` passes with both new contracts named in its output alongside the layer spine."
  - "Seeded probe per contract: a temporary `from atlas.linear import ...` (then `from atlas.github import ...`) in a module under atlas/storage/ makes lint-imports fail naming the new contract; seeds are removed before the final commit and the fires-guard tests reproduce the probe permanently."
  - "The diff is limited to pyproject.toml and tests/test_import_linter_contract.py."
  - "Full gate sweep green with ATLAS_LIVE_TESTS=0; enumeration pins unchanged."
non_goals:
  - "No changes to the layers contract or spine order. No ports package. No code moves. No contracts beyond the two named."
test_requirements:
  - "Follows the ATLAS-114 three-guard pattern; seeded defects use assert 1 == 2 (B011) where applicable; ATLAS_LIVE_TESTS=0."
definition_of_done:
  - "All acceptance criteria evidenced by named tests; full gate sweep green; PR title carries the minted key."
---

# A permitted edge is a pending import

The contract that would catch it does not exist yet; this ticket is
that contract, plus the proof it bites.

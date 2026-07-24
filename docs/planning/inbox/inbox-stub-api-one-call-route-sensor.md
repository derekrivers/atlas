---
title: Enforce the API no-logic rule mechanically in the architecture test
objective: 'Turn the operator-ratified route rule into a sensor: a route dependency in atlas/api/ makes exactly one service or repository call, so coordination cannot creep into the HTTP adapter unnoticed.'
context: 'The rule is canonical in docs/atlas/operator-api.md (D-2): ''The API contains no logic: a route dependency makes exactly one service or repository call, then presents. Anything requiring more than one call, a branch on domain state, or cross-layer assembly moves to atlas.orchestration.'' A documented rule with no sensor is a wish; get_ticket_board() in atlas/api/dependencies.py already branches on repo selection, which is the first millimetre of the leak the rule exists to prevent. Pre-ruled decisions (operator-ratified in reviewer session 2026-07-24; land them, do not relitigate): D-1 the check is AST-level in tests/test_api_architecture.py, following the existing test_cli_remains_a_thin_presentation_layer pattern in tests/test_import_linter_contract.py. D-2 the sensor must demonstrably fire: a seeded second service call in a route dependency fails it, reproduced as a permanent guard test. D-3 if an existing dependency function violates the rule, the plan gate decides - either the ticket includes moving it to atlas.orchestration, or it is carved out with a named follow-up; the agent proposes and does not decide silently. RATIFIED CARVE-OUT (operator ruling, ATLAS-033M gate OP-1; land it, do not relitigate): docs/atlas/operator-api.md records that the ticket board''s optional `status` query parameter selecting between `list_by_status` and `list`, and the count route''s single `count` call, are transport-level operation selections, not domain decisions. The sensor MUST allow parameter-driven selection between repository operations on the same resource. It must still fire on genuine coordination: two different services or repositories called in one dependency, a branch on domain state (status values, verdicts, readiness), or cross-layer assembly. A sensor that cannot distinguish these two cases is not ready — state how yours does at the plan gate.'
ticket_type: tech_debt
epic_ref: ATLAS-E12
risk_level: low
component: api
acceptance_criteria:
- The sensor passes on the current tree (after any carve-out or move approved at the plan gate).
- The sensor demonstrably fires on a seeded extra service call in a route dependency, reproduced as a permanent guard test.
- The rule's canonical wording in docs/atlas/operator-api.md is cited in the test docstring.
- 'The carve-out is proven both ways: the existing get_ticket_board parameter-driven selection passes, while a seeded dependency calling two different repositories, and one branching on a domain status value, both fire.'
non_goals:
- No new endpoints or behaviour changes. No refactor of atlas/orchestration. No import-linter contract changes.
test_requirements:
- AST-based, fixture-free where possible; ATLAS_LIVE_TESTS=0; seeded defects use assert 1 == 2 (B011).
definition_of_done:
- All acceptance criteria evidenced by named tests; full gate sweep green; PR title carries the ticket key.
---

# Enforce the API no-logic rule mechanically in the architecture test

Minted from the reviewer session of 2026-07-24; decisions in `context` are operator-ratified.

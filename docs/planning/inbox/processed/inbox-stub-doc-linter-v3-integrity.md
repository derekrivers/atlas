---
title: 'Doc-linter v3: PATH and PHASE documentation-integrity checks'
objective: 'Extend atlas/tools/doc_linter.py with two check families that make documentation integrity executable: PATH (backticked repository-path references in active canonical docs must resolve) and PHASE (phase-status consistency between the roadmap, closure reports and ROADMAP.md).'
context: 'The v2 LINK check validates only relative .md link targets, which is how README.md''s dead backticked path `tools/run_planner.py` survived nine phases until ATLAS-192 removed it by hand. v3 closes that class. Pre-ruled decisions (operator-ratified in reviewer session 2026-07-24; land them, do not relitigate): D-1 PATH: backticked spans that parse as repository paths must resolve at HEAD. The heuristic and its carve-outs (module dotted paths, command lines, glob patterns, docs/archive/, the inbox carve-outs the LEGACY check established) are proposed at the plan gate; default constraint is fail closed on anything that parses as a path, with an explicit carve-out list rather than cleverness. BINDING CARVE-OUT, not open to proposal: files under docs/closure/ are terminal records and are fully exempt from PATH - they legitimately reference retired artefacts (phase-2-closure-report.md records the retired run_planner.py harness), the same semantics as roadmap ''Retired:'' lines. PHASE still reads docs/closure/; only PATH exempts it. D-2 PHASE: every roadmap phase section marked CLOSED has a closure report under docs/closure/, every closure report has a roadmap section marked CLOSED, and ROADMAP.md''s current-phase claim names a phase section that exists. Parse anchors (what counts as a phase heading, a status line, how fractional phases such as 2.5/3.5 map to filenames, and that smoke-b reports are not phase closures) are proposed at the plan gate; an unrecognised status line fails closed with a PHS code. D-3 house conventions: follow the existing Finding dataclass, check-function and finding-code patterns (PTH00x / PHS00x continuing JSN00x / GEN001). Validators return findings and never raise. The Phase 10 roadmap section now exists (ATLAS-034M, #238) with ''Status: IN PROGRESS.'', so the PHASE check has a live in-progress section as well as nine closed ones to reason about; an in-progress phase has no closure report and must not be treated as a violation.'
ticket_type: feature
epic_ref: ATLAS-E1
risk_level: medium
component: tooling
acceptance_criteria:
- '`uv run python -m atlas.tools.doc_linter` exits 0 on the current tree; any true findings beyond seeded fixtures are reported as follow-ups, never fixed in this diff.'
- 'Negative fixtures prove each new code bites: a backticked path to a missing file fires PATH; a CLOSED phase without a closure report fires PHASE; a closure report without a CLOSED section fires PHASE; an unrecognised status line fails closed.'
- 'The originating incident is pinned: a fixture doc containing a backticked tools/run_planner.py reference fires PATH.'
- 'The terminal-record carve-out is proven both ways: identical dead-path content does NOT fire under a simulated docs/closure/ and DOES fire outside it - the carve-out is directory-scoped, never a global suppression.'
- Existing check families produce byte-identical findings on the existing fixtures.
non_goals:
- 'No repairs: the linter reports; repairing drift is a separate ticket (the ATLAS-4/ATLAS-5 split). No changes to existing check families beyond what registering the new ones requires. No CI workflow changes - the linter already runs via pre-commit. No fragment/anchor validation (deferred to the heading-anchor index).'
test_requirements:
- Fixture-based negative tests in the established style; ATLAS_LIVE_TESTS=0; seeded defects use assert 1 == 2 (B011).
definition_of_done:
- All acceptance criteria evidenced by named tests; full gate sweep green; PR title carries the ticket key.
---

# Doc-linter v3: PATH and PHASE documentation-integrity checks

Minted from the reviewer session of 2026-07-24; decisions in `context` are operator-ratified.

---
title: "Print every SyncResult counter in the tick summary, with a completeness test"
objective: "Make a sync tick report what it did. `SyncResult` tracks twenty-one counters and the summary line prints eight, so a tick that completes a ticket and a tick that completes nothing emit identical output. Add the missing counters on a second line and pin completeness mechanically, so a future counter cannot be added without being printed."
context: "Cost real time on 2026-07-26. Four tickets sat in `Review Required` with a PASSED verdict and a satisfied merge gate, and the operator could not tell from three successive ticks whether step 3b had run, refused, or was never reached — the line was byte-identical each time. The outcome had to be read off the Linear board and then out of SQLite. `_format_sync_result` (atlas/cli.py:374) prints `pushes`, `pushed_created`, `pushed_updated`, `embeds`, `status_pulls`, `status_unchanged`, `anomalies_logged`, `unmapped_observations`, and the `push_skipped` breakdown. It omits `promoted`, `completed`, `follow_ups_stubbed`, `dwell_breaches`, `routed_to_human`, `review_cycles_logged`, `stale_blocks`, `agent_runs_reconstructed`, `agent_runs_updated`, `draft_lessons_filed`, `packs_truncated`, and `pack_render_failures` — every one of which represents a state change the operator is accountable for. Note what is NOT wrong, so the fix stays scoped: `sync_result_is_empty` (atlas/pm/sync.py) already folds all twenty-one counters, so the 'no work performed' prefix is correct today; only the rendering is deficient. Pre-ruled decisions (operator-ratified in reviewer session 2026-07-26; land them, do not relitigate): D-1 the existing first line stays BYTE-IDENTICAL and the missing counters go on a second line prefixed `pm sync actions:`; anything already grepping or asserting the first line keeps working, and the existing sync tests need no edit. D-2 zeros are always printed, never suppressed — a fixed-shape record is greppable and an omitted counter is ambiguous between zero and not-tracked, which is the exact failure this ticket closes. D-3 completeness is enforced by test, not by review: a test enumerates `SyncResult`'s dataclass fields and asserts every integer counter appears in the rendered output, so adding a counter without printing it fails CI. This is the acceptance criterion that matters; the rest is formatting. D-4 the `push_decisions` lines and their `--verbose` classification filter are untouched. D-5 `--repair-packs` keeps its own `_format_repair_pack_result` formatter, unchanged. D-6 no `--json` on `atlas pm sync`; a machine-readable tick record is a separate decision and out of scope."
ticket_type: tech_debt
epic_ref: "ATLAS-E6"
risk_level: low
component: cli
acceptance_criteria:
- "Every integer counter on `SyncResult` appears in the rendered summary, asserted mechanically by enumerating the dataclass fields and searching the output for each name — so a counter added later without a print fails this test."
- "The first summary line is byte-identical to today's for a given `SyncResult`, asserted against a pinned expected string."
- "A tick that completes one ticket renders `completed=1`; a tick that completes none renders `completed=0` — both present, neither omitted."
- "The `no work performed` prefix still appears for an all-zero result, and `completed` alone being non-zero is enough to render the `completed` prefix instead."
- "`push_decisions` lines and their `--verbose` filtering are unchanged: the existing decision-rendering tests pass unmodified."
- "`--repair-packs` output is unchanged: its formatter's existing tests pass unmodified."
non_goals:
- "No change to `SyncResult`'s fields, to any counter's increment site, or to `sync_result_is_empty`."
- "No `--json` output for `atlas pm sync`."
- "No change to `_format_repair_pack_result`, to `SyncDecision`, or to the decision classification filter."
- "No change to logging levels or to any log message inside the tick."
- "No renaming of existing printed counters — the first line's vocabulary is frozen."
test_requirements:
- "Pure formatter tests over hand-built `SyncResult` values; no database, no Linear client, no network; `ATLAS_LIVE_TESTS=0`; seeded defects use `assert 1 == 2` (ruff B011)."
- "The completeness test is the milestone anchor: seeding it by removing one counter from the rendered line must make it fail."
definition_of_done:
- "Every acceptance criterion evidenced by a named test; full gate sweep green with `ATLAS_LIVE_TESTS=0`; enumeration pins in `tests/test_acceptance.py` and `tests/test_schemas_export.py` confirmed unchanged; PR title carries the minted key."
---

# A tick that will not say what it did

Twenty-one counters tracked, eight printed. The four that mattered were the
ones missing.

---
title: "Retire-on-reject scope: a rejected apply must not retire hand-authored inbox stubs"
objective: "A rejected apply leaves the inbox exactly as it found it; only an applied run retires the stubs that fed it."
context: "ATLAS-122 (D2) retires inbox stubs to processed/ on BOTH the applied and the rejected outcome — 'both mean considered' — designed when a decline meant the follow-up's content was declined. The 2026-07-08 double-emission created the other decline reason: a diff declined for duplicate ADDs whose stub content is still wanted. Under the current scope that decline retires every hand-authored stub that fed the run, forcing a manual restore before the re-roll (the restore dance observed 2026-07-08, and re-observed as a hazard on PR #155's stray-commit incident). A reject is a verdict on the diff, not on the stubs: an operator who genuinely declines a stub's content deletes the stub — a git-visible act — rather than having apply infer it. Interacts with the promotion-dedup fix (F-4): apply-and-cancel is the standing disposition for duplicate mints precisely because a decline currently costs both a re-roll and the restore dance; this ticket removes the second cost."
ticket_type: "bug"
epic_ref: "ATLAS-E3"
acceptance_criteria:
  - "A rejected apply run moves no inbox stub: fixture with committed stubs, confirm callback returning REJECTED — every stub is still at its original inbox/ path afterwards; pre-fix this fixture shows them under processed/."
  - "The applied outcome is byte-identical to today: stubs retire to processed/ exactly as ATLAS-122 specified, idempotence preserved (missing source or existing target is a skip, not an error)."
  - "The rejected PlanRun itself is otherwise unchanged (status, provenance, renders untouched) — the fix narrows the retirement trigger only."
  - "The planning spec's apply-lifecycle text and the _retire_inbox_stubs docstring both state the new rule and its rationale (reject judges the diff, deleting a stub judges its content) in the same change."
non_goals:
  - "No change to applied-path retirement, the processed/ namespace, or idempotence semantics."
  - "No un-retire tooling or recovery path for past runs — restoring already-retired stubs stays a manual git act."
  - "No new stub lifecycle states."
test_requirements:
  - "Apply-level unit tests over tmp-path repo fixtures with a scripted confirm callback (no planner calls, ATLAS_LIVE_TESTS=0); the rejected-outcome fixture is the named regression."
definition_of_done:
  - "All acceptance criteria evidenced by named tests; full gate sweep green; the planning spec's apply section documents the narrowed retirement rule in the same change."
---

# Retire-on-reject scope

A decline should cost one roll, not a restore dance on top of it.

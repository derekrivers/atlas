---
title: "AgentRun reconstruction: build the run record from board and PR observation"
objective: "Every Symphony-dispatched ticket yields a persisted AgentRun row — dispatch time, handoff state, PR number, head commit, and the embedded pack_id — reconstructed from observation, so dispatched work is attributable without reading transcripts."
context: "Phase 8 seed 84. The AgentRun model and AgentRunRepo exist (atlas/core/models/agent_run.py, storage/repositories.py) with no producer. Atlas observes rather than integrates (ADR-0008): the record is reconstructed at sync time from what the loop already sees — the ticket's status transitions (dispatch = entry into in_progress; handoff = entry into review_required/needs_human_decision), the PR resolved from the verification close-set parsing or evidence rows, the head commit from evidence pins, and input_context_pack_id parsed from the PACK header the push embedded (pack_id / rendered_at are in the delimited section Atlas itself wrote — parsing our own header is not pull-side pack consumption and does not violate the ATLAS-164 non-goal). Pre-ruled decisions: reconstruction runs as a sync-tick step AFTER the pull, is idempotent (one AgentRun per ticket per dispatch cycle, keyed on ticket + dispatch transition id), tolerates missing pieces by recording what is observable with nulls elsewhere (never blocks the tick, never raises for absent data), and adds NO per-ticket Linear requests (reads the store and existing evidence only — the ATLAS-148 request-bound test extends to prove it)."
ticket_type: "feature"
epic_ref: "ATLAS-E10"
acceptance_criteria:
  - "A fixture replaying ATLAS-161's observed lifecycle (planned -> ready -> in_progress -> review_required, evidence rows pinned to a head, embedded pack header) yields one AgentRun with dispatch/handoff timestamps from the transition log, the PR and head commit from evidence, and the pack_id from the description header."
  - "Idempotence: a second tick over unchanged state creates zero new rows; a genuine re-dispatch (second in_progress entry) creates a second run."
  - "Partial observation records partial rows: a ticket with no evidence rows yet yields an AgentRun with null PR/commit and populated transitions; nothing raises."
  - "The no-op tick request bound is unchanged (extend the ATLAS-148 test); reconstruction makes zero Linear calls."
  - "`atlas pm report` gains an agent-runs section (count, mean dispatch-to-handoff) rendered from the rows."
non_goals:
  - "No Symphony-side integration, no transcript parsing, no pack content consumption beyond Atlas's own delimited header, no dispatch control."
test_requirements:
  - "Fixture-driven, ATLAS_LIVE_TESTS=0, seeds `assert 1 == 2` (B011); the ATLAS-161 lifecycle is the named fixture."
definition_of_done:
  - "All acceptance criteria evidenced by named tests; full gate sweep green; pm-engine-and-linear-sync.md documents the reconstruction step in the same change."
---

# AgentRun reconstruction

The loop already sees everything; this writes down what it saw.

# linear-sync Playbook

## When to Use This Playbook

Apply this playbook whenever you are building, modifying, or reviewing any component that reads from or writes to Linear as part of the Atlas sync pipeline. This includes the push pipeline that embeds context packs into issue descriptions, the preflight validation layer that checks state maps before apply, and the tick loop that polls and mutates Linear issues. If your change touches Linear API calls, state-map configuration, or agent-dispatch context delivery, this playbook applies.

---

## Operating Rules

### Context Pack Embedding

- At definition-push time, render the ticket's context pack and compose it into the pushed Linear description beneath the definition fields, separated by a named delimiter.
- The pack section is Atlas-owned and one-directional: push only, never parsed back into Atlas-owned fields. Round-trip pull parsing must not be affected by pack content.
- Size overflow must use truncation-with-marker, not a separate file or attachment.
- Render failures must push the definition-only and log a typed anomaly so the tick continues for remaining tickets. A render failure must never halt the full push run.
- Re-push on definition change must re-render the pack without adding per-ticket Linear requests. Embedding cost is rendering cost only; the request budget established for the push pipeline must be preserved.
- Cover boundary behaviours — size overflow and render failure — with named fixture tests at the emulator level.

### Preflight Validation and State Maps

- The accepted-type allowlist for rejected statuses must reflect exactly the type strings the live Linear board emits, verified by a real `workflowStates` query, not inferred from documentation or assumed spellings.
- Do not retain spelling variants that no live Linear instance has been observed to emit. Unverified variants are the direct cause of silent divergence.
- When adding a new state entry to `LINEAR_STATE_MAP`, confirm the corresponding type string against the live board before updating the allowlist.
- Fixture payloads used in validation tests must assert the spelling the live board uses, not the spelling the code wants. A fixture that agrees with the code instead of the board hides exactly this class of defect.
- Retain negative tests that reject completed-type and started-type states as rejected-status candidates.

### API Request Budget and Query Scope

- Never introduce per-entity fetch loops over a growing collection. A loop that fetches one ticket per request is an O(n) rate-budget exhaustion pattern by design, not a runtime accident.
- Replace per-ticket fetch loops with a single paginated, project-scoped issues query. The target ceiling for a no-op tick over a board of approximately 110 tickets is no more than 15 total client calls.
- Restrict comment scanning to tickets in the documented active-state set. Skip parked states such as `needs_human_decision`.
- Scope `fetch_workflow_states` to the team ID already required by the tick. Workspace-wide state queries return foreign teams' states with colliding names and make state-map resolution ambiguous.
- Encode all state-map decisions and active-state rationale in the canonical sync design document in the same change that introduces them.
- Validate the request bound with a falsifiable fake-client test that fails the moment a per-ticket fetch is reintroduced.

---

## Failure Modes to Avoid

**Parsing Atlas-owned description fields back into Atlas state.** The context pack section of a Linear description is write-only from Atlas's perspective. Treating it as a readable input field corrupts the one-directional boundary and risks feedback loops.

**Halting a push run on a single render failure.** A render failure for one ticket must not prevent the remaining tickets from receiving their definition push. Log the anomaly and continue.

**Hardcoding enum spellings from documentation rather than from the live board.** Documentation and live system spellings diverge silently. The live board is the only authoritative source. Verify by query.

**Keeping test fixtures that encode the desired spelling instead of the live spelling.** This keeps the test suite green while the live integration fails. Fixtures must reflect what the board actually emits.

**Introducing per-ticket fetch or scan loops.** Even a loop that works correctly at low ticket counts will exhaust a fixed request budget as the board grows. This is a structural design flaw, not a scaling concern to address later.

**Querying workflow states workspace-wide.** Workspace-wide queries return states from foreign teams, producing name collisions that make state-map resolution non-deterministic.

**Omitting a falsifiable request-budget test.** Without a test that breaks when a per-ticket fetch is reintroduced, the budget constraint will be silently violated by future changes.

---

## Review Checklist

- [ ] Context pack is composed beneath a named delimiter in the Linear description and is never read back during pull parsing.
- [ ] Size overflow path uses truncation-with-marker; render failure path pushes definition-only and logs a typed anomaly.
- [ ] Re-push on definition change does not add per-ticket Linear requests beyond rendering cost.
- [ ] Boundary behaviours (overflow, render failure) are covered by named emulator-level fixture tests.
- [ ] Accepted-type spellings in the preflight allowlist have been verified against a live `workflowStates` query for this board.
- [ ] No unverified spelling variants are present in the allowlist.
- [ ] Validation fixture payloads assert live-board spellings, not code-side spellings.
- [ ] Negative tests for completed-type and started-type states are present and passing.
- [ ] The tick loop uses a single paginated, project-scoped issues query; no per-ticket fetch loop is present.
- [ ] Comment scanning is restricted to the documented active-state set.
- [ ] `fetch_workflow_states` is scoped to the team ID, not workspace-wide.
- [ ] State-map decisions and active-state rationale are documented in the canonical sync design document in this change.
- [ ] A falsifiable fake-client test enforces the request-per-tick ceiling and will fail if a per-ticket fetch is reintroduced.

## Provenance

Generated from ACTIVE lessons tagged `linear-sync`.

- `57c4ccc7-98b0-4179-9820-e9dfe1da3dc1` - Embed rendered context packs in Linear issue descriptions for agent-readable context delivery
- `af5799f7-1e4f-4093-bcfc-da2da3e38ae0` - Hardcoded accepted-type spellings caused preflight failures when live board used alternate variants
- `6bdb52d3-45c2-435f-b540-fb4e208ff37c` - Per-ticket fetch loops exhaust API rate budgets by design, not by accident

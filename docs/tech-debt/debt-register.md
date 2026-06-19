# Debt Register

A running register of known technical debt.

## Atlas → Linear priority mapping (deferred, ATLAS-42)

`priority` is owned Atlas → Linear (ADR-0006 field ownership) but is **not
synced** in v1. Atlas `priority` is an unconstrained signed integer (the
data-model example uses `10`); Linear `priority` is an inverted 4-value
category enum (`0` = No priority, `1` = Urgent, `2` = High, `3` = Medium,
`4` = Low). There is no honest mapping yet: a clamp to `[0, 4]` would both
lose information and invert meaning. ATLAS-42 therefore drops `priority` from
`OWNED_DEFINITION_FIELDS` and syncs title + description only, exactly as
`labels` is deferred until a `Ticket.labels` field exists.

**To close:** pin Atlas's `priority` convention (range and which direction is
"more urgent"), then add a real translation that respects Linear's inverted
0–4 enum, restore `priority` to `OWNED_DEFINITION_FIELDS`, and assert the
mapping (not a raw value) crosses. Owner: a follow-up on the Phase-4
field-ownership work.

## Non-idempotent issue-create window in the sync push (ATLAS-42 → ATLAS-50)

`sync_tick`'s push creates a Linear issue for a pushable ticket that has no
`external_linear_id`, then immediately commits the returned id back to the
ticket (push-then-stamp, D5). `update_issue` is idempotent, so a crash between
push and stamp is harmless on the update path. `create_issue` is **not**: a
crash after the create but before the id-commit means the next tick re-creates
a duplicate Linear issue. The commit-id-immediately step shrinks the window to
a single statement but does not eliminate it.

**Why it can bite:** unattended frequent ticks (ATLAS-50, the PM scheduler)
multiply the exposure. **To close:** give the create a dedup key — e.g. an
idempotency token derived from the ticket key/id, or a pre-commit of the
intent — so a replayed tick reconciles to the existing issue instead of
minting a second one. Owner: ATLAS-50 (scheduler), where the create path runs
unattended.

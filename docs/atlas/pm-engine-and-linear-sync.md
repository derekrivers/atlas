# PM Engine and Linear Sync Design (Phase 4)

Status: Active design document for Phase 4. Builds on ADR-0006 field
ownership and `symphony-integration.md#ticket-transitions-one-writer-per-state-edge`.

## Boundary

The PM Engine is a reconciliation loop, not an orchestrator. It promotes
ready work, mirrors state between Atlas and Linear under strict field
ownership, converts agent follow-up comments into planning inputs, and
detects delivery anomalies. It never executes work, never restarts agents,
and never writes to `docs/planning/` (that is `atlas apply`'s monopoly).

## Field ownership

| Field                      | Owner / direction              |
| -------------------------- | ------------------------------ |
| title, priority, labels    | Atlas → Linear                 |
| description (context pack) | Atlas → Linear, frozen once In Progress (`symphony-integration.md#context-pack-delivery`) |
| state                      | split by transition edge (see symphony-integration state table) |
| comments                   | agent writes; Atlas reads tagged follow-ups |
| assignee, estimates        | unsynced in v1                 |

`external_linear_id` on the ticket is the join key; it is written once at
issue creation and never reused.

## Sync loop

Pull-based, consistent with ADR-0008 (no webhooks before hosting):

1. Every tick (default 60s): fetch Linear states for all tickets with an
   `external_linear_id` in a non-terminal Atlas status; apply state
   changes that follow the ownership table; log anomalies otherwise.
2. Push definition updates (title/priority/labels/description) for
   tickets whose Atlas `updated_at` is newer, only while the ticket is in
   a pre-dispatch status or `Ready for Agent`.
3. Run the readiness predicate (dependency-engine.md#readiness-predicate);
   for each newly ready ticket: render its context pack (Phase 5), create
   or update the Linear issue with the embedded pack, set
   `ready_for_agent` in Atlas and `Ready for Agent` in Linear. The PM
   Engine is the **sole writer** into this state.
4. Scan recent issue comments for the `atlas:proposed-follow-up` tag.
5. Run anomaly and dwell checks (below).

Ticks are idempotent; a missed tick costs latency only. The scheduler is a
plain loop (or cron) — no distributed job system.

## Follow-up ingestion

Tagged comments are converted into proposal stubs written to
`docs/planning/inbox/<ticket-key>-<n>.md` (title, verbatim comment body,
source issue link). The inbox is an *input document set* for the next
`atlas plan` run — follow-ups enter the backlog only through plan/apply
(ADR-0007), never as direct ticket creation. Applied or rejected stubs are
moved to `docs/planning/inbox/processed/` by `atlas apply`.

## Anomaly and dwell detection

- Out-of-ownership state transitions: logged; three occurrences for the
  same ticket creates a `DebtItem`.
- Review cycling: more than 3 `changes_requested → pr_open` round trips
  routes the ticket to `Needs Human` with a failure-analysis note.
- Dwell horizons (config, defaults): `in_progress` 24h, `pr_open` 48h,
  `review_required` 7d. Breaches surface in the delivery report; only the
  review-cycle rule changes state automatically.

## Delivery metrics

`atlas pm report`: throughput (tickets done/week), cycle time per state,
ready-queue depth, anomaly counts, dwell breaches. CLI/markdown output;
no dashboard (Revision 1).

## Open items

- Linear issue creation batching and rate limits — measure before tuning.
- Whether epic ↔ Linear project mapping is worth it in v1 (current
  position: labels only).

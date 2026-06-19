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
| description | Atlas → Linear, frozen once In Progress. Summary only in v1; context pack embedded from Phase 8 (`symphony-integration.md#context-pack-delivery`) |
| state                      | split by transition edge (see symphony-integration state table) |
| comments                   | agent writes; Atlas reads tagged follow-ups |
| assignee, estimates        | unsynced in v1                 |

`external_linear_id` on the ticket is the join key; it is written once at
issue creation and never reused.

### Field-ownership boundary (ATLAS-41)

The ownership rule is enforced as a hard allow-list in `atlas/linear/`, the
Phase-4 provider boundary (a layer above `atlas.core`, which imports nothing
Linear/HTTP/GraphQL — import-linter spine; ADR-0006). ATLAS-41 delivers this
boundary only; the reconcile loop that drives it on a cadence is ATLAS-42.

**Client.** `LinearClient` (`atlas/linear/client.py`) is the atlas-side
protocol: `create_issue`/`update_issue` (definitions, Atlas → Linear),
`fetch_issue` and `fetch_workflow_states` (status direction and validation).
It returns a `LinearIssue` DTO (`id`, `title`, `state_id`, `state_name`,
`state_type`). The real `LinearGraphQLClient` talks request/response GraphQL
at `https://api.linear.app/graphql` (stdlib transport, no webhooks —
ADR-0008); `InMemoryLinearClient` is the contract-tested fake.

**Definitions (Atlas → Linear).** `definition_payload(ticket)` is built only
by iterating `OWNED_DEFINITION_FIELDS` (title and a description that is the v1
human-readable summary). It carries no state key, and the client rejects any
key outside `OWNED_LINEAR_INPUT_KEYS`, so ticket *status* is mechanically
incapable of crossing Atlas → Linear. Two doctrine fields are owned but not
yet syncable, deferred rather than silently guessed: `labels` is owned in the
table above but has no `Ticket.labels` field; and `priority` is owned but has
no honest mapping yet — Atlas `priority` is an unconstrained signed integer
while Linear `priority` is an inverted 4-value enum (0 = None, 1 = Urgent …
4 = Low), so ATLAS-42 deferred it (a naive clamp would lose information and
invert meaning) until that mapping is pinned (tracked in
`docs/tech-debt/debt-register.md`). v1 therefore syncs title + description.

**Status (Linear → Atlas).** `LinearStatusMap` is an operator-configured
`dict[linear_state_id → TicketStatus]`, sourced from the JSON env var
`LINEAR_STATE_MAP` (e.g. `{"<state-uuid>": "in_progress"}`). The stable
Linear state **id** is the lookup key — never the customizable name
(rename-fragile), never the coarse type (`in_progress`, `pr_open`,
`review_required`, `changes_requested` all share Linear type `started`, and
the anomaly engine needs them distinguished). `status_from_issue(issue,
status_map)` reads only the state id and returns the mapped status or `None`;
an unmapped id is dropped, not guessed (ATLAS-42 counts and logs it; ATLAS-118
surfaces it as an anomaly).
The Linear state `type` is used only as load-time validation
(`validate_against_states`): it confirms each configured id still exists in
the workspace (stale-map guard — rotated UUIDs fail loudly) and rejects a
type-contradictory mapping, while permissively allowing several Atlas
statuses under one Linear type. A missing or empty `LINEAR_STATE_MAP` on the
live path raises `LinearStatusMapError` rather than silently disabling
status sync.

**Secrets.** `LINEAR_API_KEY`, `LINEAR_TEAM_ID`, and `LINEAR_STATE_MAP` are
read only at the client boundary, never logged, never committed (`.env` is
git-ignored). The deterministic core never touches them; tests inject the
client and the status map directly, so CI runs with no network and no
secrets. An opt-in live smoke test (`ATLAS_LIVE_TESTS=1` plus the token)
exercises the real workspace and is skipped in default CI.

## Sync loop

Pull-based, consistent with ADR-0008 (no webhooks before hosting):

1. Every tick (default 60s): fetch Linear states for all tickets with an
   `external_linear_id` in a non-terminal Atlas status; apply state
   changes that follow the ownership table; log anomalies otherwise.
2. Push definition updates (title/priority/labels/description) for
   tickets whose Atlas `updated_at` is newer, only while the ticket is in
   a pre-dispatch status or `Ready for Agent`.
3. Run the readiness predicate (dependency-engine.md#readiness-predicate);
   for each newly ready ticket, write `Ready for Agent` in Linear through the
   PM Engine's dedicated state-write path (`LinearClient.set_state`). The
   definition (title/labels and human-readable summary) is already mirrored by
   step 2's push, which is reused — step 3 adds only the state transition. The
   PM Engine is the **sole writer** into this state, and this is the **one
   sanctioned outbound status write** Atlas → Linear: it cannot carry a
   definition field, the general allow-list is unchanged (`stateId` stays out
   of it), and every other status write Atlas → Linear remains mechanically
   impossible. The write is Linear-only: Atlas's own `ready_for_agent` is
   reconciled by step 1's next pull, which keeps the pull the single writer of
   Atlas status (one tick of latency). The write is idempotent — setting the
   already-set state is a no-op — so an interrupted or repeated promotion is
   safe, and a ticket already in `ready_for_agent` is never re-promoted. The
   target Linear state is resolved by inverting the configured status map
   (exactly one state must map to `ready_for_agent`, validated at load), so the
   state written is exactly the one the next pull reads back.

   Context-pack rendering (Phase 5) and embedding the pack into the issue
   description (Phase 8, ATLAS-82, per
   `symphony-integration.md#context-pack-delivery`) are forward
   capabilities. Distinguish dependency-readiness (the Phase 3 predicate,
   live now — which already enforces criteria-present and ADR-accepted, not
   dependencies alone) from dispatch-readiness (dependency-ready +
   pack-rendered): only the `pack-rendered` conjunct is deferred, becoming
   load-bearing when Symphony consumes this state (Phase 8). Promoting without
   a pack in Phase 4–7 is harmless — nothing dispatches off `Ready for Agent`
   until Phase 8.
4. Scan recent issue comments for the `atlas:proposed-follow-up` tag.
5. Run anomaly and dwell checks (below).

Ticks are idempotent; a missed tick costs latency only. The scheduler is a
plain loop (or cron) — no distributed job system.

**Step → ticket map.** Steps 1+2 (pull a mapped status; push owned
definitions) are ATLAS-42 (`atlas/pm/sync.py`, `sync_tick`). Step 1's "log
anomalies otherwise" clause (an unmapped Linear state → `DebtItem`) and step 5
(anomaly and dwell checks) are ATLAS-118. Step 3 (readiness promotion, sole
writer into `Ready for Agent`) is ATLAS-43. Step 4 (the follow-up comment
scan) is ATLAS-45. The recurring scheduler that calls `sync_tick` on a cadence
is ATLAS-50.

## Follow-up ingestion

Tagged comments are converted into proposal stubs written to
`docs/planning/inbox/<ticket-key>-<n>.md` (title, verbatim comment body,
source issue link). The inbox is an *input document set* for the next
`atlas plan` run — follow-ups enter the backlog only through plan/apply
(ADR-0007), never as direct ticket creation. Applied or rejected stubs are
moved to `docs/planning/inbox/processed/` by `atlas apply`.

## Anomaly and dwell detection

- Out-of-ownership state transitions: each observed transition appends one
  `DebtItem` row (append-only, system-written). Recurrence — default: three
  or more rows for the same ticket and anomaly type — is the query-time
  `recurring(...)` predicate surfaced in the delivery report, never a
  creation gate and never a stored counter.

`DebtItem` is an operational record (ADR-0006 §2), append-only, written by
the PM Engine from deterministic observation — `created_by_type = system`,
so no trust tier and no PENDING cap (it is not evidence). One row per
observation; recurrence and severity derive by query. Recording a
`DebtItem` never changes ticket state: only the review-cycling rule below
routes to `Needs Human`. Logging debt and moving a ticket are separate
concerns.
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

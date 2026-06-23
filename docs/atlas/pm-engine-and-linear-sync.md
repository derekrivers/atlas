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
anomalies otherwise" clause — an unmapped Linear state appends one
`OUT_OF_OWNERSHIP_TRANSITION` `DebtItem` per transition — is ATLAS-118 (woven
into `sync_tick`'s pull). Step 3 (readiness promotion, sole writer into `Ready
for Agent`) is ATLAS-43. Step 4 (the follow-up comment scan) is ATLAS-45.
Step 5's anomaly checks split by mechanism, all woven into `sync_tick`'s final
pass after `promote_ready`: dwell-breach logging is ATLAS-119 (a `_detect_dwell`
pass keyed on `Ticket.status_entered_at`; report-only, never moves a ticket),
review-cycling is ATLAS-120 (a `_detect_review_cycle` pass keyed on
`Ticket.review_cycle_count`, routing over-threshold tickets to
`needs_human_decision` via `set_state` and logging one `REVIEW_CYCLE` `DebtItem`
— the one anomaly that moves a ticket), and stale-block detection is ATLAS-44 (a
`_detect_stale_block` pass keyed on `blocked(graph, key)` over the same
dependency graph `promote_ready` consumes; report-only, never moves a ticket).
The recurring scheduler that calls `sync_tick` on a cadence is ATLAS-50.

## Follow-up ingestion

Tagged comments are converted into proposal stubs written to
`docs/planning/inbox/<ticket-key>-<n>.md`. The inbox is a *separate plan input
source* — its own input document set for the next `atlas plan` run, distinct
from the operator's hand-authored input docs — and follow-ups enter the backlog
only through plan/apply (ADR-0007), never as direct ticket creation.

**Producer / consumer split.** This loop is delivered in two halves:

- The **producer** (step 4, ATLAS-45) is the comment scan. Per synced ticket
  (one with an `external_linear_id`, in a non-terminal status), the sync loop
  reads the issue's comments through the read-only `LinearClient.fetch_comments`
  and, for each comment whose body contains the `atlas:proposed-follow-up` tag,
  writes one stub to the working tree. The stub carries a title, the verbatim
  comment body, an honest source reference (the source ticket key and its Linear
  issue id), and the source comment id. The write is atomic (temp + rename) and
  is the **one sanctioned `docs/planning/` write** outside `atlas apply` — the
  inbox's machine writer, like `apply` writes the backlog renders (ADR-0007);
  it writes only under `inbox/`, nowhere else in `docs/planning/`. The producer
  creates no ticket and writes no Atlas or Linear state, and it **does not commit
  the stubs**: it writes to the working tree and stops. The follow-up scan reads
  real Linear, so ATLAS-45 carries an operator-run live gate (a real
  `atlas:proposed-follow-up` comment producing one stub) — CI green is necessary
  but not sufficient (ADR-0008).
- The **operator** commits the inbox. This is the human-steered gate that decides
  which follow-ups become plan inputs — surfacing a follow-up is mechanical;
  admitting it to the backlog is a deliberate act.
- The **consumer** (ATLAS-122) closes the loop: `atlas plan` reads the committed
  inbox as the separate input source above, and `atlas apply` moves applied or
  rejected stubs to `docs/planning/inbox/processed/`. ATLAS-45 does **not** wire
  either side.

**Dedup.** The scan sees the same tagged comment on every tick, so each stub is
made self-identifying by recording its **source comment id** as the dedup key: a
comment whose id already has a stub under `docs/planning/inbox/` **or**
`docs/planning/inbox/processed/` is skipped. A comment is therefore stubbed once
on first sight and never again — robust to a comment tagged late (an older
comment newly tagged is stubbed on first sight, then skipped), and needing no
per-ticket timestamp cursor (which would miss late-tagged older comments) and no
schema field. The key is written as a non-rendering HTML comment
(`<!-- atlas-source-comment-id: <id> -->`) on the stub's first line, kept
**separate** from the verbatim body so body text that itself contains the tag can
never be mistaken for the key. `<n>` is the next free index for the ticket key,
computed across both `inbox/` and `inbox/processed/` so indices stay monotonic
even after `atlas apply` (ATLAS-122) moves a stub to `processed/`. Accepted
failure modes: a stub manually deleted from `inbox/` before processing is
re-stubbed on next sight; a verbatim body containing the exact marker line would
false-dedup (vanishingly unlikely).

## Anomaly and dwell detection

- Out-of-ownership state transitions (ATLAS-118): each observed transition
  appends one `OUT_OF_OWNERSHIP_TRANSITION` `DebtItem` row (append-only,
  system-written). The pull observes this when a Linear state does not follow
  the ownership table — i.e. `status_from_issue` returns `None` (an unmapped
  state). "Per transition" is enforced by `Ticket.last_observed_linear_state_id`:
  the row fires only when the observed state id *changes* into an
  out-of-ownership state, so an unmapped state that persists across ticks logs
  one row, not one per tick, while a re-occurrence (unmapped → mapped →
  unmapped) is a genuine new transition. Recurrence — default: three or more
  rows for the same ticket and anomaly type — is the query-time `recurring(...)`
  predicate surfaced in the delivery report, never a creation gate and never a
  stored counter.

`DebtItem` is an operational record (ADR-0006 §2), append-only, written by
the PM Engine from deterministic observation — `created_by_type = system`,
so no trust tier and no PENDING cap (it is not evidence). One row per
observation; recurrence and severity derive by query. Recording a
`DebtItem` never changes ticket state: only the review-cycling rule below
routes to `Needs Human`. Logging debt and moving a ticket are separate
concerns.
- Review cycling (ATLAS-120): more than 3 `changes_requested → pr_open` round
  trips routes the ticket to `Needs Human` with a failure-analysis note. This is
  the **one anomaly that changes ticket state** — it both logs AND moves, where
  the out-of-ownership and dwell logs only log. The round trips are counted on
  `Ticket.review_cycle_count`, incremented by `apply_linear_status` (the sole
  post-creation status writer) only on a `changes_requested → pr_open`
  transition — no other transition touches it, and like `status_entered_at` it
  never bumps `updated_at`. The step-5 pass routes a ticket whose count exceeds
  the threshold while it is still in a cycling state (`changes_requested` or
  `pr_open`) through the sanctioned `LinearClient.set_state` to
  `needs_human_decision` — the **same** outbound write ATLAS-43 uses, resolved
  via `state_id_for(needs_human_decision)` (a unique Needs-Human state required,
  validated up front like the Ready-for-Agent target); `stateId` stays out of
  the allow-list and no new outbound mechanism is added. The write is Linear-only
  (ATLAS-42's next pull reconciles Atlas, after which the ticket leaves the
  cycling states and the pass self-clears) and idempotent. The route runs first,
  then the note: one `REVIEW_CYCLE` `DebtItem` (system-written, the deterministic
  failure-analysis summary — no model call, no Linear comment) deduped per
  `pr_open` episode by the same `Ticket.status_entered_at` boundary dwell uses,
  so a not-yet-reconciled or retrying route logs one row, not one per tick. The
  counter is monotonic in v1 (no reset on human intervention).
- Dwell horizons (ATLAS-119; config, defaults): `in_progress` 24h, `pr_open`
  48h, `review_required` 7d. When a ticket sits in one of these working states
  past its horizon, the sync loop's step-5 pass appends one `DWELL_BREACH`
  `DebtItem` (append-only, system-written) and the breach surfaces in the
  delivery report (ATLAS-47). It is report-only — like the out-of-ownership
  log, it NEVER changes ticket state; only the review-cycle rule does that.
  "Per episode" is enforced by `Ticket.status_entered_at` (the per-state entry
  timestamp the model now carries): a breach fires only when no `DWELL_BREACH`
  row exists for the ticket since it entered its current status, so a ticket
  that stays past its horizon across ticks logs one row, not one per tick. When
  the status changes, `status_entered_at` advances and a fresh episode can log
  again. `status_entered_at` is stamped by `apply_linear_status` (the sole
  post-creation status writer) only on a real status change, and a NULL entry
  time (unknown) is skipped, never breached. Recurrence is the same query-time
  `recurring(...)` predicate, never a stored counter.
- Stale block (ATLAS-44): when a ticket sits in the `blocked` status but its
  structural blockers have all cleared — i.e. `blocked(graph, key)` (the
  dependency-engine blocker analysis) is empty — the step-5 pass appends one
  `STALE_BLOCK` `DebtItem` (append-only, system-written) and the candidate
  surfaces in the delivery report. It is **report-only — like the out-of-ownership
  and dwell logs it NEVER changes ticket state**; only the review-cycle rule does
  that. It surfaces a ticket that may be ready to move but is stranded in
  `blocked`, where `promote_ready` will not touch it (that pass promotes only
  `planned`/`backlog`). It deliberately does **not route**: the dependency graph
  knows only *structural* blockers (`DEPENDENCY_NOT_DONE` / `ADR_NOT_ACCEPTED` /
  `DANGLING_TARGET`), so a ticket may be `blocked` for a non-structural reason the
  graph cannot see — the engine reports the candidate and the operator decides
  whether to move it. The inverse direction (a ticket structurally blocked but
  not marked `blocked`) is **out of scope**: `is_ready` already refuses to promote
  it, so it is not stranded. This is distinct from blocked-dwell ("blocked too
  long while genuinely blocked"), which is deliberately not detected — `blocked`
  carries no dwell horizon. "Per episode" is enforced exactly as for dwell, by
  `Ticket.status_entered_at`: a row fires only when no `STALE_BLOCK` row exists
  for the ticket since it entered `blocked`, so a ticket stranded across ticks
  logs one row, not one per tick; when the status changes, `status_entered_at`
  advances and a fresh stranded episode can log again. A NULL entry time
  (unknown) is skipped, never logged. The check is structural, not time-based —
  it takes no horizon and no clock beyond stamping the row's `observed_at`. The
  graph is the one `promote_ready` already projects this tick from current Atlas
  state (promotion writes Linear only, so it does not perturb the graph).

## Delivery metrics

`atlas pm report` (ATLAS-47): throughput (tickets done/week), cycle time per
state, ready-queue depth, anomaly counts, dwell breaches. CLI/markdown output
with a `--json` form; no dashboard (Revision 1). A PURE READER — it makes no
Linear calls and writes nothing, computing every metric from stored tickets and
`DebtItem`s (never from the per-tick, ephemeral `SyncResult`), so it runs with
no network and no secrets.

The five metrics, as computed in v1:

- **Throughput** — tickets currently `done`, bucketed by the ISO week
  (`YYYY-Www`) of `status_entered_at`. A `done` ticket with a null entry time
  falls in an `unknown` bucket rather than being dropped.
- **Cycle time per state** — reported as a **current-dwell proxy**, NOT
  historical per-state cycle time. The data model carries only
  `status_entered_at` (when a ticket entered its *current* status), so full
  historical time-in-each-state is not computable. v1 therefore reports, per
  non-terminal status, the current time-in-state (min/median/max hours) of the
  in-flight tickets, labelled *current dwell per state*. Failure modes: a ticket
  that was `done` and later reopened has lost its earlier entry time, so its
  prior dwell is invisible; a ticket whose `status_entered_at` is null (unknown
  entry) is excluded from the durations and counted separately rather than
  guessed. The true historical metric needs a per-transition history the model
  does not yet carry; that schema is deferred to ATLAS-121 (an append-only
  `TicketStatusTransition` log written by `apply_linear_status`, owner: PM
  Engine) and is NOT added here.
- **Ready-queue depth** — the count of tickets in `ready_for_agent`.
- **Anomaly counts** — `DebtItem`s grouped by `AnomalyType`, with the recurring
  ones (the query-time `recurring(...)` predicate) called out per type.
- **Dwell breaches** — the `DWELL_BREACH` subset of the anomaly log (ATLAS-119),
  surfaced per ticket.

## Open items

- Linear issue creation batching and rate limits — measure before tuning.
- Whether epic ↔ Linear project mapping is worth it in v1 (current
  position: labels only).

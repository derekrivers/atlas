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
| description | Atlas → Linear, frozen once In Progress. Full-spec definition render with the rendered context pack embedded beneath it at definition-push time (ATLAS-164; push-only — Atlas never parses packs back — behind the pinned `ATLAS CONTEXT PACK v1 \| pack_id: … \| rendered_at: …` delimiter; `symphony-integration.md#context-pack-delivery`) |
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
`fetch_project_issues` (the batched, paginated, project-scoped pull that
feeds step 1; ATLAS-148), `fetch_issue` (single-issue reads outside the
tick), and `fetch_workflow_states` (validation) — the last **team-scoped**
(`team(id:) { states }`, taking the team id the tick already requires;
ATLAS-148) because the workspace-wide form returned foreign teams' states
with colliding names (two `Canceled`, two `Done`, two `Duplicate` observed
live). It returns a `LinearIssue` DTO (`id`, `title`, `state_id`,
`state_name`, `state_type`, plus `identifier` on the batched pull —
diagnostics only, never a join key). The real `LinearGraphQLClient` talks request/response GraphQL
at `https://api.linear.app/graphql` (stdlib transport, no webhooks —
ADR-0008); `InMemoryLinearClient` is the contract-tested fake.

**Definitions (Atlas → Linear).** `definition_payload(ticket)` is built only
by iterating `OWNED_DEFINITION_FIELDS` (title and the full-spec definition
description). It carries no state key, and the client rejects any
key outside `OWNED_LINEAR_INPUT_KEYS`, so ticket *status* is mechanically
incapable of crossing Atlas → Linear. At definition-push time the sync tick
widens the description with the ticket's rendered context pack
(`compose_embedded_description`, ATLAS-164) beneath the definition fields,
behind the pinned delimiter — still the same single owned key, content
widened exactly as ATLAS-143 widened `title`; the render-failure posture,
the 100,000-char overflow pin, the definition-change-only refresh rule,
and the operator-invoked pack repair sweep are recorded in
`symphony-integration.md#context-pack-delivery`. Two doctrine fields are
owned but not
yet syncable, deferred rather than silently guessed: `labels` is owned in the
table above but has no `Ticket.labels` field; and `priority` is owned but has
no honest mapping yet — Atlas `priority` is an unconstrained signed integer
while Linear `priority` is an inverted 4-value enum (0 = None, 1 = Urgent …
4 = Low), so ATLAS-42 deferred it (a naive clamp would lose information and
invert meaning) until that mapping is pinned (tracked in
`docs/tech-debt/debt-register.md`). The sync therefore carries title +
description (definition + embedded pack), and nothing else.

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
(`validate_against_states`): it confirms each configured id still exists on
the team's board (team-scoped since ATLAS-148; stale-map guard — rotated
UUIDs fail loudly, and a foreign team's same-named state can no longer
satisfy the check) and rejects a
type-contradictory mapping, while permissively allowing several Atlas
statuses under one Linear type. A missing or empty `LINEAR_STATE_MAP` on the
live path raises `LinearStatusMapError` rather than silently disabling
status sync.

### State-map completeness (ATLAS-148)

Every workflow state visible in the workspace is accounted for below —
**mapped** (it appears in `LINEAR_STATE_MAP` with the Atlas status shown)
or **intentionally unmapped** (with its rationale). Nothing is silently
unmapped: an id observed outside this table is a genuine anomaly
(ATLAS-118), not a latent decision. State ids below were resolved by the
operator running the team-scoped `fetch_workflow_states` query this
change introduced (one read-only request, 2026-07-08) — the workspace
carries two same-named `Duplicate` states (one per team), so only a
team-scoped read disambiguates them; the workspace-wide listing that
preceded it could not.

Atlas team states (nine; Linear `type` in parentheses):

| Linear state | State id (UUID) | Maps to | Rationale |
| ------------ | --------------- | ------- | --------- |
| Ready for Agent (unstarted) | `df1ebd92-7c41-4585-a15b-29b9e73f840f` | `ready_for_agent` | the step-3 promotion target; the PM Engine's one sanctioned outbound state write resolves to exactly this state |
| In Progress (started) | `381b59b4-7ffe-4247-9cd8-6a11585203ea` | `in_progress` | an agent is actively working the ticket; dwell-horizoned |
| PR Open (started) | `1ea72cdb-5f02-473f-8439-028e40d904f0` | `pr_open` | a PR is up; review-cycling counts arrivals into this state |
| Review Required (started) | `cf16f7da-6193-4dbf-b8fd-fa75dc9a16d7` | `review_required` | awaiting verification; step 3b's verified completion consumes it |
| Changes Requested (started) | `a3bba9c2-716e-47a6-b1ce-dcff4183c425` | `changes_requested` | rework requested; the other half of the review cycle |
| Needs Human (backlog) | `311a3a97-c409-4cce-96ab-0a3bfc2a5541` | `needs_human_decision` | parked for the operator; the review-cycling route target |
| Done (completed) | `ca6f5cee-5796-4102-bab7-24f08732549d` | `done` | delivered; terminal |
| Canceled (canceled) | `84207146-0b47-4821-a7e9-331abe38e77a` | `rejected` | closed undelivered; terminal |
| Duplicate (duplicate) | `cd8e7c95-8a25-48ad-b0ef-19e00f000e70` | `rejected` (operator adds post-merge) | a duplicate is work that closed undelivered under this key; the duplicate-of reason lives in Linear natively, not in a new Atlas status |

The operator adds the `Duplicate` entry to `LINEAR_STATE_MAP` from the
UUID documented above after this change merges — the change itself edits
no environment configuration. Known gap, flagged for follow-up rather
than folded into this change: `validate_against_states`' accepted-types
table admits only `cancelled` for `rejected`, while the live board
reports type `canceled` (US spelling) for the Canceled state and type
`duplicate` for the Duplicate state — so preflight C2 will fail on the
`rejected` mappings until the accepted-types row learns both live
spellings. The sync tick itself does not run that validation and is
unaffected.

Sibling team states (grouped): the workspace's second team carries nine
workflow states, all intentionally unmapped — foreign team; its issues
never sync to Atlas.

**Secrets.** `LINEAR_API_KEY`, `LINEAR_TEAM_ID`, `LINEAR_PROJECT_ID`, and
`LINEAR_STATE_MAP` are read only at the client boundary, never logged, never
committed (`.env` is git-ignored). `LINEAR_PROJECT_ID` scopes issue creation to
a Linear project alongside the team, so created issues are visible to Symphony's
project-scoped poll; it is the project's **id** (a UUID), not its `slugId` (the
`project_slug` in `WORKFLOW.md`) — two different fields of the same project. The deterministic core never touches them; tests inject the
client and the status map directly, so CI runs with no network and no
secrets. An opt-in live smoke test (`ATLAS_LIVE_TESTS=1` plus the token)
exercises the real workspace and is skipped in default CI.

## Sync loop

Pull-based, consistent with ADR-0008 (no webhooks before hosting):

1. Every tick (default 60s): fetch every issue in the configured Linear
   project in **one batched, paginated, project-scoped query**
   (`LinearClient.fetch_project_issues`, `project(id:).issues` at a page
   size of 250 — Linear's maximum, so a board within 250 issues pulls in
   one request and a larger one costs `ceil(n / 250)`; ATLAS-148 — the
   project scope matches Symphony's poll, `LINEAR_PROJECT_ID`). Join the
   returned issues to tickets with an `external_linear_id` in a
   non-terminal Atlas status **by `external_linear_id` only — never by
   title, never by identifier** (both are carried for diagnostics only);
   a joined ticket whose issue is absent from the pull (deleted, or moved
   out of the project and so out of the poll scope) is left unchanged
   with a warning, exactly as the retired per-ticket fetch treated a
   missing issue. The fetch is skipped entirely when no joined
   non-terminal ticket exists, so an empty board costs zero pull
   requests. Apply state changes that follow the ownership table; log
   anomalies otherwise. (The pre-148 shape fetched each ticket's issue
   individually — ~110 requests per tick on a 110-ticket board, which is
   what starved the 2,500/hour budget at the default cadence.)

   Immediately after the pull, reconstruct `AgentRun` rows from local
   observations (ATLAS-166): each `in_progress` entry in the
   `TicketStatusTransition` log is one dispatch cycle, keyed by that dispatch
   transition id. The next `review_required` or `needs_human_decision`
   transition supplies the handoff timestamp/state when present; evidence and
   verification rows supply the PR number/head commit; the already-fetched
   issue description supplies the Atlas-authored context-pack header
   (`pack_id`, `rendered_at`). Missing pieces remain null and never block the
   tick. The step makes no Linear call of its own and updates an existing
   partial run when later ticks observe handoff or evidence.

2. Push definition updates (title/priority/labels/description) for
   tickets whose Atlas `updated_at` is newer, only while the ticket is in
   a pre-dispatch status or `Ready for Agent`. A successful full embed
   stamps `linear_synced_at` to the pushed `updated_at`. An enumerated
   context-pack render failure still pushes the definition-only payload
   and logs one `PACK_RENDER_FAILURE`, but does **not** stamp the cursor:
   `updated_at` remains ahead of `linear_synced_at`, so the next tick retries
   the full embed until the render condition clears. A first-sync degraded
   create records only the Linear join key, never the cursor, so the retry
   updates the same issue instead of creating a duplicate.

   `atlas pm sync --repair-packs` adds an operator-invoked, one-shot repair
   sweep after this normal push pass. The sweep examines only descriptions
   already returned by the batched project pull, selects pushable tickets with
   an `external_linear_id`, a current definition cursor, and no
   `ATLAS CONTEXT PACK v1` header in Linear, then re-renders and re-pushes the
   full embedded description. Successful repairs stamp normally; a second
   repair run over the same board is a zero-write no-op. The plain periodic
   tick does not run this branch and therefore keeps the ATLAS-148 request
   budget unchanged.
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

   Context-pack rendering (Phase 5) is live, and embedding the pack into
   the issue description is delivered by ATLAS-164 at the definition-push
   step above (per `symphony-integration.md#context-pack-delivery`, the
   Phase 8 milestone leg). Distinguish dependency-readiness (the Phase 3
   predicate, live now — which already enforces criteria-present and
   ADR-accepted, not dependencies alone) from dispatch-readiness
   (dependency-ready + pack-rendered): the `pack-rendered` conjunct of
   PROMOTION remains deferred — promotion does not gate on a successful
   render, and a push-time render failure degrades that push to
   definition-only under the D-2 rule rather than blocking the ticket, with
   the cursor left unstamped so a later clean tick retries the embed —
   becoming load-bearing when Symphony consumes this state.
4. Scan issue comments for the `atlas:proposed-follow-up` tag — but only
   for tickets in the **active-state set** `{ready_for_agent, in_progress,
   pr_open, review_required, changes_requested}` (ATLAS-148; one
   `fetch_comments` request per member, so the scan costs O(active), not
   O(board) — the pre-148 shape scanned every non-terminal ticket, ~108
   more requests per tick). Per included state: `ready_for_agent` is
   scanned because a dispatched agent may comment the moment it picks the
   ticket up, before the state flips. `in_progress` is scanned because it
   is where an agent actively works and files most follow-up proposals.
   `pr_open` is scanned because review discussion on a fresh PR is where
   split-this-out follow-ups surface. `review_required` is scanned because
   the reviewing agent or operator tags follow-ups while assessing the
   work. `changes_requested` is scanned because rework discussion
   routinely spawns deferred-scope proposals. Excluded: `backlog`,
   `planned`, and `blocked` are pre-dispatch — no agent has touched the
   ticket, so no agent comment can exist; a parked `needs_human_decision`
   is awaiting the operator, who admits follow-ups through the inbox gate
   directly rather than by tagging comments at the engine; terminal
   statuses are closed work and are not polled at all. A late follow-up
   tagged after a ticket leaves the active set is picked up if the ticket
   ever re-enters it (the comment-id dedup makes re-scanning safe), or
   admitted by the operator by hand — an accepted trade-off for the
   request budget.
5. Run anomaly and dwell checks (below).

Ticks are idempotent; a missed tick costs latency only. The scheduler is a
plain loop (or cron) — no distributed job system.

**Step → ticket map.** Steps 1+2 (pull a mapped status; push owned
definitions) are ATLAS-42 (`atlas/pm/sync.py`, `sync_tick`). Step 1's "log
anomalies otherwise" clause — an unmapped Linear state appends one
`OUT_OF_OWNERSHIP_TRANSITION` `DebtItem` per transition — is ATLAS-118 (woven
into `sync_tick`'s pull). AgentRun reconstruction after the pull is ATLAS-166.
Step 3 (readiness promotion, sole writer into `Ready for Agent`) is ATLAS-43.
Step 4 (the follow-up comment scan) is ATLAS-45.
Step 5's anomaly checks split by mechanism, all woven into `sync_tick`'s final
pass after `promote_ready`: dwell-breach logging is ATLAS-119 (a `_detect_dwell`
pass keyed on `Ticket.status_entered_at`; report-only, never moves a ticket),
review-cycling is ATLAS-120 (a `_detect_review_cycle` pass keyed on
`Ticket.review_cycle_count`, routing over-threshold tickets to
`needs_human_decision` via `set_state` and logging one `REVIEW_CYCLE` `DebtItem`
— the one anomaly that moves a ticket), and stale-block detection is ATLAS-44 (a
`_detect_stale_block` pass keyed on `blocked(graph, key)` over the same
dependency graph `promote_ready` consumes; report-only, never moves a ticket).
The recurring scheduler that calls `sync_tick` on a cadence is ATLAS-50
(`atlas/pm/scheduler.py`, driven by `atlas pm sync`). It
also owns create-on-crash: when a `sync_tick` raises, the scheduler records one
durable `TickFailure` (the append-only, system-attributed, tick-level crash
record — no ticket, so a separate model from `DebtItem`) and continues. That
record and its query-time dedup predicate (`recorded_since`, deduping by
`failure_signature` over a caller-supplied window) are ATLAS-125, a prerequisite
for ATLAS-50; the scheduler is the sole writer, and the count surfaces in the
delivery report (ATLAS-47).

The realized shape. The scheduler is a plain loop with an interruptible sleep
between ticks (default interval 60s, `--interval`); `now` is taken fresh per
tick. `--once` runs exactly one tick and exits, reusing the same single-tick
body as the loop. Graceful shutdown: SIGTERM/SIGINT set a shutdown flag the loop
consults only *after* the in-flight tick returns, so a signal finishes the
current tick and then stops — a tick is never abandoned mid-write (the next tick
would re-run it anyway, since ticks are idempotent). Create-on-crash dedup is
windowed by `CRASH_DEDUP_WINDOW` (a module constant, default **1 hour**): a
persistent crash records at most once per hour per signature, not once per tick,
via `recorded_since(signature, now - CRASH_DEDUP_WINDOW)`. The
`failure_signature` is the caught exception's fully-qualified type name, so a
recurring transient transport error dedups together while the specific message
is preserved in the record's `detail`. Two distinct bugs sharing one exception
type collapse to one signature inside the window — an accepted trade-off for a
dedup key that only bounds row volume. Every Linear HTTP call carries an
explicit transport timeout (`LINEAR_HTTP_TIMEOUT_SECONDS`, 30s), so a hung
request fails its tick instead of hanging the loop (ATLAS-147). A tick crashed
by Linear's rate limit (the typed `LinearRateLimitError`, detected from the
GraphQL errors' `RATELIMITED` code on both the transport-400 and
200-with-errors paths) stretches the next wait to the parsed reset — floored at
the base interval, capped at `RATE_LIMIT_MAX_BACKOFF_SECONDS` (1 hour), the
full cap when no reset parses — through the same interruptible sleep, instead
of retry-starving the request budget at the base cadence (ATLAS-147). The
end-to-end round-trip against real
Linear (a status change reflected in Atlas within one tick) is operator-run live
evidence (ADR-0008), not a CI proof.

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

After the review-cycle and dwell-breach clauses append their `DebtItem` rows,
the same step-5 anomaly pass files one DRAFT `Lesson` row for each newly
detected pattern instance. The draft is keyed by anomaly type plus sorted ticket
set, so a re-tick over unchanged state, or a later repeat of the same
pattern/ticket set, does not duplicate it. The row names the pattern, the ticket
keys, and the `DebtItem` ids as evidence pointers; it is a store row only
(`status = draft`, system-written by the PM Engine), never a document write, and
it triggers no automation. Promotion or discard remains the operator-owned
Learning System workflow.

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
state, ready-queue depth, anomaly counts, dwell breaches, tick failures, agent
runs, and the DRAFT lessons awaiting operator review. CLI/markdown output with
a `--json` form; no dashboard (Revision 1). A PURE READER — it makes no Linear
calls and writes nothing, computing every metric from stored tickets,
`DebtItem`s, transition rows, tick failures, `AgentRun`s, and `Lesson` rows
(never from the per-tick, ephemeral `SyncResult`), so it runs with no network
and no secrets.

The metrics, as computed in v1:

- **Throughput** — tickets currently `done`, bucketed by the ISO week
  (`YYYY-Www`) of `status_entered_at`. A `done` ticket with a null entry time
  falls in an `unknown` bucket rather than being dropped.
- **Cycle time per state** — true historical per-state cycle time from the
  `TicketStatusTransition` log (ATLAS-121/126), measured over **completed
  episodes**. ATLAS-121 made `apply_linear_status` append a transition on every
  real status change, so the log now records each state entry and exit; for a
  ticket's ordered transitions `T1..Tn`, episode `i` (`i` in `1..n-1`) is the
  state `to_i` entered at `t_i` and exited at `t_{i+1}`, contributing duration
  `t_{i+1} - t_i`. Aggregated per state across all tickets into min/median/max
  hours and an episode count (a state re-visited N times contributes N
  episodes; a state with no completed episodes does not appear). Two episodes
  are deliberately not counted: the initial state before `T1` (no recorded
  entry) and the current open episode after `Tn` (no recorded exit — the
  current-dwell the retired ATLAS-47 proxy reported). The computation is
  deterministic timestamp subtraction, so ADR-0005 holds by construction —
  nothing is assigned, a duration is measured.
- **Ready-queue depth** — the count of tickets in `ready_for_agent`.
- **Anomaly counts** — `DebtItem`s grouped by `AnomalyType`, with the recurring
  ones (the query-time `recurring(...)` predicate) called out per type. For
  `PACK_RENDER_FAILURE`, the same table also counts tickets whose definition
  cursor is still unstamped, i.e. degraded pushes still waiting for a successful
  full embed retry.
- **Dwell breaches** — the `DWELL_BREACH` subset of the anomaly log (ATLAS-119),
  surfaced per ticket.
- **Tick failures** — the count of durable scheduler crash rows.
- **Draft lessons** — DRAFT `Lesson` rows, including anomaly-draft rows filed
  from review-cycle and dwell-breach patterns. The report surfaces their title,
  pattern tags, related ticket keys, and `DebtItem` evidence ids for operator
  review; it does not promote, discard, or otherwise mutate the rows.
- **Agent runs** — the count of reconstructed `AgentRun` rows and the mean
  dispatch-to-handoff duration over rows with both `started_at` and
  `completed_at` populated. Partial observations count as rows but do not enter
  the mean.

## Open items

- Linear issue creation batching and rate limits — measure before tuning.
- Whether epic ↔ Linear project mapping is worth it in v1 (current
  position: labels only).

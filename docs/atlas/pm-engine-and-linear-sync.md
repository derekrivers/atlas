# PM Engine and Linear Sync Design (Phase 4)

Status: Active design document for Phase 4. Builds on ADR-0006 field
ownership and `symphony-integration.md#ticket-transitions-one-writer-per-state-edge`.

## Boundary

The PM Engine is a reconciliation loop, not an orchestrator. It promotes
ready work, mirrors state between Atlas and Linear under strict field
ownership, converts agent follow-up comments into planning inputs, and
detects delivery anomalies. It never executes work, never restarts agents,
and never writes to `docs/planning/` (that is `atlas apply`'s monopoly).

### Ticket publication is not delivery admission

Planning, Linear publication and delivery admission are separate authority
boundaries:

1. After exact operator approval, `atlas apply` mints Atlas tickets and assigns
   keys in the store. The complete apply-owned renders and inbox retirement are
   committed, reviewed and merged before PM publication.
2. Atlas PM is the sole authority for first publication of those minted tickets
   to Linear and their subsequent PM-owned definition and state-edge
   reconciliation. It creates or updates an issue, persists or reuses
   `external_linear_id`, pushes the Atlas-owned definition and context, and on
   first creation asserts the Linear state mapped to the ticket's current Atlas
   status. The field and state-edge ownership tables below continue to govern
   every later reconciliation edge.
3. Delivery admission is a later, independent PM decision. Only the active
   operator-owned policy, coherent snapshot, deterministic evaluator,
   revalidation and write-fence protocol may authorise promotion of an existing
   issue to `ready_for_agent`.

Creating or updating an issue, persisting its join key, and asserting its
mapped current Atlas state prove publication/reconciliation only. None is
permission to promote the ticket to `ready_for_agent`. In particular, a
create-time assertion of an already-current mapped state is not an admission
decision.

The ordinary sync tick contains both step 2's definition publication and step
3's admission protocol. Therefore a mint-only or publish-only intent has no
admission side effect: it MUST use a separately governed publication-only seam
when one is available, and otherwise fail closed rather than invoke a runtime
path that could admit work. This contract does not activate such a seam or
change the current tick.

Atlas PM publication cannot be replaced by raw `linear_graphql` issue creation
or workflow mutation. Those calls do not own PM definition publication,
`external_linear_id` persistence, state-map assertion, write fencing or later
reconciliation, even if the resulting board issue appears correct.

### Publication failure and recovery

An applied `PlanRun` is final and MUST NOT be re-applied to repair a repository
or Linear incident. Recovery starts from the minted Atlas ticket and the
existing apply artifacts.

Once Linear issue creation returns an id, PM retains that id as
`external_linear_id`. A degraded create caused by context-pack render failure
records no definition cursor, so the later definition retry updates that same
issue. If the create-time mapped-state assertion fails, current runtime retains
the join key but its existing-issue update path does not reassert workflow
state. The event therefore remains an explicit held
publication/reconciliation incident: do not re-run apply, create a replacement
issue or treat the retained identity as delivery admission. Current main has no
supported repair path for that assertion. Any separately delivered recovery
MUST reuse and reconcile that exact issue under PM authority and satisfy the
crash, fence and fresh-process requirements in
`pm-resilience-and-retrospective-recovery.md` before activation. Retaining the
identity makes non-duplicating recovery possible; it does not make that
recovery active here.

Every unexpected PM publication stop follows the disposition contract in
`docs/runbooks/planning-phases-and-ticket-stubs.md`: classify it before retry or
closure, retain bounded evidence, and name the PM code, operational runbook,
governance document, Lesson or delivery/debt surface that absorbs any durable
finding. A bare retry is not a disposition; only a proven transient external or
system event may close with no product change.

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

**Status lifecycle classification.** `TicketStatus` is persisted Atlas
vocabulary; membership does not imply a Linear workflow state. The executable
authority is `TICKET_STATUS_LINEAR_CLASSIFICATION` in
`atlas/linear/ownership.py`: `planned`, `ready_for_agent`, `in_progress`,
`pr_open`, `ci_pending`, `review_required`, `changes_requested`,
`needs_human_decision`, `done` and `rejected` are externally mirrored;
dependency `blocked` is derived; and `backlog` is Atlas-internal compatibility
vocabulary. `LinearStatusMap` rejects a configured Backlog or Blocked target at
construction, before validation or any write. `rejected` intentionally remains
many-to-one: both Linear `Canceled` and `Duplicate` map to it.

**Status (Linear → Atlas).** `LinearStatusMap` is an operator-configured
`dict[linear_state_id → TicketStatus]`, sourced from the JSON env var
`LINEAR_STATE_MAP` (e.g. `{"<state-uuid>": "in_progress"}`). The stable
Linear state **id** is the lookup key — never the customizable name
(rename-fragile), never the coarse type (`in_progress`, `pr_open`,
`ci_pending`, `review_required`, `changes_requested` all share Linear type
`started`, and the anomaly engine needs them distinguished). `status_from_issue(issue,
status_map)` reads only the state id and returns the mapped status or `None`;
an unmapped id is dropped, not guessed (ATLAS-42 counts and logs it; ATLAS-118
surfaces it as an anomaly).
Mapped CI-pending edges have an additional ownership gate in the actual pull.
The generic observation may mirror only the agent-owned `pr_open → ci_pending`
entry. Every other entry and every exit remains unchanged and appends one
deduplicated `out_of_ownership_transition` anomaly per observed state change;
even a mapped Review Required or Changes Requested observation is not proof of
the trusted Atlas CI classification that ATLAS-256 will own.
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

Atlas team states (eleven; Linear `type` in parentheses):

| Linear state | State id (UUID) | Maps to | Rationale |
| ------------ | --------------- | ------- | --------- |
| Planned (unstarted) | `f95de64e-f351-4226-b4ec-a5bddbc6fd2d` | `planned` | the normal first-publication target for apply-created work; publication is not delivery admission |
| Ready for Agent (unstarted) | `df1ebd92-7c41-4585-a15b-29b9e73f840f` | `ready_for_agent` | the step-3 promotion target; the PM Engine's one sanctioned outbound state write resolves to exactly this state |
| In Progress (started) | `381b59b4-7ffe-4247-9cd8-6a11585203ea` | `in_progress` | an agent is actively working the ticket; dwell-horizoned |
| PR Open (started) | `1ea72cdb-5f02-473f-8439-028e40d904f0` | `pr_open` | a PR is up; review-cycling counts arrivals into this state |
| CI Pending (started) | `85cdfa65-b990-41cc-a4ea-0071868ba27f` | `ci_pending` | a published PR awaits Atlas-owned system-tier CI classification; non-active for Symphony |
| Review Required (started) | `cf16f7da-6193-4dbf-b8fd-fa75dc9a16d7` | `review_required` | awaiting verification; step 3b's verified completion consumes it |
| Changes Requested (started) | `a3bba9c2-716e-47a6-b1ce-dcff4183c425` | `changes_requested` | rework requested; the other half of the review cycle |
| Needs Human (backlog) | `311a3a97-c409-4cce-96ab-0a3bfc2a5541` | `needs_human_decision` | parked for the operator; the review-cycling route target |
| Done (completed) | `ca6f5cee-5796-4102-bab7-24f08732549d` | `done` | delivered; terminal |
| Canceled (canceled) | `84207146-0b47-4821-a7e9-331abe38e77a` | `rejected` | closed undelivered; terminal |
| Duplicate (duplicate) | `cd8e7c95-8a25-48ad-b0ef-19e00f000e70` | `rejected` (operator adds post-merge) | a duplicate is work that closed undelivered under this key; the duplicate-of reason lives in Linear natively, not in a new Atlas status |

ATLAS-255 created the single `CI Pending` team state and pinned its returned
UUID above; the operator adds that exact `ci_pending` entry to
`LINEAR_STATE_MAP` after merge. The operator also adds the `Duplicate` entry
from the UUID documented above. The repository change edits no environment
configuration. The `rejected` mappings are intentionally
admitted by the accepted-types table: Linear reports type `canceled` (US
spelling) for the Canceled state and type `duplicate` for the Duplicate
state, and Atlas accepts exactly those two live spellings for
`rejected`. Preflight C2 therefore permits both terminal-negative
mappings while still rejecting `completed` or `started` states mapped to
`rejected`; the sync tick itself does not run that validation and is
unaffected.

There is no Atlas-team `Backlog` or `Blocked` state. Neither is omitted from
the map: both are deliberately non-mirrored under the classification above.

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

## Delivery admission policy ownership

Phase 15 separates dependency eligibility from delivery admission. The PM
Engine remains the only Atlas component permitted to promote an externally
eligible ticket to Linear `Ready for Agent`, but readiness alone no longer
grants delivery authority once the admission evaluator is connected. The
operator-owned `DeliveryAdmissionPolicyRevision` is the bounded input to that
later decision: approved Symphony ceiling, working/integration/review budgets,
Changes Requested reserve, exact risk/component lanes and mode for one product.

ATLAS-246 delivers only that versioned policy boundary. It adds no candidate
ranking and makes no Linear write. Policy creation and complete replacement run
through the Phase 13 command gateway with a server-resolved `human/operator`
actor, a hashed idempotency identity, canonical request fingerprint and
expected-revision compare-and-set. The immutable revision, active pointer and
successful append-only action receipt commit together. Stale revision, altered
replay or transaction failure produces no new authoritative policy.

The bootstrap revision is explicit `running` policy at ceiling/working/review
three; migration `0031` adds a conservative integration default of one without
rewriting that historical row or migration `0025`. It does not change the
`WORKFLOW.md` agent ceiling. `paused` and
`draining` are fail-closed inputs to admission and never trigger a status
transition, agent cancellation or workspace lifecycle action. The existing PM
sync promotion path is replaced by the coherent snapshot/admission protocol in
the later Phase 15 tickets; the policy module itself cannot import or invoke
Symphony.

## Coherent delivery occupancy snapshot

`build_delivery_snapshot` is the pure read-side boundary between the existing
project-scoped board pull and the later admission decision engine. The caller
freezes the list returned by `LinearClient.fetch_project_issues` in one
immutable `LinearBoardPull`; the envelope states whether the cursor chain
completed and retains any pagination-gap references. It does not issue another
request, and the builder accepts no Linear client or repository capable of a
write.

The builder maps each issue by the configured stable state id. Linear state
names are fingerprinted as observed provenance but never consulted to infer a
status. The issue's coarse state type corroborates the configured mapping using
the same contradiction table as status-map preflight. Working occupancy is the
sum of `ready_for_agent`, `in_progress`, `pr_open` and
`changes_requested`; integration occupancy independently counts only
`ci_pending`; review occupancy independently sums `review_required` and
`needs_human_decision`. CI-pending identities and headroom against the
operator-owned integration budget are explicit. Changes Requested has its own
count so the configured reserve remaining and new-admission working capacity
are explicit.

Every working issue joined to an Atlas ticket by `external_linear_id` consumes
all matching configured risk and canonical component lanes. Working and
CI-pending issues additionally consume every protected integration lane
selected from the joined ticket's component, tags and canonical declared paths
by `atlas/pm/protected_lane_registry_v1.json`. Invalid, ambiguous or
contradictory active declarations are typed snapshot incompleteness reasons.
No title or Linear identifier join is permitted. A complete status count includes every
`TicketStatus`, including zeroes, so no source order or omitted empty bucket can
alter the canonical result.

An Atlas ticket in any working, CI-pending or review state without an
`external_linear_id` is a typed `missing_external_linear_id` incompleteness
reason. The builder does
not infer Linear state or occupancy from Atlas state alone, so the observed
count remains zero and admission fails closed. Backlog, planned and blocked are
pre-delivery vocabulary that does not consume any occupancy budget. `planned`
is the normal externally mirrored state and may legitimately precede its first
publication tick; `backlog` is Atlas-internal compatibility vocabulary and
dependency blockedness is derived even though the historical `blocked` enum
member remains readable. A missing id in those states is not a join gap. After
an id exists, every non-terminal ticket still requires that exact id in the
complete project pull.

The immutable snapshot pins product/project, policy id and revision, the
byte-stable legacy policy fingerprint, an explicit canonical integration-budget
input, the status-map fingerprint, fetched-board fingerprint/count, sorted
CI-pending ticket identities, protected-lane registry and active-surface
fingerprints, Atlas store and graph revision fingerprints and an injected
observation time. The store revision includes tags, `relevant_docs` and
`documentation_requirements`, so protected classification input cannot move
under an otherwise matching key. Historical pre-0031 admission policy hashes remain
reconstructable, while any integration-budget change still changes the complete
snapshot fingerprint. It
reports every working, integration, review, risk, component and protected-lane
over-capacity dimension. Incomplete pulls,
pagination gaps, missing/duplicate issue identities, duplicate joins, unmapped
or contradictory states, working/CI-pending/review tickets without an external
Linear id,
absent joined issues, board issues without a local non-terminal join, and
Atlas/Linear status disagreement are typed incompleteness reasons. Any reason,
over-capacity result, paused mode or draining mode sets
`admission_allowed=false`. Construction performs no Linear write, ticket
mutation, demotion or Symphony action. Canonical compact JSON, sorted repeated
inputs and SHA-256 hashing make counts, reasons, revision pins and snapshot
fingerprint byte-stable for identical inputs regardless of iteration order.

## Deterministic admission calculation and record

The Phase 15 evaluator is a pure PM calculation after snapshot construction.
It derives candidates by calling the existing dependency
`ready_tickets(graph)` predicate; callers cannot submit or score an alternative
candidate list. Materialised tickets, the active policy, the one coherent
snapshot, explicit uninterrupted-eligibility starts and one injected clock
sample are its complete inputs. A missing/future eligibility start is rejected,
not approximated from unrelated ticket timestamps.

The snapshot pins canonical revisions of the complete product-ticket set and
the exact projected graph supplied to the dependency analyses. Before sampling
the clock or calling readiness, admission recomputes both revisions from its
live ticket/graph inputs. Membership, identity, status, acceptance-criteria,
rank/lane attribute, dependency topology or target-state drift raises typed
`AdmissionInputMismatchError`, retaining every mismatched revision; no
candidate is ranked, no selection is returned and no `AdmissionRun` exists to
persist. Only an exact coherence match can reach the zero/one decision path.

Ranking is unlock count descending, critical-path membership then zero-based
execution position, priority descending, risk severity low-to-critical,
eligibility start oldest first and the shared natural ticket-key order. The
evaluator retains those values in each decision. It then simulates working plus
one, checks full or breached integration pressure, remaining Changes Requested
reserve, review pressure and every exact
matching risk/component lane. It then classifies each candidate from trusted
component/tags and canonical `relevant_docs` / `documentation_requirements`
paths only. Every matched protected lane and the semantic registry fingerprint
are stored in the decision; ambiguous, contradictory or invalid declarations
are typed holds. A full lane retains its sorted current ticket owners in the
hold. Paused/draining mode, policy/snapshot mismatch,
each incompleteness reason, each full/breached budget or lane and missing Linear
identity are bounded typed holds. The existing rank tuple is unchanged. The
first reason-free candidate is the sole `admit`; later feasible candidates are
held by the single-write limit even if they occupy a different protected lane.

The returned immutable `AdmissionRun` pins policy and snapshot fingerprints,
records every ready candidate in rank order and names zero or one selection. A
canonical UUIDv5 makes identical injected inputs replay to byte-identical run
content. Calculation performs no Linear write and opens no repository. The
orchestration-level `record_admission_run` appends the returned model to
`admission_runs`; database triggers reject update/delete and the row contains
decision JSON only, never a raw Linear payload. Connecting this result to the
sync tick and its single external state write remains the later fail-closed
integration step.

## System-tier CI handoff reconciliation

`reconcile_ci_handoff` remains the one-candidate PM operation for Atlas-owned
exits from `ci_pending`, but production selection is owned by the durable
fairness scheduler used by both one-shot and recurring `atlas pm sync` ticks.
After the complete project pull and AgentRun reconstruction, one finite snapshot
contains only locally `ci_pending` tickets. Every eligible lifecycle/publication
episode receives a durable recovery identity from the latest authoritative
transition into `ci_pending` plus the exact issue-bound publication generation;
a legacy candidate with no transition history bootstraps deterministically once
from its durable ticket identity. Identity does not require an AgentRun and does
not change on process restart.

Within a product, normal order is the least durable fairness cursor:
`last_evaluated_sequence` after evaluation and otherwise
`episode_created_sequence`. A held or actionable evaluation moves that episode
to the product sequence tail. New arrivals allocate at the same tail behind
already-established older work, and reconstruction preserves the order. Across
products, durable observation-time rank supplies the outer scheduler described
in `pm-resilience-and-retrospective-recovery.md`. Ticket key is only a final
deterministic tie-break for corrupt or equivalent legacy input; it is not the
ongoing scheduler authority.

The authority split is explicit:

```text
durable fairness owner
        ↓
selected exact ticket
        ↓
existing CI-handoff adapter/reconciler
        ↓
publication/evidence/fence/workflow authority
```

Fairness decides which exact candidate receives this tick's evaluation turn; it
does not decide whether that candidate is safe to mutate. A complete board
observation may first catch the local mirror up from a Symphony-active
predecessor if the poll missed transient states; the direct transition names the
real observed source and the `pm-engine:linear-poll-compression` actor, never
invented intermediate rows. The ticket's issue-bound Linear GitHub attachment
must expose one exact repository/PR publication. The canonical URL and GitHub
metadata must agree, the metadata must identify an `open` or `draft`
`main`-target PR that closes the issue, the bounded attachment connection must
be complete, and the adapter joins it to the ticket only through the stable
Linear issue id. Missing, truncated, contradictory, multiple, closed or merged
publication identities hold before any GitHub request; titles, branches,
rollups, manual input and earlier handoff episodes are not identity sources.
Fair scheduling does not activate retrospective merged-publication recovery. A
historical merged publication remains ineligible in the ordinary lane, but its
held evaluation moves to the tail instead of monopolising every future tick.

For that exact publication the adapter invokes `drive_evidence_pull` inside the
supported tick. The canonical mapper persists normal product-scoped
system-tier GitHub evidence and returns the full contributor head plus every
exact source observation, including immutable rows reused by dedup. Provider or
malformed-source failure holds without a Linear mutation. The adapter supplies
the publication, full head, exact observed evidence ids and the tick's complete
project pull to the domain operation. The operation takes the shared product
admission lease, reconciles any earlier ambiguous fence first, loads the active
delivery policy, builds the coherent snapshot and revalidates the PR/head
without consuming a GitHub rollup. Only the pull-attributed product evidence is
classified through the canonical required-check resolver and system-tier
evaluators; explicitly ticket-scoped records still participate only for their
exact ticket.

Only a complete current-head `passed` set selects `review_required`; only a
complete determinate set containing an explicit implementation `failure`
selects `changes_requested`. Pending, missing, infrastructure, stale, malformed
and indeterminate sets select no target. The result is an immutable
`CIHandoffReconciliation` containing bounded reason/check projections and the
repository, PR, head, policy and snapshot identity. It contains no raw Linear
or GitHub response, exception text, token or log.

Before a selected decision can write, the operation fetches the PR and complete
board again, reloads the ticket and policy, rebuilds the snapshot and verifies
the lease. It repeats those checks across the final deterministic race seam,
then reloads product-scoped evidence and requires the classification, bounded
check results and deciding evidence ids to equal the selected assessment. Any
movement records a typed hold and performs zero state mutations; newer
same-head evidence is classified by a later fresh tick. A
`ci_handoff_write_fences` row is then committed before the strict writer can call only
`LinearClient.set_state(issue_id, review_required|changes_requested)`. A
confirming response updates the local observed status and clears the fence. An
exception or non-confirming response marks the fence `indeterminate`; a later
fresh complete board pull clears it only after proving the exact issue is at the
source, target or another state, and that fence-reconciliation tick never
attempts a second write.

Existing unresolved CI-handoff fences outrank ordinary fairness selection and
are reconciled before publication or evidence work, including when no ordinary
`ci_pending` candidate remains. Fenced products rotate by their durable outer
rank, but crash safety has precedence over throughput: every existing-fence
reconciliation attempt ends that tick, whether the fresh board proves source,
target, external movement or continuing ambiguity. Exact live lease and fence
identity guard retirement, and target confirmation commits the matching local
status with fence removal.

Admission fences exclude same-product ordinary CI evaluation until their named
owner reconciles them. Retained admission fences and independent-product CI
work share the durable outer rank, so a still-unresolved fence can defer only
its product while independent work eventually receives a turn; late admission
ambiguity ends the current CI attempt without advancing the displaced episode.
Every later workflow writer—definition creation and its create-only assertion,
admission, verified completion and review-cycle routing—uses the shared product
lease, rechecks both fence kinds at the write boundary and shares one latched
tick budget.

This seam has no GitHub mutation, Git, Symphony, policy, acceptance,
verification-waiver, merge or Done authority. The generic Linear pull continues
to reject both `ci_pending` exits; it does not become a second writer. **At most
one workflow effect per PM tick.** Fairness operates across repeated ticks, not
by performing multiple workflow mutations inside one tick. A confirmed
CI-handoff mutation, every prior-fence reconciliation attempt, or the first
completed downstream workflow route closes that tick's write window.
Admission, verified completion and anomaly routing therefore cannot perform a
second workflow mutation in the same tick.

### Evidence-backed Planned-to-CI-Pending mirror recovery

The generic pull still rejects `planned -> ci_pending`, and `planned` is not a
member of `CI_PENDING_POLL_COMPRESSION_SOURCES`. Before recording or
deduplicating that ownership anomaly, a separate predicate may recover the
local mirror only from a complete project observation and a uniquely
reconstructed governed delivery chain. Exactly one system-authored successful
`AdmissionRun` must select the stored ticket UUID and key, its product and
admitted decision's external Linear UUID must match the ticket, and exactly one
PM receipt joined by the run's exact evaluation/tick-start instant must be a
successful result with `admitted = promoted = 1`, `stale = 0` and
`indeterminate = 0`.

The observed board must contain one exact issue join at `ci_pending` and one
complete coherent GitHub attachment identity under the canonical issue-bound
publication-equivalence contract: live open/draft `main` PR, closes linkage,
and agreeing canonical URL/metadata. Titles, branches, descriptions, body text,
approximate timestamps and operator assertions are not inputs. Existing
AgentRuns are optional corroboration; the absence of a dispatch transition is
expected in this poll-compression shape and never causes Atlas to fabricate a
run. Duplicate/mismatched runs or receipts, missing/ambiguous publication,
incomplete/duplicate board identity or non-pre-dispatch local history refuses
recovery and leaves the ordinary `OUT_OF_OWNERSHIP_TRANSITION` behaviour in
force. Any active write fence encountered by the recovery predicate has the same
result. A CI-handoff fence already present when the tick performs its initial
fence scan instead retains absolute precedence before generic pull; its
reconciliation tick creates no synthetic ownership debt, and ordinary recovery
is reconsidered on a fresh tick after the fence is durably cleared.

On acceptance, `PlannedCIPendingRecoveryRepo` revalidates all durable local
inputs and compare-and-sets the ticket in the same transaction that appends the
single direct `planned -> ci_pending` transition and bounded immutable recovery
record. That record pins the admission run, PM receipt, ticket, product,
external issue, observed state, publication attachment/repository/PR, board
fingerprint/count and deterministic recovery identity. It stores no provider
payload, issue/PR body, secret or CI result. The transaction stamps only the
real `ci_pending` entry time and observed-state cursor; it creates no missed
state/timestamp, `AgentRun` or Linear write. A prior deduplicated anomaly and
all historical debt remain unchanged, and replay inserts nothing.

Recovery is mirror catch-up, not CI authority. AgentRun reconstruction still
runs afterward and creates no row without an `in_progress` transition. The
existing production CI-handoff adapter must independently re-resolve the
issue-bound publication, exact contributor head, complete current board,
policy/snapshot and system-tier checks before ATLAS-256 can write a later
`ci_pending -> review_required|changes_requested` edge.

### One-time ATLAS-280/ATLAS-281 bootstrap exception

The explicit operator bootstrap exception for ATLAS-280/ATL-456 and
ATLAS-281/ATL-457 is an incident-bound local-mirror repair, not generic PM
authority or precedent. It was introduced because the ticket that owns the
reusable governed repair, ATLAS-281, was itself blocked by ATLAS-280's local
`Planned`/external `CI Pending` mismatch. The reusable recovery contract is the
separate normal-cadence predicate above; it does not call this bootstrap seam.

The standalone bootstrap executable accepts no ticket identity. Its read-only
check reconstructs the fixed tickets, admission run and PM receipt, issue-bound
PR #350 head, historical anomaly, complete board observation, paused policy,
dependencies and absent write fences. A separately authorised apply may then
commit only the direct local `Planned -> CI Pending` transition and its
purpose-specific append-only receipt in one transaction. It writes neither
Linear nor GitHub, creates no `AgentRun`, invokes no CI handoff, and changes no
policy, lane, Symphony or other ticket state.

This exception does not add `planned` to
`CI_PENDING_POLL_COMPRESSION_SOURCES`, redefine an incomplete snapshot as
complete, or widen any normal state owner. It remains fixed historical incident
machinery; the reusable predicate above does not call or retarget it. After an
authorised bootstrap apply, only a subsequent normal PM cadence may invoke
ATLAS-256's CI reconciler for a `CI Pending` exit.

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

   A complete pull that observes `ci_pending` may catch the local mirror up
   from `ready_for_agent`, `in_progress`, `pr_open` or `changes_requested` —
   exactly Symphony's active predecessors — when transient intermediate states
   were compressed between polls. The one direct append-only transition keeps
   the actual source and poll-compression provenance. It creates no missing
   state rows, makes no Linear write and grants no CI-exit authority. Other
   entries and every exit still fail closed as ownership anomalies. A local
   `planned` source is considered only by the separate governed recovery
   predicate above. Its exact admission/receipt/publication/history/fence proof
   commits one direct local transition plus immutable audit row atomically; a
   failed proof follows the same deduplicated anomaly path and a prior observed
   state id does not suppress later proof-backed reconsideration.

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

   Existing CI-handoff fences are recovered before this ordinary pull. After
   the pull, the durable fairness owner establishes eligible episodes, selects
   at most one exact local `ci_pending` ticket, and passes that ticket to the
   existing adapter. The adapter resolves ordinary publication identity and
   evidence directly from bounded trusted provider observations, so a
   compressed observation with no reconstructed AgentRun remains recoverable.
   It either records a typed durable hold or delegates to the system-tier
   reconciler; held evaluation moves the episode to the durable tail. A
   same-product admission fence excludes the candidate, while its durable outer
   rank still permits an independent product to receive a later turn. A
   confirmed write, any prior-fence reconciliation attempt, late admission
   ambiguity or lease-loss outcome closes the tick's workflow-write window.
   Otherwise the tick may continue to definition and admission work without
   considering another CI candidate. `GITHUB_TOKEN` is therefore a production
   `atlas pm sync` precondition alongside the Linear credentials; the GitHub
   client is read-only and is not called for an unresolved identity.

2. Push definition updates (title/priority/labels/description) for
   tickets whose Atlas `updated_at` is newer, only while the ticket is in
   the externally mirrored `planned` pre-dispatch state or `Ready for Agent`.
   `backlog` and `blocked` are deliberately not `PUSHABLE_STATUSES`, so
   definition publication cannot accidentally turn either enum member into a
   requirement for a nonexistent Linear target. A successful full embed
   stamps `linear_synced_at` to the pushed `updated_at`. An enumerated
   context-pack render failure still pushes the definition-only payload
   and logs one `PACK_RENDER_FAILURE`, but does **not** stamp the cursor:
   `updated_at` remains ahead of `linear_synced_at`, so the next tick retries
   the full embed until the render condition clears. A first-sync degraded
   create records only the Linear join key, never the cursor, so the retry
   updates the same issue instead of creating a duplicate. On first sync only,
   immediately after a successful `create_issue`, the PM Engine durably records
   that returned join key (and the definition cursor for a full embed) before it
   resolves and asserts the Linear workflow state mapped to the ticket's current
   Atlas status via `LinearClient.set_state`; the update path never writes
   workflow state.
   Because every pushable status is externally mirrored, this inverse lookup is
   fail-closed and unique. In particular, apply-created work asserts `Planned`;
   it does not assert `Ready for Agent` until the separate admission step.
   If that create-time assertion fails after the issue is created, the join key
   remains recorded and the tick logs/counts the failed assertion as an anomaly,
   so the issue is never orphaned.

   `atlas pm sync --repair-packs` adds an operator-invoked, one-shot repair
   sweep after this normal push pass. The sweep examines only descriptions
   already returned by the batched project pull, selects pushable tickets with
   an `external_linear_id`, a current definition cursor, and no
   `ATLAS CONTEXT PACK v1` header in Linear, then re-renders and re-pushes the
   full embedded description. Successful repairs stamp normally; a second
   repair run over the same board is a zero-write no-op. The plain periodic
   tick does not run this branch and therefore keeps the ATLAS-148 request
   budget unchanged.
3. Enter the lease-guarded single-write admission protocol. Both the recurring
   scheduler and `--once` call this exact `sync_tick` step. One product-scoped
   `admission_leases` row admits an evaluator owner; a concurrent owner records
   `lease_unavailable` as a typed held/no-write result and does not evaluate a
   second candidate set. The owner freezes step 1's complete project pull,
   builds the coherent delivery snapshot, reconciles uninterrupted eligibility
   episode starts, invokes the deterministic evaluator and appends its exact
   `AdmissionRun`. An incomplete/discontinuous pull or snapshot records stale
   and writes nothing.

   If the run selects a ticket, re-fetch the complete project board, reload the
   protected-lane registry and active policy immediately before mutation. The
   second snapshot must exactly match
   the first fingerprint, including candidate state, every occupancy value,
   policy/status-map/registry fingerprints, protected-lane owners and Atlas
   store/graph revisions. Re-read the registry, ticket surfaces and policy and
   verify the lease owner once more after the deterministic race boundary.
   Registry movement records `protected_lane_registry_changed`; active-surface
   movement records `protected_lane_state_changed`. Either returns before the
   durable fence. Policy movement, candidate movement, lease loss, a malformed
   or partial page chain, or any pre-write transport failure likewise records
   `stale`, ends the admission step and writes no state.

   Before mutation, persist an `admission_write_fences` row naming the exact
   run, candidate issue, observed source state and target. Then call only
   `LinearClient.set_state(selected_issue_id, ready_for_agent_state_id)`. The
   target is resolved by inverting the configured status map (exactly one state
   must map to `ready_for_agent`). A confirming response clears the fence and
   reports `admitted`; a transport failure or non-confirming response retains
   it as `indeterminate`, records a partial receipt and stops the tick. A later
   complete step-1 pull must reconcile that exact issue before the fence is
   cleared, and the reconciliation tick performs no new admission.

   The definition is already mirrored by step 2 and step 3 cannot carry a
   definition field: `stateId` remains outside the owned-definition allow-list.
   The write is Linear-only; step 1 of the next tick remains the sole Atlas
   status writer. Setting the same target on a later retry is idempotent, but a
   stale or ambiguous run never falls through to a different candidate.

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

After step 5, before the tick reports success to its caller, the PM Engine
appends one bounded `PmSyncReceipt`. The receipt stores the injected entry-time
logic clock and a second completion-clock sample taken after the body finishes,
the configured Linear project id, the unambiguous product
identity when one can be resolved, a SHA-256 fingerprint of the status map, a
SHA-256 fingerprint of the fetched board's ids/identifiers/state metadata, the
fetched issue count, the `SyncResult` integer counters, and a bounded result
classification. It deliberately excludes Linear descriptions, issue bodies,
comments, raw payloads, tokens and credentials.

The fixed counter shape includes `admitted`, `held`, `over_capacity`, `stale`
and `indeterminate`; `promoted` remains the compatibility projection of
`admitted`. One safe presentation detail carries the bounded reason, selected
ticket key and policy revision/fingerprint. A stale revalidation or unresolved
write is a partial receipt and does not advance freshness. A coherent policy or
capacity hold is a successful zero-action observation, and a confirmed
admission is successful status-only.

An unsuccessful receipt stores no arbitrary exception message. Its optional
diagnostic is limited to a sanitized exception type and a controlled local
error code; raw HTTP bodies, GraphQL errors and credential-bearing exception
text are excluded even when the Linear client includes them in `str(error)`.

Receipt persistence is part of the local completion boundary. If the tick body
completed but the receipt write fails, `sync_tick` returns a typed receipt
persistence failure rather than reporting the tick as successful. Successful
definition-changing, status-only and zero-action ticks are the only receipt
classes that advance `last_successful_linear_sync_at`; degraded pack-render,
unmapped-state, missing-issue, stale-admission, indeterminate-admission,
malformed-pull, cancelled and failed ticks keep their diagnostic receipts but
do not advance freshness. `Ticket.linear_synced_at` remains only the
definition-push cursor and is not a board freshness signal.

Ticks are idempotent only at seams whose prerequisites and ambiguous outcomes
are durably reconstructable; a missed tick costs latency only there. The
stronger recovery, eventual-convergence and health contract is owned by
`pm-resilience-and-retrospective-recovery.md`; an unsafe or unreconstructable
interruption may instead leave a durable blocker. The scheduler is a plain loop
(or cron) — no distributed job system.

**Step → ticket map.** Steps 1+2 (pull a mapped status; push owned
definitions) are ATLAS-42 (`atlas/pm/sync.py`, `sync_tick`). Step 1's "log
anomalies otherwise" clause — an unmapped Linear state appends one
`OUT_OF_OWNERSHIP_TRANSITION` `DebtItem` per transition — is ATLAS-118 (woven
into `sync_tick`'s pull). AgentRun reconstruction after the pull is ATLAS-166.
Step 3's original readiness writer is ATLAS-43; ATLAS-249 replaces its
promote-everything call site with the lease/revalidation/fence protocol while
preserving `LinearClient.set_state` as the dedicated ownership boundary.
Step 4 (the follow-up comment scan) is ATLAS-45.
Step 5's anomaly checks split by mechanism, all woven into `sync_tick`'s final
pass after admission: dwell-breach logging is ATLAS-119 (a `_detect_dwell`
pass keyed on `Ticket.status_entered_at`; report-only, never moves a ticket),
review-cycling is ATLAS-120 (a `_detect_review_cycle` pass keyed on
`Ticket.review_cycle_count`, routing over-threshold tickets to
`needs_human_decision` via `set_state` and logging one `REVIEW_CYCLE` `DebtItem`
— the one anomaly that moves a ticket), and stale-block detection is ATLAS-44 (a
`_detect_stale_block` pass keyed on `blocked(graph, key)` over the same
dependency graph admission consumes; report-only, never moves a ticket).
The recurring scheduler that calls `sync_tick` on a cadence is ATLAS-50
(`atlas/pm/scheduler.py`, driven by `atlas pm sync`). It
also owns create-on-crash: when a `sync_tick` raises, the scheduler records one
durable `TickFailure` (the append-only, system-attributed, tick-level crash
record — no ticket, so a separate model from `DebtItem`) and continues. That
record and its query-time dedup predicate (`recorded_since`, deduping by
`failure_signature` over a caller-supplied window) are ATLAS-125, a prerequisite
for ATLAS-50; the scheduler is the sole writer, and the count surfaces in the
delivery report (ATLAS-47).
`PmSyncReceipt` is different from `TickFailure`: it is written by `sync_tick`
itself for every completed local boundary, including successful no-op ticks,
partial ticks, malformed pulls and failures that were caught at the sync
wrapper. The scheduler's `TickFailure` remains the crash ledger used for
deduped scheduler reporting.

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
  inbox as the separate input source above, and the normal `atlas apply` path
  moves applied or operator-rejected stubs to
  `docs/planning/inbox/processed/`. The stale-proposal disposition path
  (`atlas apply --reject-stale`) finalises only the stale `PlanRun`; it does not
  retire stubs, because the proposal was not considered on its merits. The
  accepted failure mode is deliberate re-reading of any still-active stub by the
  next fresh plan unless the operator removes or edits it. ATLAS-45 does
  **not** wire either side.

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
the same step-5 anomaly pass triggers lesson extraction for each newly observed
failure-analysis event. The extractor calls an LLM over a bounded evidence
bundle and persists a schema-valid DRAFT `Lesson` with `confidence = null` and
the source ticket in `source_ticket_id`; `related_ticket_ids` remains reserved
for later citation feedback. A re-tick over unchanged state does not retry
because no new `DebtItem` is appended. The row is store-only, never a document
write, and promotion or discard remains the operator-owned Learning System
workflow.

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
- Stale block (ATLAS-44, compatibility hygiene): when a historical ticket sits
  in the retained `blocked` enum value but its structural blockers have all
  cleared — i.e. `blocked(graph, key)` (the
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
  whether to move it. This detector does not make stored `blocked` the normal
  dependency model: no production path writes it, no Linear state maps to it,
  and a planned ticket with unfinished dependencies remains `planned`. The
  inverse direction needs no stored transition: `is_ready` already refuses to
  promote it and `blocked(graph, key)` derives the reasons. This is distinct
  from blocked-dwell ("blocked too
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
- **Draft lessons** — DRAFT `Lesson` rows awaiting operator review, including
  lesson-extraction rows from completion, rejection, review-cycle, and dwell
  events. The report surfaces their title, pattern tags when present, related
  ticket keys, and `DebtItem` evidence ids when present; it does not promote,
  discard, or otherwise mutate the rows.
- **Agent runs** — the count of reconstructed `AgentRun` rows and the mean
  dispatch-to-handoff duration over rows with both `started_at` and
  `completed_at` populated. Partial observations count as rows but do not enter
  the mean.

## Open items

- Linear issue creation batching and rate limits — measure before tuning.
- Whether epic ↔ Linear project mapping is worth it in v1 (current
  position: labels only).

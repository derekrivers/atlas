# Multi-Agent Delivery Control Design (Phase 15)

Status: Planned Phase 15 design authority. Defines operator-owned delivery
policy, coherent occupancy, deterministic admission to `Ready for Agent`,
review-pressure protection and the controlled Symphony ceiling ramp.

## Purpose and milestone

Phase 15 replaces promote-everything readiness with bounded admission. A
ticket that satisfies the Phase 3 readiness predicate is eligible; Atlas may
promote it only when one coherent delivery snapshot and the current operator
policy show room in every applicable budget.

The phase closes only when a live controlled wave with more than ten
independent tickets first proves the serialized one-agent baseline and then
proves the ceiling can move to 3, 5, 7 and 10 without exceeding working,
integration, review or lane budgets, starving Changes Requested work or granting Atlas scheduling,
review or merge authority. The milestone/closure change then lands
`WORKFLOW.md` with `max_concurrent_agents: 10`. Closure below ten is prohibited.

## Authority and ownership

- The operator owns policy values, revisions, mode and ceiling changes.
- The PM Engine remains the sole writer into Linear `Ready for Agent`.
- Symphony remains the scheduler, worker owner and workspace-lifecycle owner.
- Linear remains the source of ticket workflow status; Atlas never invents a
  fresher local status when the pull is incomplete or stale.
- Phase 13 authentication, actor context, idempotency and action receipts own
  every browser policy mutation.
- Phase 14 owns exact-head acceptance. Admission may observe its review queue
  but cannot confirm, verify, rebase or merge a PR.

No policy value is inferred from an LLM or changed by an agent.

## Successful sync receipt

`last_linear_sync_at` must stop reusing the most recent ticket-definition
cursor. A durable `PmSyncReceipt` records one completed tick with:

- receipt ID, start and finish timestamps;
- product/project identity and status-map fingerprint;
- fetched-board fingerprint and ticket count;
- result counters and a bounded success/failure classification.

The start and finish are separate injected clock samples: finish is taken only
after the tick body completes (or at its unsuccessful receipt boundary). Failed
receipt diagnostics contain only a sanitized exception type and controlled
local error code, never arbitrary Linear exception text or response payloads.

Only `success_definition_changed`, `success_status_only` and
`success_zero_action` receipts advance `last_successful_linear_sync_at`. Failed,
cancelled, malformed-pull and partial ticks still write diagnostic receipts but
do not advance freshness. No-op and status-only successful ticks therefore
count as fresh complete observations, while `Ticket.linear_synced_at` remains
only the definition-push cursor. The Operator API keeps the existing
`last_linear_sync_at` response field and projects it from the latest successful
receipt's `finished_at`; the UI consumes that field directly.

## Delivery admission policy

One active `DeliveryAdmissionPolicy` exists for the Atlas product. Each change
creates an immutable revision and an append-only Phase 13 action receipt.

The policy contains:

- `revision`, `mode` (`running`, `paused`, `draining`) and `created_at`;
- `working_budget`, bounded from 1 to the approved Symphony ceiling;
- `integration_budget`, independently bounded from 1 to 10;
- `review_budget`, bounded from 1 to 10;
- `changes_requested_reserve`, bounded from 0 to `working_budget`;
- zero or more exact risk-lane and canonical component-lane limits, each
  bounded from 0 to `working_budget`; and
- the operator-approved Symphony ceiling recorded for that policy revision.

Risk selectors use the four closed `RiskLevel` values and may occur once each.
Component selectors are NFKC-normalised, trimmed and case-folded exact strings;
selectors that canonicalise to the same value are ambiguous and rejected. The
policy accepts at most four risk rules and 64 component rules. The approved
ceiling, budgets, reserve and lane limits are strict integers: booleans and
numeric strings are not accepted as capacity.

The store uses an append-only `delivery_admission_policy_revisions` history and
one mutable `delivery_admission_policy_active` pointer per product. A composite
foreign key prevents the pointer from selecting another product's revision.
Database triggers reject update or deletion of history on both SQLite and
PostgreSQL, and the public repository exposes reads only. Migration `0025`
creates explicit revision one for every existing product with mode `running`,
ceiling `3`, working budget `3`, review budget `3`, reserve `0` and empty lane
sets. Migration `0031` adds the integration budget from head `0030` with a
conservative compatibility default of one and no historical-row update,
delete, table recreation or edit to `0025`. Both migrations change neither
`WORKFLOW.md` nor live Symphony configuration, so they cannot raise the
existing ceiling as a side effect.

Revision one is immutable historical bootstrap data, not the current live
Symphony ceiling. ATLAS-054M later set the live declaration to one. Before any
Phase 15 milestone activity, the operator must append a new policy revision
with `approved_symphony_ceiling=1`, `working_budget=1` and valid reserve/lane
limits, then move the active pointer to that revision through the owning
operator control. The recorded revision and fingerprint must be observed to
match the live one-agent declaration before Gate 1 begins. The milestone is
blocked if that reconciliation cannot be proved; migration `0025` and revision
one are never updated, deleted or relabelled as the current policy.

Every post-bootstrap creation or replacement goes through the Phase 13
operator-action gateway as `delivery_admission_policy.revise`. The service
supplies the authenticated single-operator actor (`human/operator`); callers
cannot submit actor identity. The canonical fingerprint covers the product,
complete validated policy and `expected_revision`. The transaction locks the
product before reading its active pointer, compares zero for creation or the
current monotonic revision for replacement, then commits the new history row,
advanced pointer, idempotency reservation and append-only action receipt
together. A stale expected revision returns a recorded conflict with no new
policy row. Exact replay returns the original receipt and revision; reuse of the
key for an altered fingerprint returns conflict without invoking the command.
Mutation, receipt-flush or commit failure rolls back the entire transaction and
leaves the prior pointer authoritative.

If existing occupancy already exceeds a new limit, the later admission
evaluator reports over-capacity and admits nobody; policy replacement itself
never demotes, cancels or terminates work. `running` permits evaluation to
continue to occupancy checks. `paused` and `draining` both stop new admission.
Draining additionally communicates operator intent that the current active set
should finish; neither mode changes an existing ticket, workspace or agent, and
the policy service has no Symphony or Linear dependency.

## Occupancy snapshot

One admission evaluation consumes a single immutable `DeliverySnapshot` built
from the project-scoped Linear pull, materialised Atlas tickets, the exact
projected dependency graph, current policy revision and status-map revision.
The existing
`LinearClient.fetch_project_issues` request remains the only board read: its
materialised result is frozen in a `LinearBoardPull` envelope that records
whether pagination reached `hasNextPage=false` and any discontinuous cursor
references. Snapshot construction is a pure calculation over that envelope and
materialised local state; it performs no Linear write, Atlas ticket mutation,
demotion or Symphony action.

Working occupancy counts tickets in:

- `ready_for_agent`;
- `in_progress`;
- `pr_open`; and
- `changes_requested`.

Review occupancy counts `review_required` and `needs_human_decision`. These
are separate budgets: review pressure can stop new admission even while a
Symphony slot is free. Changes Requested tickets consume working capacity
before new candidates, and the configured reserve cannot be consumed by new
admissions.

Integration occupancy separately counts only `ci_pending`: published PRs whose
required CI evidence has not reached a terminal Atlas classification. It does
not consume working, review, risk-lane or component-lane occupancy. A full or
breached integration budget stops new admission, but never cancels, demotes or
rewrites an existing ticket. Lowering the budget can therefore report an
over-capacity queue while all current work continues unchanged.

Protected repository lanes are a separate Phase 15.5 capacity dimension.
Working and `ci_pending` tickets consume every lane selected from their trusted
component, tags and canonical `relevant_docs` / `documentation_requirements`
paths. CI-pending therefore releases its Symphony working slot and its
risk/component occupancy while retaining every protected integration lane it
may still contend on. Each protected lane is bounded by the versioned
repository registry, not by inferred utilisation or a model decision.

Every active ticket also consumes every matching risk and component lane.
Lane matching uses the ticket joined by `external_linear_id`, never its title or
Linear identifier. Component values use the policy's NFKC/trim/case-fold
canonical form. Changes Requested occupancy is retained separately, and the
snapshot derives both the remaining Changes Requested reserve and working
capacity available to a new admission after that reserve.

Every Atlas ticket in a working, CI-pending or review status must have an
`external_linear_id`. If it does not, the snapshot reports a typed
`missing_external_linear_id` reason and fails closed without guessing occupancy
from the Atlas status alone. Backlog, planned and blocked tickets are outside
delivery occupancy and may legitimately precede Linear issue creation, so a
missing id in those states is not an occupancy join gap. Once any non-terminal
ticket has an id, however, absence of that exact issue from the complete project
pull remains a typed `missing_joined_issue` failure.

The snapshot pins product id, Linear project id, immutable policy id/revision
and mode, the byte-stable legacy policy fingerprint, an explicit canonical
`integration_budget` input, the configured state-id map fingerprint, the
fetched-board fingerprint and count, sorted CI-pending Atlas ticket identities,
the protected-lane registry version/fingerprint and active-surface
fingerprint, Atlas store and graph revision fingerprints, and an injected UTC
observation time. Keeping the pre-0031 policy hash contract stable makes historical
`AdmissionRun.policy_fingerprint` values reconstructable; the explicit snapshot
field still makes every integration-budget change alter the snapshot hash. The store
revision covers complete product-ticket membership, ticket and Linear
identities, status, acceptance criteria, priority, risk, component, tags,
declared paths and effort.
The graph revision covers every projected node identity and readiness/rank
attribute plus dependency topology, type, identity and reason, including
ticket and ADR target state. Its complete canonical JSON representation is
key-sorted and compact, all repeated fields are sorted, and its SHA-256
fingerprint excludes no decision field. Identical inputs therefore produce
byte-identical counts, reasons, canonical bytes and fingerprint regardless of
source iteration order.

Unknown or unmapped state ids, state-id/type contradictions, incomplete pulls,
pagination gaps, missing or duplicate issue identities, duplicate Atlas joins,
working, CI-pending or review tickets without an external Linear id, missing
joined issues,
unjoined non-terminal board issues, and disagreement between the joined Atlas
and Linear status are typed incompleteness reasons. Any such reason sets
`admission_allowed=false`; display names are provenance only and are never
status lookup keys. Existing occupancy above the working, integration, review,
risk-lane, component-lane or protected-lane limit reports every breached
dimension and also prohibits admission. Paused or draining policy likewise
makes the snapshot ineligible for admission without misclassifying the coherent
observation as incomplete.

## Deterministic admission decision

Candidates are the exact results of the existing Phase 3 readiness predicate;
Phase 15 does not reimplement dependency readiness. The engine returns one
append-only `AdmissionRun` with the snapshot/policy fingerprints and one
decision per candidate.

`evaluate_admission` calls `ready_tickets(graph)` itself. Its considered key
set is therefore exactly the existing Phase 3 result; a caller cannot supply a
broader candidate list or turn a readiness failure into a policy hold. The
matching materialised `Ticket` supplies only policy/rank attributes. The caller
must supply an aware `continuously_eligible_since` value for every ready key;
the evaluator rejects a missing or future value rather than guessing from the
ticket's creation or status-entry timestamp.

Before sampling the evaluation clock or invoking readiness, the evaluator
recomputes the canonical product-ticket and exact projected-graph revisions
from its live inputs and compares both with the snapshot pins. Any membership,
identity, status, acceptance-criteria, rank/lane attribute, dependency edge or
dependency-target state drift raises typed `AdmissionInputMismatchError` with
every mismatched revision. Rejected inputs produce no ranking, selection or
`AdmissionRun`; the orchestration layer therefore cannot persist a run whose
snapshot fingerprint describes different state from the decision inputs.

Candidates are ordered by this stable tuple:

1. number of currently blocked non-terminal tickets the candidate would
   unlock, descending;
2. membership and position on the current critical path, critical first;
3. Atlas priority, descending;
4. risk severity (`low`, `medium`, `high`, `critical`), lower first;
5. uninterrupted eligibility start, oldest first; and
6. ticket key through the shared natural-key ordering, ascending.

Critical-path position is its zero-based execution-order position, earlier
first; a non-member sorts after every member. A configured risk lane does not
rewrite rank or invert severity: it is an exact capacity permission evaluated
after ranking. Rank inputs store the unlock count, membership/position,
priority, risk ordinal, eligibility start and exact injected-clock age, so the
order is reconstructable without consulting Linear list order or a model.

The engine never uses an agent score, model opinion, title similarity or
Linear display order. Each decision is `admit` or `hold`. Protected-lane
classification inspects only stored component/tags and the canonical paths in
`relevant_docs` and `documentation_requirements`; objective, context,
acceptance prose and implementation notes are not classifier inputs. A single
declaration that selects different lanes is ambiguous, non-canonical values
are invalid and canonical duplicates with contradictory spellings are
contradictory. All fail closed. Distinct declarations may validly select
multiple lanes, which are stored in deterministic lane order with the registry
version and semantic fingerprint. Typed reasons retain
paused/draining mode, policy/snapshot mismatch, every snapshot-incompleteness
reason, full or breached working/integration/review budgets, remaining Changes Requested
reserve, every matching risk/component/protected lane, missing external identity
and the single-write limit. Each saturated protected-lane reason names the
lane, simulated count, bound and all current owning ticket keys. A candidate is
simulated at working occupancy plus one and against every matched protected
lane before `admit` is returned; a multi-lane candidate acquires feasibility
for all lanes or none. Review occupancy is a pressure gate even though
promotion does not increase it. Existing over-capacity dimensions remain
reasons for every candidate; no evaluator response demotes existing work.

An admission pass selects at most one external promotion. This deliberately
trades a few five-second polling intervals for a safe external-write boundary:
Linear offers no multi-issue transaction, so Atlas never constructs a batch
that could partially succeed.

The highest ranked candidate with no reason is selected. Lane classification
does not alter the stable rank tuple. Evaluation continues after held
candidates; after selection, every otherwise-feasible lower-ranked
candidate receives `single_write_limit`. The immutable `AdmissionRun` records
all considered candidates in rank order, every matched protected lane and
registry identity, the zero/one selected ticket, exact policy/snapshot
fingerprints and the one injected evaluation timestamp. Its id
is UUIDv5 over the canonical decision payload, so random UUID generation and
timestamps outside the injected clock cannot affect ordering or decisions.
`admission_runs` rejects update/delete on SQLite and PostgreSQL. The evaluator
has no repository; `atlas.orchestration.record_admission_run` appends the
already-returned run and stores bounded decision JSON, never raw Linear
payloads.

## PM-sync write protocol

The periodic and `--once` paths enter the same `sync_tick` body and acquire the
same product-scoped row in `admission_leases`. The row has an opaque owner and
expiry; an expired owner may be replaced atomically, while a concurrent live
owner records a typed `lease_unavailable` held/no-write outcome and never runs
the evaluator. The owner reconciles `admission_eligibility` rows before
evaluation so every ready candidate has the start of its uninterrupted
eligibility episode rather than a guessed ticket timestamp.

For a selected candidate the PM Engine:

1. freezes the normal initial project pull, rejects incomplete or discontinuous
   pagination, builds the complete snapshot and records the deterministic
   `AdmissionRun`;
2. re-reads current policy and the complete project-scoped Linear board
   immediately before the write;
3. requires the second snapshot fingerprint, policy fingerprint/revision,
   candidate state, Atlas store/graph revisions and every occupancy count to
   match the decision inputs, then verifies the lease owner again;
4. commits one `admission_write_fences` row naming the selected issue, source
   state and unique `Ready for Agent` target before making any external call;
5. calls only `LinearClient.set_state(selected_issue_id,
   ready_for_agent_state_id)`, confirms the returned issue/state, clears the
   fence and records `admitted` in the successful sync receipt; and
6. relies on the next normal pull to reconcile Atlas status, preserving the
   existing single-writer boundary. An admitted, stale or indeterminate result
   ends the tick at this boundary, so later status routes cannot become a
   second external mutation in the same decision window.

A stale second read, policy revision, lease loss, candidate movement or
malformed/partial Linear response records `stale` and produces no write. A
transport failure or non-confirming response after `set_state` marks the fence
`indeterminate`, records a partial sync receipt, admits no second candidate and
blocks further admission. A later complete normal pull must observe the exact
issue: the target or a downstream Symphony-active state reconciles as admitted;
the original state reconciles as no-write; an absent, unmapped or contradictory
issue leaves the fence blocking. The reconciliation tick itself performs no new
admission. Retrying the state change on a later tick is idempotent, and the
protocol never attempts a compensating status write.

`SyncResult` and `PmSyncReceipt.counters` expose fixed integer counters for
`admitted`, `held`, `over_capacity`, `stale` and `indeterminate` (with the
legacy `promoted` counter retained as the admitted compatibility projection).
One bounded presentation detail names only outcome/reason, ticket key and
policy revision/fingerprint; it excludes descriptions, issue bodies, exception
text, tokens and credentials.

## API and UI contract

The authenticated API adds a narrow delivery-control resource:

```http
GET  /api/v1/delivery-control
POST /api/v1/delivery-control/policy
```

The read returns the active policy revision, approved ceiling, mode, truthful
last-successful-sync timestamp, current occupancy, latest admission decisions
and all hold/over-capacity reasons. The POST accepts a complete policy plus
`expected_revision`; Phase 13 supplies actor, CSRF, origin and idempotency.
There is no generic patch or ticket-state action.

The returned `approved_symphony_ceiling` is the active Atlas policy value. It
is not an independent observation of the configured or running Symphony
ceiling: `WORKFLOW.md.agent.max_concurrent_agents` remains authoritative for
configuration, and this API reads neither `WORKFLOW.md` nor Symphony. Until the
operator appends the governed reconciliation revision required above, a
historical policy value of three may truthfully coexist with the serialized
one-agent baseline without implying that Symphony is running three workers.

The delivered API read requires the live shared session and is always
`no-store`. It is deliberately observational: one orchestration operation
consumes one storage-owned repeatable-read snapshot containing the active
policy, product-scoped sync receipts, materialised ticket statuses, latest
immutable admission run, latest per-ticket CI reconciliations observed no
earlier than each current `status_entered_at` episode, their selected
evidence identities, stored acceptance assessments and unresolved write
fences. It does not acquire the admission lease, refresh Linear or GitHub,
calculate or execute validation, rerun an evaluator, append a run or receipt,
transition a ticket, or mutate policy. Because no raw board is stored, the
occupancy projection explicitly names its source as
`materialized_atlas_statuses` and uses the successful-sync timestamp to convey
freshness rather than claiming a new Linear observation.

The read response is bounded. It returns at most the first 100 rank-ordered
decisions from the latest run, plus the stored total and a truncation flag.
Every included decision carries the complete fixed persisted rank-input
projection: unlock count, critical-path membership and position, priority, risk
level and severity, and continuously-eligible timestamp and age. The operator
UI therefore explains the server ordering without reconstructing it or reading
raw Linear identity. Within those decisions, duplicate per-issue snapshot
defects collapse to each distinct closed source code; all typed hold codes and
capacity selectors remain visible. Current over-capacity dimensions are
recomputed from the same working, integration, review, reserve and lane definitions.
A durable unresolved write fence is exposed as `write_indeterminate`, whether
its stored fence state is `pending` or `indeterminate`. Raw Linear issue/state
identities, board payloads, pagination cursors, exception summaries,
credentials and browser secrets are excluded.

Phase 15.5 adds a composite snapshot fingerprint over exact policy, board,
evidence and integration identities. The board projection retains the last
successful receipt and a newer unsuccessful attempt separately. The evidence
fingerprint covers only bounded ids, commit/run/job pins, payload hashes,
statuses and lifecycle times selected by persisted CI decisions; the query
does not load provider payloads, summaries or source URIs. The integration
fingerprint pins every current CI-pending key, latest current-episode
reconciliation, applicable stored acceptance assessment, validation registry
and protected-lane active-state fingerprint. Closed `coherent`, `stale` and `indeterminate`
statuses and reasons are server classifications. A stale or indeterminate
snapshot reports zero available working and integration admission capacity
while retaining observed occupancy and limits.

The same response returns at most 100 CI-pending candidates, with total and
truncation metadata. A missing status-entry boundary or a reconciliation from
an earlier CI-pending episode fails closed as `ci_reconciliation_unavailable`
without carrying its PR, head, evidence or acceptance identity forward.
Persisted CI classifications, decisions, reasons and
bounded evidence ids are passed through without presenter recomputation.
Validation registry identity is always explicit; because the local validation
plan is not a stored system authority, an absent exact plan fingerprint,
base/head tuple or profile set is `validation_plan_provenance_unavailable`, not
an invented profile. Exact-base state likewise uses only a matching stored
acceptance assessment: `exact_branch`, `rebase_required`, `stale` or
`indeterminate`. No read invokes the live PR integration classifier.

Protected-lane occupancy covers both working and CI-pending owners and returns
all six registry lanes with bounded owner keys. Candidate holds continue to
come from the latest immutable admission run. The API exposes registry version,
semantic fingerprint and active-state fingerprint, but it accepts no client
lane rule or capacity override.

The complete-policy POST rejects unknown fields recursively: the policy object,
each risk-lane entry and each component-lane entry are all closed request
shapes. Nested client-owned actor, action or current-state material cannot cross
the validation boundary or invoke the policy service.

Policy replacement requires every policy field and both complete lane arrays
in one strict JSON body. The API accepts no product, actor, action or current
state from the client. After the shared Host/Origin, JSON, session, CSRF and
idempotency dependencies resolve, the adapter invokes `revise_current` once;
that service selects the single local product and delegates to the existing
atomic policy command. Exact replay returns the original revision and receipt.
The canonical request fingerprint also pins the server-validated protected-lane
registry version and semantic fingerprint, so integration budget and active
protected-lane rules share the actor, compare-and-set, idempotency and atomic
receipt boundary without becoming client-authored policy fields.
Stale `expected_revision`, altered replay and an in-progress key return `409`
without changing the active policy. The route inventory contains no
ticket-status, dispatch, cancel, merge, rebase, arbitrary `PATCH`/`PUT`,
agent-session or automatic-ceiling operation.
Policy replacement changes only Atlas policy; it neither reads nor mutates
`WORKFLOW.md` or Symphony.

The Operator UI presents `approved_symphony_ceiling` as **Approved policy
ceiling** and describes it as operator-owned Atlas policy state that bounds
admission. It does not present that field as independently observed Symphony
configuration or occupied workers, read `WORKFLOW.md`, discover Symphony state,
or hide a temporary policy/configuration mismatch. Any future server-provided
configured-ceiling or occupied-session observation must be a distinct field;
the present UI consumes only the delivery-control projection.

Budgets are shown as maximums rather than targets. Server-provided working and
CI/integration used/available capacity, review pressure, protected Changes
Requested reserve, risk lanes, component lanes and indeterminate/over-capacity
state stay visually distinct. Review availability is not inferred. The
composite server snapshot's coherent, stale or indeterminate class, complete
reasons and pinned identities are explicit, while loading and refetch failure
preserve the last truthful response as transport-stale. Every persisted
admission decision, rank input and complete typed reason inventory is displayed
without browser reranking, availability inference or a manufactured safe
result.

CI-pending cards pass through exact head/PR identity, persisted outcome and
required-check classes, bounded evidence ids, validation profiles/provenance
and all typed wait/failure reasons without raw provider material. Exact branch,
rebase required, stale and indeterminate assessment classes are visually
distinct evidence claims and never merge approval. The failed ATLAS-259/260
boundary is explicit: the client shows no exact-integration-candidate success
class. Protected-lane cards show all occupants, immutable limits, registry and
active-state identity, held candidates and complete lane reasons; Symphony
capacity cannot override lane saturation.

Policy editing is a complete replacement, never a partial patch or hidden
adjustment. The confirmation names mode, approved policy ceiling, working,
integration and review budgets, Changes Requested reserve, all risk and
component lane limits, the server-observed protected-lane registry identity and
`expected_revision`, plus the newly minted idempotency identity that will be
submitted. Each new explicit command receives a fresh key. Stale revision and
altered replay preserve every entered proposal field; after inspecting the
complete current policy the operator may adopt only its revision, then must
review and reconfirm a freshly keyed command. Only an unchanged ambiguously
completed command may be explicitly retried with the same key. Success displays
the server revision and receipt and refetches before claiming current state. If
that refetch fails, the confirmed success remains non-retryable and another
command stays blocked until an authoritative refresh succeeds.

## Symphony ceiling ramp

There is one operator-owned Symphony ceiling:
`WORKFLOW.md`'s `agent.max_concurrent_agents`. The policy field
`approved_symphony_ceiling` is its recorded admission-side mirror, not another
control. Working and review budgets, Changes Requested reserve and lane limits
bound Atlas admission independently; actual occupied slots are observed
Symphony sessions. None is a utilisation target.

Ordinary committed `main` remains at `max_concurrent_agents: 1` and
`max_turns: 10` while Phase 15 is open. The controlled exercise is pinned to
the dedicated `phase-15-atlas-253-ceiling-ramp` branch. Only the operator may
change that branch's declaration in the exact sequence **1 → 3 → 5 → 7 → 10**.
Before creating or resuming that milestone branch, the operator must verify
that Phase 15.5's efficiency and integration milestone has passed, its closure
report is merged and ATLAS-253 is deliberately released from `Needs Human`.
Phase 15.5 changes no ceiling; it proves that focused local validation,
system-tier CI handoff, protected integration lanes and exact-base acceptance
do not turn additional slots into avoidable queue and rebase pressure.
`max_turns` is outside this ramp and remains ten. Each edit requires a PASS
receipt for the preceding gate; no intermediate value is merged or
cherry-picked independently. The exact preflight, fixed observation window,
evidence receipt, stop conditions and rollback are defined in the Symphony
ceiling controlled-ramp runbook in `docs/runbooks/operator-environment.md`.
The ramp introduces no delivery-policy mutation path: only the human/operator
may reconcile the policy mirror through the existing governed Phase 15
policy-revision boundary, and neither agents nor ramp automation receive that
authority.

The delivery-control UI may submit a complete policy selected by the operator
at any governed gate, but it does not advance a gate, validate a PASS receipt,
edit the milestone branch or `WORKFLOW.md`, start or stop agents, decide that an
increase is safe, or declare Phase 15 closed. Paused and draining policies stop
new admission while preserving active work; a lower Atlas policy ceiling does
not terminate an active Symphony session.

The milestone-only doc-linter and workflow-contract validation context accepts
levels 1, 3, 5, 7 and 10 only on the exact dedicated branch. Ordinary CI omits
that context, so an open-phase checkout above one remains non-mergeable. Every
gate receipt pins the fetched `origin/main` and branch merge-base commits. If
main moves, the gate fails; after the operator restores one and rebases, every
old receipt is historical and the cumulative sequence restarts at Gate 1.

The entry gates are cumulative:

| Level to begin | Evidence that must already exist |
| ---: | --- |
| 1 | Phase 15.5 closure is merged; ATLAS-253 is operator-released; the active policy is reconciled to the live one-agent declaration; Phase 15 controlled fixtures and admission observability are ready |
| 3 | Gate 1 PASS proves the serialized baseline, paused/draining no-admit behaviour and Changes Requested rework reserve |
| 5 | Gate 3 PASS proves the first controlled increase and bounded review pressure |
| 7 | Gate 5 PASS proves stable review pressure and stale/partial-write fail-closed behaviour |
| 10 | Gate 7 PASS, Phase 14 closure and adequate exact-head acceptance throughput at seven |

A failed or incomplete level restores or retains the last proven branch value,
records a FAIL receipt, leaves Phase 15 open and merges no ceiling change to
`main`. Pausing admission or lowering the branch declaration constrains future
admission/scheduling only; neither action claims to terminate active Symphony
sessions. Closure below ten is prohibited. Only a successful Gate 10 permits
the one milestone/closure PR to merge, and its resulting `main` tree must
declare exactly `max_concurrent_agents: 10`. A checkout at 3, 5 or 7 is valid
only as the pinned milestone branch state and remains unmergeable to `main`.

## Explicit non-goals

- Replacing Symphony scheduling or choosing which active worker runs next.
- Cancelling agents, deleting workspaces or demoting tickets.
- Automatic policy changes, model selection or score-driven routing.
- GitHub merge/rebase, Linear review transitions or plan approval.
- Multi-product/global capacity allocation, autoscaling or remote hosting.
- Predictive throughput optimisation; Phase 16 first establishes measurement.

## Milestone test

Seed more than ten independent tickets across risk and component lanes plus
Review Required, Needs Human and Changes Requested work. Against the live PM
sync and Symphony boundary, prove the serialized level and then move
deliberately through ceilings 1, 3, 5, 7 and 10.
Prove every occupancy and lane invariant, review-pressure stop, rework reserve,
pause/drain behaviour, deterministic selection and truthful sync timestamp.
Inject stale board/policy snapshots, concurrent ticks, partial/malformed reads,
ambiguous Linear writes and duplicate policy commands. No case may admit an
unselected ticket, exceed an approved budget, conceal indeterminate state or
grant Atlas scheduler, review, merge or deployment authority. The milestone is
complete only after the ten-agent gate succeeds and the same closure change
commits and merges `WORKFLOW.md` at `max_concurrent_agents: 10` to `main`; any
lower proven branch ceiling is recorded honestly while Phase 15 remains open
and no ceiling change merges.

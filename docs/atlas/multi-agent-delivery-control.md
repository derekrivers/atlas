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
independent tickets proves the ceiling can move from 3 to 5, 7 and 10 without
exceeding working, review or lane budgets, starving Changes Requested work or
granting Atlas scheduling, review or merge authority, and the milestone/closure
change then lands `WORKFLOW.md` with `max_concurrent_agents: 10`. Closure below
ten is prohibited.

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
- `review_budget`, bounded from 1 to 10;
- `changes_requested_reserve`, bounded from 0 to `working_budget`;
- zero or more exact risk-lane and canonical component-lane limits, each
  bounded from 0 to `working_budget`; and
- the operator-approved Symphony ceiling, initially 3.

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
sets. The migration changes neither `WORKFLOW.md` nor live Symphony
configuration, so it cannot raise the existing ceiling as a side effect.

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
from the project-scoped Linear pull, Atlas ticket/dependency store, current
policy revision and status-map revision. The existing
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

Every active ticket also consumes every matching risk and component lane.
Lane matching uses the ticket joined by `external_linear_id`, never its title or
Linear identifier. Component values use the policy's NFKC/trim/case-fold
canonical form. Changes Requested occupancy is retained separately, and the
snapshot derives both the remaining Changes Requested reserve and working
capacity available to a new admission after that reserve.

Every Atlas ticket in a working or review status must have an
`external_linear_id`. If it does not, the snapshot reports a typed
`missing_external_linear_id` reason and fails closed without guessing occupancy
from the Atlas status alone. Backlog, planned and blocked tickets are outside
delivery occupancy and may legitimately precede Linear issue creation, so a
missing id in those states is not an occupancy join gap. Once any non-terminal
ticket has an id, however, absence of that exact issue from the complete project
pull remains a typed `missing_joined_issue` failure.

The snapshot pins product id, Linear project id, immutable policy id/revision
and mode, a canonical policy fingerprint, the configured state-id map
fingerprint, the fetched-board fingerprint and count, Atlas store and graph
revision fingerprints, and an injected UTC observation time. The store
revision covers the ticket identity/join/status/lane inputs; the graph revision
covers ticket graph attributes and dependency edges. Its complete canonical
JSON representation is key-sorted and compact, all repeated fields are sorted,
and its SHA-256 fingerprint excludes no decision field. Identical inputs
therefore produce byte-identical counts, reasons, canonical bytes and
fingerprint regardless of source iteration order.

Unknown or unmapped state ids, state-id/type contradictions, incomplete pulls,
pagination gaps, missing or duplicate issue identities, duplicate Atlas joins,
working or review tickets without an external Linear id, missing joined issues,
unjoined non-terminal board issues, and disagreement between the joined Atlas
and Linear status are typed incompleteness reasons. Any such reason sets
`admission_allowed=false`; display names are provenance only and are never
status lookup keys. Existing occupancy above the working, review, risk-lane or
component-lane limit reports every breached dimension and also prohibits
admission. Paused or draining policy likewise makes the snapshot ineligible for
admission without misclassifying the coherent observation as incomplete.

## Deterministic admission decision

Candidates are the exact results of the existing Phase 3 readiness predicate;
Phase 15 does not reimplement dependency readiness. The engine returns one
append-only `AdmissionRun` with the snapshot/policy fingerprints and one
decision per candidate.

Candidates are ordered by this stable tuple:

1. number of currently blocked non-terminal tickets the candidate would
   unlock, descending;
2. membership and position on the current critical path, critical first;
3. Atlas priority, descending;
4. risk severity, lower first unless a policy lane explicitly permits more;
5. time continuously eligible, oldest first; and
6. ticket key, natural-key ascending.

The engine never uses an agent score, model opinion, title similarity or
Linear display order. Each decision is `admit` or `hold` with all applicable
typed reasons, including mode, stale snapshot, working budget, review budget,
rework reserve, risk lane, component lane and dependency status.

An admission pass selects at most one external promotion. This deliberately
trades a few five-second polling intervals for a safe external-write boundary:
Linear offers no multi-issue transaction, so Atlas never constructs a batch
that could partially succeed.

## PM-sync write protocol

The periodic tick acquires one database-backed admission lease. A concurrent
tick that cannot acquire it records a held/no-write outcome.

For a selected candidate the PM Engine:

1. builds the initial complete snapshot and deterministic decision;
2. re-reads current policy and the project-scoped Linear board immediately
   before the write;
3. requires the second fingerprint, candidate state and every occupancy count
   to match the decision inputs;
4. writes only that candidate to the uniquely mapped `Ready for Agent` state;
5. records the admission outcome and sync receipt; and
6. relies on the next normal pull to reconcile Atlas status, preserving the
   existing single-writer boundary.

A stale second read, policy revision, lease loss or malformed/partial Linear
response produces no write. A transport-ambiguous single write marks the run
`indeterminate`, admits no second candidate and blocks further admission until
a fresh pull reconciles the issue. Retrying the same state change is
idempotent. The protocol never attempts compensating status writes.

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

The Operator UI presents budgets as ceilings rather than targets, shows used
versus available capacity, keeps working and review pressure separate, and
explains why each eligible ticket was admitted or held. Policy changes require
an explicit confirmation and never silently retry an altered revision.

## Symphony ceiling ramp

Committed `main` remains at `max_concurrent_agents: 3` while Phase 15 is built.
For the live exercise, the operator uses a dedicated milestone branch and
changes its `WORKFLOW.md` ceiling from 3 to 5, 7 and 10 only after the preceding
evidence gate passes:

| Ceiling | Required evidence before proceeding |
| ---: | --- |
| 3 | Baseline admission, pause/drain and rework-reserve proof |
| 5 | Stable review occupancy and no stale/partial-write breach |
| 7 | Component/risk lane and Changes Requested recovery proof |
| 10 | Phase 14 closed; exact-head acceptance throughput remains within policy |

Each level is a maximum, never a desired occupancy. Failure or sustained
review pressure stops the exercise. On any failed gate, the operator restores
or retains the last proven `WORKFLOW.md` value on the milestone branch, records
the failure, leaves Phase 15 open and merges none of the branch's ceiling
changes to `main`; the milestone may not declare closure below ten. Only after
the ten-agent gate succeeds does the Phase 15 milestone/closure change commit
and merge `max_concurrent_agents: 10` to `main`. A rollback of the configured
branch ceiling is an operator change and never terminates active workers.

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
sync and Symphony boundary, move deliberately through ceilings 3, 5, 7 and 10.
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

# Parallel Delivery Efficiency and Integration Control (Phase 15.5)

Status: Planned interstitial design authority. Defines the bounded workflow
correction required before ATLAS-253 may begin Phase 15's live ceiling ramp.

## Problem and outcome

Ten available workers do not create ten useful delivery lanes when every agent
repeats the repository-wide test matrix, waits for CI and rewrites its branch
after every sibling merge. That operating model spends agent turns on work CI
already owns and turns independent implementation into avoidable integration
contention.

Phase 15.5 separates four kinds of capacity and authority:

- an agent owns implementation and focused local confidence;
- CI owns complete system-tier validation for the published identity;
- Atlas owns deterministic admission and bounded queue state;
- the operator owns review, rebase, conflict resolution and merge.

The outcome is higher accepted flow with less duplicated validation and fewer
unnecessary branch rewrites. The phase does not raise concurrency. Its closure
is the explicit operator release gate for ATLAS-253, which still owns Phase
15's controlled one-to-three-to-five-to-seven-to-ten exercise.

## Binding decisions

| Decision | Contract |
| --- | --- |
| Local validation | Agents run ticket-required and affected checks selected by a deterministic repository-owned registry. Unknown or protected surfaces fall back to a complete local sweep. |
| CI authority | Complete CI remains mandatory and is the only system-tier authority. Scoped local checks are confidence evidence, never completion evidence. |
| Agent lifetime | After focused checks and one successful publication, the ticket enters `CI Pending`; the Symphony slot is released and the agent does not poll CI. |
| CI reconciliation | Atlas observes pinned GitHub check evidence and performs one deterministic state transition. Infrastructure, pending and ambiguous outcomes remain held without retry churn. |
| Capacity accounting | Working, CI-pending, integration and review occupancy are separate budgets. Freeing a worker never makes downstream queues unbounded. |
| Conflict avoidance | Declared protected surfaces are exclusive integration lanes. Independent surfaces remain parallel. |
| Freshness | A clean candidate may avoid branch rewrite only after a feasibility spike proves exact repository, PR, base, head and synthetic-merge identities from authoritative GitHub evidence. |
| Rebase fallback | A conflict, stale identity, provider ambiguity or failed spike routes to the delivered operator-owned rebase lane. Atlas never resolves or publishes a rebase automatically. |
| Existing Phase 15 work | ATLAS-250 and ATLAS-251 continue normally and become prerequisites of the Phase 15.5 API/UI tickets. They are not recreated or rewritten. |
| Ramp release | ATLAS-253 remains `Needs Human` until the Phase 15.5 milestone and closure are reviewed and merged. |

## Authority and non-authority

Phase 15.5 may calculate validation plans, observe commit-pinned CI, classify
outcomes, hold or advance tickets through governed PM transitions, calculate
protected-lane occupancy and present integration pressure.

It may not skip required CI, mark agent claims as system-tier, merge a pull
request, rebase or force-push a branch, resolve a conflict, cancel a worker,
change the Symphony ceiling, approve a plan, weaken branch protection, mutate
GitHub checks or grant itself credentials. An optimisation that cannot prove
its inputs fails closed to the existing safer path.

## Local confidence and complete CI

### Validation registry

A digest-pinned declarative registry at
`atlas/verification/validation_registry_v1.json` maps narrow repository paths
and registered ticket requirements to ordered profiles. The versioned v1
profiles are Python tests, static checks, documentation, database/schema,
generated OpenAPI client drift, Operator UI unit/build, browser/end-to-end,
workflow contracts and the complete local sweep. The registry declares both
commands and selection reasons. Its content hash is checked before use; a
schema, version or digest mismatch is registry drift and selects the safe
complete-sweep baseline embedded in code.

`atlas validation-plan` accepts a full lowercase 40- or 64-character base
object id, a full head object id, and every repository-relative path in that
exact diff. Repeatable `--ticket-requirement` values name registry ids and
repeatable `--ticket-test` values add explicit test files. The optional
`--expect-registry-version` pins a caller to the policy version it reviewed.
The CLI reads the packaged registry but does not discover the diff, invoke Git,
execute a command or write repository/external state. It emits human-readable
output by default or canonical compact JSON with `--json`, containing:

- the repository identities used;
- every selected profile and command;
- the path or ticket requirement that selected it;
- every mandatory changed or ticket-declared test file;
- any protected-surface reason; and
- whether the complete-sweep fallback is mandatory.

Inputs and rendered fields have fixed count and length bounds. Input order,
duplicates, clocks, UUIDs and model interpretation do not affect the plan;
identical identities and changed-path sets serialize to identical bytes.
Changed tests, tests added for changed behaviour and ticket-declared tests are
additive mandatory inputs. There is no exclusion option, and an invalid or
unregistered free-form value cannot remove a profile. Unknown paths, omitted
diffs, ambiguous or inconsistent identities, registry drift, oversized input
and protected cross-cutting surfaces select the documented complete local
sweep rather than an incomplete result.

The complete local sweep runs the unfiltered Python test/static/documentation/
architecture gates and both Operator UI cold-checkout wrappers. It remains an
explicit profile and the conservative fallback. CI independently runs every
required job, including event-scoped gates such as PR-title provenance; local
profile selection never changes the CI workflow.

### Evidence boundary

Agent-run scoped checks are agent-tier evidence: useful for handoff and
debugging, but insufficient for completion. CI executes the complete required
matrix against the authoritative candidate identity and supplies system-tier
evidence. No local optimisation removes, weakens or conditionally skips a
required CI job.

An unchanged head is validated locally once per selected plan. Repetition of a
complete sweep for the same identity requires an explicit fallback reason; it
is not a default handoff ritual.

## CI-pending lifecycle

### State mapping

`CI Pending` is a tracker state observed by Atlas and configured in Symphony's
workflow mapping. It means implementation has published one candidate and is
waiting for authoritative checks. It is neither an active Symphony state nor a
review verdict.

```mermaid
stateDiagram-v2
    [*] --> InProgress
    InProgress --> CIPending: focused checks and publish
    CIPending --> ReviewRequired: system CI passes
    CIPending --> ChangesRequested: definite implementation failure
    CIPending --> CIPending: running, infrastructure or ambiguous
    ReviewRequired --> ChangesRequested: human remediation request
```

On successful publication the agent records the exact repository, PR and head
identity, transitions once to `CI Pending` and stops. It does not wait, poll,
interpret remote failures or consume a Symphony working slot. A changed head
invalidates earlier CI authority.

The CI reconciler consumes trusted check evidence pinned to the current head:

- all required checks passed: transition to `Review Required`;
- a definite implementation-owned failure: transition to `Changes Requested`;
- checks running or queued: remain `CI Pending`;
- provider outage, rate limit, missing check, malformed payload, unknown
  conclusion or identity mismatch: remain held with a typed reason.

Only the existing PM ownership boundary performs Linear transitions. Duplicate
observations are idempotent. Conflicting or partial observations produce no
advance. A new head restarts the lifecycle with new evidence; previous records
remain history.

## Four separate capacity budgets

Phase 15's working and review budgets remain binding. Phase 15.5 adds explicit
CI-pending and integration budgets:

| Budget | Counts | Releases when |
| --- | --- | --- |
| Working | Ready/active Symphony work under the Phase 15 policy | Work publishes or otherwise leaves the active delivery states |
| CI pending | Published heads awaiting a determinate CI outcome | Required checks become determinate for the same identity |
| Integration | Candidates admitted to freshness/acceptance processing | Candidate is reviewed, held for rebase/conflict or leaves the lane |
| Review | Existing Phase 15 acceptance pressure | Existing review policy releases it |

A ticket moves between budgets; releasing one budget does not erase downstream
pressure. Admission stops when any applicable budget or protected lane is
full. Paused or draining modes retain their existing Phase 15 semantics.
No budget is a utilisation target and none authorises Symphony cancellation.

## Protected integration lanes

### Classification

The repository owns a deterministic protected-surface registry. Initial
classes include database migrations, generated contracts and clients,
`WORKFLOW.md` and workflow validators, planning-store/renders, release and
policy files, and operator-declared temporary hotspots. Each class maps exact
paths or narrowly defined prefixes to a stable lane key and capacity, normally
one.

A ticket declares expected surfaces before admission. Atlas verifies the
actual changed paths at publication. An undeclared protected path is a hold,
not an implicit lane expansion. A candidate spanning multiple protected lanes
acquires all applicable capacity atomically or none. Lane acquisition order is
stable, so competing candidates cannot deadlock or depend on clock order.

### Behaviour

Protected lanes serialize only conflict-prone integration surfaces. Tickets
that share no protected lane remain independently admissible. Every hold names
the occupied lane, owning identity and policy revision without exposing
secrets. Stale observations, concurrent admission ticks or partial repository
classification promote nobody unexpectedly.

The registry is operator-owned configuration reviewed like other delivery
policy. Atlas does not infer a permanent lane from model judgement or learn
one automatically from a conflict.

## Exact-base acceptance without unnecessary rewrite

### Why this is a spike first

Being behind `main` is not itself a semantic defect. A clean candidate can be
evaluated as the exact synthetic merge of current base plus unchanged head,
but only if GitHub exposes identities and checks strongly enough to bind the
acceptance decision. The sixth ticket is therefore an evidence spike, not an
implementation assumption.

The spike must record, across controlled clean, conflicting and moving-base
PRs:

- repository and PR identity;
- current protected base branch and fetched base commit;
- candidate head commit;
- GitHub mergeability state and its convergence behaviour;
- synthetic merge commit/tree identity where available;
- required-check association and whether it is unambiguously tied to that
  synthetic identity; and
- behaviour under head movement, base movement, conflict, provider delay and
  malformed/partial responses.

PASS requires reproducible authoritative evidence that the accepted snapshot
is exactly the candidate head integrated with the current base, with no stale
reuse window and no reliance on an agent-provided merge claim. If GitHub cannot
provide that contract for this repository and branch protection, the spike
records FAIL and the no-rewrite path is not implemented.

### Conditional no-rewrite lane

Only after spike PASS may Atlas classify a candidate as `exact-base clean`.
Acceptance pins repository, PR, base branch, base commit, candidate head,
synthetic merge identity, required-check set and acceptance-criteria
fingerprint. Any movement or ambiguity invalidates the classification and all
derived authority.

An exact-base-clean classification permits review of the unchanged candidate
without force-pushing a rebased head. It does not merge the PR or bypass
required checks. A true conflict, failed mergeability, absent synthetic
identity, branch-protection mismatch or stale observation routes to
`rebase required` or `indeterminate`. The delivered Phase 12 operator lane
remains the only rebase/publish mechanism.

## API and Operator UI

After ATLAS-250 delivers the Phase 15 API, the Phase 15.5 API extension exposes
bounded projections for CI-pending and integration occupancy, protected-lane
holds, current candidate identities, validation-plan summaries and typed
freshness outcomes. It adds no generic mutation route and returns no raw
provider payload, credential, command output or workspace path.

After ATLAS-251 delivers the Phase 15 UI, the console shows working,
CI-pending, integration and review pressure separately. It distinguishes
waiting from failure, explains protected-lane holds, marks identity movement
stale and never presents a no-rewrite classification as a merge action. Policy
changes retain the existing authenticated confirmation and receipt boundary.

## Symphony workflow contract

`WORKFLOW.md` instructs an implementation agent to:

1. inspect the exact ticket requirements and changed surfaces;
2. run the deterministic scoped validation plan;
3. fix locally owned failures and rerun affected checks;
4. publish the candidate once;
5. enter `CI Pending`; and
6. stop the session.

The workflow forbids agent-side CI polling, repeated full sweeps for an
unchanged head without a fallback reason, automatic rebase/conflict resolution
and mutation of the ceiling. System reconciliation, operator review and the
existing integration lane continue after the agent releases its slot.

## Existing-ticket and release sequencing

ATLAS-250 and ATLAS-251 are already minted Phase 15 delivery contracts. They
should continue after PR #315 rather than wait for Phase 15.5 implementation.
The new API ticket depends on ATLAS-250; the new UI ticket depends on ATLAS-251
and that API extension. This avoids reopening their accepted definitions while
giving the new projections stable extension points.

An existing ticket cannot depend on a future unminted stub. Therefore
ATLAS-253 is governed by an operator release gate: keep it in `Needs Human`,
merge and apply the Phase 15.5 planning inputs, deliver the Phase 15.5 graph,
merge its closure, then deliberately release ATLAS-253. No automation performs
that release.

## Delivery graph

```mermaid
flowchart TD
    V["Scoped validation"] --> W["Publish and slot release"]
    C["CI pending"] --> R["CI reconciliation"]
    R --> W
    L["Protected lanes"] --> A["API and UI"]
    S["Merge-evidence spike"] --> N["Conditional no-rewrite lane"]
    N --> A
    W --> M["Efficiency milestone"]
    A --> M
```

The validation, CI-lifecycle and merge-evidence lanes can begin independently
once their listed existing prerequisites are satisfied. The milestone joins
them and remains the only Phase 15.5 closure authority.

## Milestone and closure

Before running the milestone, the operator records fixed observation windows,
an independent workload and numerical PASS/FAIL thresholds for:

- agent active time and local-validation time;
- duplicate complete sweeps per unchanged identity;
- CI queue/run time and indeterminate outcomes;
- working, CI-pending, integration and review queue bounds;
- review dwell, conflicts and rebases; and
- accepted completed flow, not merely PR creation or worker utilisation.

The controlled run includes protected-lane collisions, definite code failure,
infrastructure failure, provider ambiguity, head/base movement and a genuine
merge conflict. Repository and external-call spies prove the absence of
automatic merge, rebase, push, worker cancellation, CI mutation, plan
approval, permission expansion, deployment and secret-bearing retained
evidence.

Phase 15.5 closes only when every predeclared threshold and authority invariant
passes and the Phase 15.5 closure report lands at
docs/closure/phase-15.5-closure-report.md. A failed or
ambiguous result records the evidence, leaves Phase 15.5 open, keeps ATLAS-253
in `Needs Human` and leaves `WORKFLOW.md`'s committed ceiling unchanged.

## Explicit non-goals

- Raising or dynamically selecting `max_concurrent_agents`.
- Executing any ATLAS-253 ramp gate.
- Removing complete CI jobs or treating focused local tests as completion.
- Automatic GitHub merge, merge queue, rebase, force-push or conflict repair.
- Predictive or learned test-impact selection.
- Agent/model scoring or automatic routing; that belongs to Phase 16.
- Cancelling sessions, deleting workspaces or demoting active tickets.
- Remote hosting, multi-product allocation or deployment authority.

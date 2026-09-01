# PM Resilience and Retrospective Recovery

Status: Active specialist design authority for the PM write boundary,
retrospective recovery, eventual convergence, fairness, and operational health.

## Authority and scope

This document specialises the Phase-4 PM design. It governs behavior when a PM
action fails, a process stops between durable and external effects, authoritative
provider state advances while Atlas is offline, or one safely held candidate
would otherwise monopolise the loop. The ordinary field owners, trust tiers,
exact-head rules, append-only histories, operator merge authority, writer
ownership, and lease/fence protocols remain unchanged.

The earlier blanket statement that "a missed tick costs latency only" is true
only for a seam whose prerequisites are recoverable and whose crash-before and
crash-after outcomes are durably reconstructable. It is not a subsystem-wide
property. Every seam must prove the stronger contract here; otherwise a missed
or interrupted tick may create a durable blocker and health must report it.

This document defines the contracts and pure health calculus. The ordinary PM
CI-handoff lane consumes the durable episode, product-global fairness and
blocker substrate to schedule one exact current `ci_pending` candidate per
tick. Retrospective merged-publication recovery remains inactive. This document
does not add a recovery CLI, operate the managed runtime, alter a production
database or recover a live ticket.

## Core invariants

1. PM fails closed on an unsafe individual action.
2. PM does not globally fail-stop because one action cannot progress.
3. Recoverable authoritative state eventually converges under repeated ordinary
   ticks, including after complete process reconstruction.
4. Ambiguous provider state never causes an unauthorised or duplicate mutation.
5. Retrospective recovery authority is separate from ordinary forward lifecycle
   authority.
6. One poison candidate cannot starve independent work indefinitely.
7. Process liveness, completed ticks, coherent observation, convergence and
   delivery progress are distinct facts.
8. Repeated non-routine holds become durably typed and diagnosable.
9. Recovery never requires direct production database edits.
10. Every external-write boundary has an explicit crash-before and crash-after
    contract.
11. Every durable lease, fence or intent has a next-process owner and a bounded
    terminal or retry state.
12. Tests prove reconstruction with fresh process objects, not merely a second
    call against retained in-memory state.

Fail-closed is an action property; eventual convergence is a system property.
Neither may be traded for the other.

## Ordinary and retrospective authorities

### Ordinary forward reconciliation

The ordinary publication identity for a current CI-handoff episode remains
exactly one issue-bound GitHub publication whose canonical URL and provider
metadata agree, targets `main`, closes the Linear issue, and is `open` or
`draft`. Merged, closed, truncated, missing, replaced, contradictory, or
multiple attachments remain invalid inputs to this predicate. The generic
Linear pull remains observation-only for Atlas-owned workflow edges. A merged
publication must never be admitted by widening the ordinary predicate.

### Historical merged-publication proof

Retrospective reconciliation is a separate, named target authority. If it is
activated by the later governed implementation defined below, the ordinary
cadence invokes it only after ordinary identity resolution reports a
historically advanced publication. It must freshly establish all of the
following:

- one joined Atlas ticket and Linear issue, using `external_linear_id` only;
- one issue-bound canonical repository and PR number with a complete attachment
  connection and agreeing canonical URL/provider metadata;
- the exact contributor head for the affected `ci_pending` episode and the
  expected base repository and branch;
- a fresh provider observation that the PR actually merged and the immutable
  merge-commit identity;
- accepted ancestry of that merge commit on a freshly resolved canonical
  `main`, without treating a branch name alone as proof;
- a persisted `PASSED` Verification Engine verdict at the exact contributor
  head, with its deciding system-tier required-check evidence retained
  append-only;
- the exact current `acceptance_criteria_fingerprint` at that contributor head,
  all required human-tier criterion confirmations, every required scope
  decision, and blanket approval, all bound to that same criteria fingerprint
  and exact head;
- system-tier `PR_MERGED` proof at the same exact contributor-head commit as the
  persisted verdict, naming the canonical PR and immutable merge commit;
- strict fresh merge/main ancestry proof: a fresh canonical-provider read of
  the merge commit and a freshly fetched canonical `main` must agree, and the
  merge commit must be an ancestor of that exact fetched main identity;
- no contradictory, multiple, replaced or indeterminate publication identity;
- no unresolved write fence, competing edge owner, or policy/snapshot movement;
  and
- the human merge/acceptance authority already exercised for that exact head,
  never inferred from merge state, local state or an agent report.

Evidence recovery appends newly observed source records and a bounded
retrospective decision. It never updates or deletes historical evidence and
never changes recency semantics. A merge alone is insufficient. If historical
human-tier confirmation, scope-decision or blanket-approval proof is absent,
the candidate receives a durable hold. The fallback is ordered and cannot use
the missing verdict as its own prerequisite:

1. A distinct authenticated operator recovery decision begins as a durable
   recovery predecision. It pins a new stable recovery-action identity, the
   exact ticket, contributor head, current criteria fingerprint, PR, merge
   commit and fresh-main identity, and names each absent human-tier artifact.
   It does not claim a verdict and does not authorise the workflow edge; the
   predecision must not require the unavailable verdict as an input.
2. Under that recovery-action identity, the authenticated operator supplies
   and writes the equivalent canonical exact-head human-tier evidence: every
   required criterion confirmation, scope decision and blanket approval bound
   to the current criteria fingerprint and contributor head. A separately
   verified alternative is permitted only when the owning acceptance authority
   first establishes that it has the same exact-head human-tier semantics and
   writes canonical evidence identities consumable by Verification Engine; it
   is not a waiver or an assertion embedded only in the recovery receipt.
3. Only after that evidence exists does Verification Engine then recompute and
   persist a new `PASSED` verdict for the exact contributor head from the
   canonical system-tier and human-tier evidence. Recovery code cannot write,
   copy or assume that verdict.
4. The append-only recovery receipt is written last. It binds the recovery
   predecision/recovery-action identity, supplied human-tier evidence ids, the
   resulting persisted verdict id and commit, current criteria fingerprint,
   system-tier `PR_MERGED` proof, PR, merge commit and fresh-main ancestry
   identity. Only this completed sequence can satisfy the final proof list
   above.

That action is not reconstructed from the merge and cannot be implied by an
agent or system actor. Any other missing or ambiguous fact likewise holds with
no workflow mutation.

The sole future owner of a direct retrospective transition is the **PM
Retrospective Completion Reconciler**. It must acquire one product-scoped
retrospective-completion lease and persist a prepared
`retrospective_completion_write_fence` before the Linear write. A confirming
provider response clears the fence; an exception or non-confirming response
makes it indeterminate. A fresh process holding the same product lease must
resolve the fence from a complete board pull before any retry, and its
fence-reconciliation tick performs no second workflow mutation.

When all facts prove that delivery has already advanced past the obsolete
intermediate states, that sole owner may perform one direct `ci_pending -> done`
transition. The edge records the actual historical advance and its exact proof;
it must not fabricate `review_required`, replay an obsolete CI classification
mutation, reopen a merged PR, or create synthetic timestamps. No other
condition grants a direct transition to `done`.

This edge is **INACTIVE**. It gains no runtime authority until a separately
reviewed implementation updates the owning canonical transition and acceptance
documents (`docs/atlas/symphony-integration.md`,
`docs/atlas/verification-engine.md`, `docs/atlas/review-acceptance-console.md`,
`docs/runbooks/pr-acceptance.md` and `WORKFLOW.md`), installs executable
single-owner, lease, fence, exact-proof and writer guards, and lands the required
storage and temporal tests. Until then current runtime ownership remains
unchanged: the existing PM CI-handoff and verified-completion owners keep their
current edges, and no component may execute the direct retrospective edge.

## Fair bounded evaluation

Each product owns one durable global monotonic sequence. A new candidate episode
atomically receives an `episode_created_sequence`. After every held or
actionable evaluation, the evaluator atomically allocates a new global monotonic
sequence and stores it as that episode's `last_evaluated_sequence`. Its ordering
cursor is `last_evaluated_sequence` when present and otherwise
`episode_created_sequence`; the natural ticket key is only the final tie-break
for corrupt/equivalent legacy input, which health must also diagnose.

Selection takes the least cursor from one finite eligible snapshot. Cursors
are product-local monotonic work ranks. Across products, the durable outer
scheduler compares the oldest episode observation time: `last_evaluated_at`
after an evaluation and otherwise `created_at`, with an unevaluated episode
first at an equal instant and product UUID only as the deterministic final tie.
A fixed candidate or fence rank can therefore have only finitely many older
ranks ahead of it; an unresolved fence is moved behind every currently older
independent product after each attempt. A held
evaluation moves that episode to the sequence tail rather than retaining first
position. New episodes receive their creation cursor at the same global tail,
while their outer rank is their later creation time, so new products and new
episodes cannot cut ahead of an older observed retry. Under a functioning
cadence, a coherent monotonic tick clock, coherent sequence allocation, a
finite eligible snapshot, finite arrivals between ticks and no global
prerequisite failure, every older rank has only finitely many ranks ahead of it
even when newer work continues to arrive; it is therefore eventually selected.
Episode identity changes only on an authoritative lifecycle entry or
publication replacement, never on process restart.

The active ordinary implementation derives a candidate episode from the latest
append-only transition into `ci_pending` and the exact issue-bound publication
attachment/repository/PR generation when one is available. A legacy row with no
transition history uses its durable ticket UUID only for the one-time bootstrap;
the first later real re-entry has a transition UUID and therefore a new episode.
Missing, incomplete or ambiguous publication observations are blockers, never
proof of replacement. The finite snapshot is established once per tick and
bootstrap allocation is deterministic; after establishment the durable cursor,
not ticket key, owns selection.

The tick still permits at most one external workflow mutation. A confirmed
mutation or any reconciliation attempt for an existing ambiguous write fence
ends the tick. A fenced product is excluded from its ordinary candidate lane
until recovery. Existing fences retain absolute precedence over ordinary
fairness and are reconciled before generic pull handling, publication
resolution or evidence evaluation, even when the earlier local target commit
means no `ci_pending` candidate remains. Multiple fenced products rotate by
their durable cross-product observation-time rank, but ordinary work never
skips unresolved write ambiguity for throughput.
Evaluation fairness never creates a second writer, bypasses a lease, weakens a
candidate predicate or converts an unbounded arrival model into a liveness
claim.

A selected publication generation is re-resolved from each final complete
provider board immediately before fence creation. Attachment replacement,
cardinality change or repository/PR mismatch is an authority change and permits
no workflow write.

Before publication resolution, evidence refresh, fence reconciliation or any
provider workflow call, the cadence durably reserves the selected product's
next signed 64-bit evaluation sequence. Exhaustion therefore fails closed
before an external effect rather than discovering an unrecordable evaluation
afterward. A crash may leave a non-authoritative unused sequence gap, but never
advances the episode cursor without its atomic evaluation and blocker commit.
A fence owner also refreshes the complete project board
after acquiring its lease; the pre-lease pull is discovery input, not recovery
authority. Source, target or moved fence retirement atomically verifies the
exact still-live lease owner and fence identity, so an expired or replaced
recovery process cannot clear ambiguity after a slow provider refresh.
Target confirmation applies the exact ticket's local status and retires that
fence in the same transaction; losing the lease or fence CAS leaves both local
eligibility and the durable ambiguity fence intact. A fence discovered after
ordinary candidate selection is accounted to its own ticket episode, never to
the displaced candidate.

Fence absence is also enforced at every later workflow-writer boundary, not
only by a cadence-level snapshot. Definition creation with its create-time
state assertion, admission, verified completion and review-cycle routing each
hold the shared product lease and atomically verify that no CI-handoff fence
exists across the bounded provider call. CI-handoff fence creation locks that
same lease row. A lease conflict or late fence therefore makes zero downstream
workflow calls and closes the rest of the tick's workflow-write window; a
fresh tick reconciles the retained fence first.

A lease-contention observation consumes its already reserved sequence only as
the blocker occurrence identity; it does not advance the episode cursor that a
live owner may still commit. The typed `lease_unavailable` blocker durably
defers that whole product until `next_safe_retry_at`, allowing another ordinary
product—or, under absolute fence precedence, another fenced product—to receive
the next cadence opportunity without racing the live owner's cursor.

## Durable blocker observations

An unsafe or incomplete action outcome that survives the current call must be
representable as a bounded typed observation. The ordinary CI-handoff scheduler
now persists this shape for publication, provider, evidence, authority, lease
and fence holds without acquiring any new workflow authority. The durable shape
must answer without raw provider payloads or exception text:

- schema and policy revision/fingerprint;
- operation and bounded reason code;
- candidate, bounded authority and current episode identity;
- one stable blocker/episode fingerprint over schema, operation, reason,
  recoverability, candidate, authority and episode identity only;
- recoverability: routine wait, retryable, unresolved fence, or unknown;
- first and last observation times plus consecutive observation count;
- next safe retry time;
- whether delivery capacity is affected;
- a deterministic same-product prefix of at most 128 independently starved
  candidates and starvation start, plus an explicit truncation marker when
  that prefix omits further members;
- the exact lease/fence/intent identity when applicable; and
- the later progress observation that supersedes the blocker.

The stable fingerprint excludes mutable observation state: first/last times,
consecutive count, retry time, capacity impact, starvation members/start and
supersession. Recurrence of the same cause in the same authority/episode keeps
one fingerprint; a changed reason, authority or episode changes it. Repeated
observations update bounded current diagnostic state or append a new observation
according to the owning storage contract; they must not grow unbounded duplicate
payloads. The ordinary CI-handoff evaluator atomically supersedes the prior
active cause when its committed current cause changes. Historical anomaly,
evidence, decision and transition records remain append-only. Progress
supersedes an obsolete blocker explicitly; silence or a
process restart does not clear it.

The storage substrate does not persist or infer
`progress_expected_since` or `convergence_expected_since`. Their writer and
reset semantics are intentionally deferred to the health/diagnose integration
unit. Until that contract is activated, an episode creation, evaluation,
heartbeat or blocker timestamp must not be projected as `last_progress_at`,
`last_convergence_at` or an expected-since baseline.

`partial` is a receipt classification, not a diagnosis. A completed tick may be
operationally blocked, and an isolated partial tick may be safely retryable.
Health consumes the typed observations rather than inferring cause from a
coarse receipt label.

## Operational health calculus

`atlas/pm/health.py` is the pure domain-only reference calculation. It accepts
an injected observation time, a `PmHealthPolicy`, four independent freshness
signals, an explicit `progress_expected` applicability input, and typed blocker
observations. It performs no clock read, database read, provider request,
logging, mutation or recovery action.

The initial policy makes every threshold explicit:

| Input | Default |
| --- | ---: |
| expected cadence | 60 seconds |
| heartbeat stale/block | 3 minutes |
| coherent board stale/block | 3 minutes |
| convergence degrade / block | 5 / 15 minutes |
| progress degrade / block | 5 / 15 minutes |
| routine retry window | 1 minute |
| retryable recurrence degrade / block | 2 / 5 observations |
| starvation block | 5 minutes |

The signals are not aliases:

- **Heartbeat** says a scheduler invocation completed far enough to emit its
  liveness observation. Missing or stale heartbeat is `BLOCKED`.
- **Coherent board** says a complete, internally consistent authoritative board
  was observed. Missing or stale coherence is `BLOCKED` even with heartbeats.
- **Convergence** says all currently recoverable observed differences settled.
  It may degrade or block while the board remains coherent.
- **Progress** says delivery state advanced; it is distinct from a legitimate
  stable convergence and from mere tick completion. Its missing/stale clock is
  evaluated only when `progress_expected=true` because eligible work or an
  unresolved recovery demand exists. An idle coherent board with no work and
  no blocker sets `progress_expected=false` and does not become unhealthy merely
  because nothing needed to move.

A first routine wait whose next retry lies within the configured window and has
no capacity/starvation effect remains `HEALTHY`. A transient retry, overdue
routine wait, capacity impact, or pre-threshold starvation is `DEGRADED`.
Recurrence at the blocked threshold, threshold-aged starvation, an unresolved
write fence, stale critical freshness, or unknown/legacy input is `BLOCKED`.
Missing convergence is degraded. Missing or stale progress degrades/blocks only
when progress is expected; fresh heartbeat, board and convergence with no
blockers is `HEALTHY` while legitimately idle.
Explicit progress supersession removes the obsolete blocker from the active
calculation; process memory and absence from one tick do not.

Reasons and blocker fingerprints are canonically sorted and hashed. Input order
and equivalent timezone representations cannot change the assessment. The
policy fingerprint accompanies the result so a stored assessment is replayable.
The highest-severity reason determines the overall state.

## Crash and next-process ownership

Every future external-write implementation must refine this table for its exact
seam before gaining authority:

| Boundary | Crash before external write | Crash after external write / before acknowledgement | Fresh-process owner |
| --- | --- | --- | --- |
| definition create | no provider effect; retry from authoritative ticket definition | re-read issue-bound identity, persist a unique join if exactly one equivalent issue exists; ambiguity blocks and create is not repeated | definition reconciliation |
| definition update | cursor remains behind; retry the same owned fields | re-read provider content/identity; stamp only confirmed equivalence, otherwise safely retry the idempotent update | definition reconciliation |
| workflow mutation | durable fence says no call occurred or remains prepared; never assume target | complete board pull proves source, exact target, or other state; target clears without a second write, source permits one governed retry, other state blocks | owning edge reconciler |
| evidence pull/ingest | no source observation; retry read | provider reads have no external mutation; append/deduplicate immutable evidence by source identity and payload hash | evidence reconciler |
| local append-only decision | transaction absence means no durable decision | committed row is replay input; canonical identity prevents duplicate logical decision | operation-specific repository |

Leases bound current ownership and expire only under their canonical protocol.
Fences preserve ambiguous external outcomes and never disappear merely because
a process died. Intents, fences and cursors must be stored before the effect
whose ambiguity they resolve, revalidated against fresh provider state, and
retired only by their named next-process owner.

## Temporal reconstruction tests

Every changed mutation seam must test the sequence:

```text
durable state at tick N
  -> injected fault before or after one boundary
  -> destroy process, repositories, clients and in-memory fakes
  -> optionally advance authoritative provider state
  -> construct a fresh process over file-backed disposable state
  -> run ticks N+1 ... N+k
  -> converge exactly once or retain a durable typed blocker
```

The harness must provide deterministic time, mutable fake Linear and GitHub
state, an external-write ledger, before/after/ambiguous failure injection, and
exact duplicate-write assertions. Required scenarios include a poison candidate
alongside an independent candidate, merged historical publication, ambiguous
provider write, persistent completed-but-blocked ticks, and recovery that
supersedes the old blocker. Same-object second-call idempotency is useful unit
coverage but is not restart proof.

## Production boundary

Repository contracts and disposable tests do not authorise managed recovery.
No implementation unit under this design may start or restart
`atlas-pm-sync.service`, run a production one-shot tick, restore/downgrade/edit
the production store, insert evidence, move a live Linear issue, change policy,
or revive a retired release. A separate operator-reviewed forward-recovery plan
may be prepared only after the required code is accepted on `main`; execution
requires a distinct explicit operator decision under the PM deployment runbook.

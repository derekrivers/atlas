# Post-Review Release Orchestration

Status: Ratified target architecture (ATLAS-084M). Design and governance
authority only; no production lifecycle, schema, provider mutation or release
authority is activated by this document.

Date: 29 August 2026.

Baseline: `derekrivers/atlas` `main` at
`3039bf5722303daa78d5ad691b0e915e81005244`.

## 1. Authority and activation boundary

This document is the canonical design authority for separating substantive
human review from the mechanical integration and release of an approved
change. It amends the target architecture established by the delivered Phase
14, Phase 15.5 and Phase 16 documents without rewriting their historical
decisions.

The amendment is intentionally inactive. Until separately reviewed
implementation and activation work lands, the current production contract is
unchanged:

- `Review Required` remains the production acceptance state;
- acceptance sessions, human confirmations and verification remain pinned to
  one exact contributor head;
- a moved head makes those current-runtime records historical for the new
  head;
- the Phase 12 rebase lane remains operator-owned, including conflict
  resolution and lease-protected publication;
- the operator manually merges the exact verified candidate;
- `Awaiting Release` is not a `TicketStatus` or Linear state;
- no release-queue budget or release occupancy exists in production policy;
- no `ReviewReleaseEpisode` or `ReviewApproval` persistence exists;
- Symphony's active-state contract is unchanged; and
- no Release Controller, automatic rebase, force-push or merge authority
  exists.

Direct unminted maintenance/meta PRs, including `ATLAS-NNM` records such as
this amendment, remain on that current manual path. They are not eligible for
the v1 target Release Controller.

Implementation documents must cite an activation decision before describing
any part of the target lifecycle as live. This amendment does not modify an
Atlas store, planning render, ticket definition, Linear state, GitHub setting,
Symphony workflow or provider credential.

## 2. Problem and motivation

The current production acceptance spine uses one state for two different
conditions. `Review Required` can mean either that substantive engineering
review has not happened, or that review succeeded and only exact-head
confirmation, rebase and manual-merge ceremony remains.

The delivered acceptance-session action already proves that one explicit
operator action can carry a criteria fingerprint, the complete criterion
index set and blanket approval atomically. Repeating a prompt for every
criterion, scope exception and blanket approval is therefore not a necessary
governance boundary. The meaningful human decision is approval of the change
that the operator actually reviewed.

The target architecture separates that decision from machine work:

```text
substantive review of reviewed change
                |
                v
       durable ReviewApproval
                |
                v
          Awaiting Release
                |
                v
JIT integration + equivalence proof + fresh exact-candidate CI
                |
                v
 exact-head guarded merge + merged proof
```

This reduces repeated operator ceremony while strengthening the truthfulness
of human provenance: Atlas records the reviewed change and never pretends that
the operator inspected a later commit merely because a machine rebased it.

## 3. Existing state

The active system has these relevant properties:

1. The system-tier CI reconciler alone admits an exact published head from
   `CI Pending` to `Review Required`.
2. Phase 14 acceptance sessions pin repository, PR, close-set, base/head SHAs,
   branch identities and a criteria fingerprint. A mutation after identity
   movement stales the session.
3. The governed confirmation action accepts all criterion indexes and blanket
   approval in one request, but writes criterion and blanket `HUMAN`
   `MANUAL_APPROVAL` evidence at that exact head.
4. Scope verification derives its declared envelope from
   `relevant_docs + source_anchor` and asks the operator to decide every other
   changed path at the exact head.
5. Phase 12 owns an operator-driven, rerere-disabled rebase workspace and an
   exact old-head force-with-lease publication boundary. Conflict resolution is
   explicitly human.
6. Required machine evidence and the acceptance verdict are exact-head bound.
   Old-head evidence cannot satisfy a new candidate.
7. Phase 14 exposes merge readiness as advice only, and the operator manually
   merges during a one-PR freeze.
8. Phase 16 `PRInteractionObservation` and `QueuePlan` are stale-aware and
   advisory. They have no merge, rebase or ticket-transition authority.

These are coherent current-runtime safeguards. The target design replaces
only the duplicated post-review ceremony and the permanent assignment of all
mechanical release work to the operator.

## 4. Target lifecycle and ownership

The future lifecycle is:

```text
PR Open
   |
   v
CI Pending
   |
   v
Review Required
   |\
   | \ semantic findings
   |  `----------------------> Changes Requested
   |
   | operator review-accept action
   v
Awaiting Release
   |
   | governed serial release
   v
Done
```

`Review Required` has one target meaning: a concrete, unambiguous PR head has
been published, the complete required system-tier CI set is present and PASSED
for that exact head, and substantive human engineering review is required.
All six predicates are mandatory:

```text
concrete PR head exists
+ candidate is published
+ candidate identity is unambiguous
+ complete required CI is present
+ complete required CI is PASSED for that exact head
+ substantive human review is required
```

A controller-local or unpublished candidate, a head without complete exact-head
CI PASS, or an identity-ambiguous head can never be labelled
`Review Required`.

`Awaiting Release` means that the operator semantically approved the reviewed
change and conditionally authorised Atlas to release that change when every
machine-verifiable release condition is satisfied. It is a non-Symphony
handoff/release state. It is not proof of merge, completion, current-head
freshness, reusable CI or permission to merge whatever commit later occupies
the PR branch.

`Needs Human` means that the release episode requires an operator judgement or
identity/provenance repair before Atlas can safely publish or present a
candidate. It is not a review-ready claim and carries no automatic-release
authority.

`Changes Requested` means a concrete candidate needs implementation or other
semantic remediation: either human review produced findings or fresh
exact-candidate CI determinately proved an implementation-owned failure. It is
the existing Symphony re-entry route, not an infrastructure-ambiguity bucket.

`Done` means the exact authorised release candidate was merged, merged proof
was recorded and the managed completion owner observed every completion gate.
A merge request, provider response or Release Controller assertion alone is
not `Done`.

State-edge ownership is narrow:

| Edge | Sole target owner |
| --- | --- |
| `CI Pending -> Review Required` | system-tier CI reconciler |
| `Review Required -> Changes Requested` | operator semantic finding relay |
| `Review Required -> Awaiting Release` | explicit governed operator review-accept action |
| `Awaiting Release -> Review Required` | release-routing owner, only after a concrete candidate is safely published and complete required CI PASSES at that exact unambiguous head |
| `Awaiting Release -> Changes Requested` | Release Controller applying a fresh implementation-owned CI failure route |
| `Awaiting Release -> Needs Human` | Release Controller applying a conflict, unsafe publication, unreconciled head or operator-judgement route |
| merged proof -> `Done` | managed completion owner, never the Release Controller by assertion |

The implementation must fence Atlas-store and Linear projections through the
existing one-writer/reconciliation discipline. A partial external transition
must remain explicit and retryable; it must not manufacture an approval or
release success.

Approval or ticket drift does not itself establish review readiness. If the
already-published reviewed head remains the candidate, it may return to
`Review Required` only while the six predicates above still hold. If a clean
controller integration produces a candidate that cannot carry automatic
release authority, the bounded release path may publish it, obtain complete
exact-head CI PASS, and only then present it as `Review Required`. Unsafe or
incomplete publication routes to `Needs Human`. A conflict produces no
controller publication and routes directly to `Needs Human`.

## 5. Four distinct identities

The release model must never collapse these identities:

| Identity | Meaning | Movement rule |
| --- | --- | --- |
| ticket identity | Stable product/ticket records and the exact close-set governed by the PR | Main movement does not alter or remint it; close-set movement invalidates release authority. |
| reviewed-change identity | Deterministic identity of the base-relative change the operator reviewed | May survive a rebase only when the proof in section 8 reproduces it exactly. |
| candidate commit identity | Exact PR head submitted to a particular CI or integration step | Any rebase or head rewrite mints a new identity and makes old exact-head machine evidence historical. |
| release commit identity | Exact authorised PR head consumed by the merge gate, plus the provider's resulting merge commit identity | Must be proved at the merge boundary and by post-merge observation; it is never inferred from a tree alone. |

The reviewed-change identity is not a commit SHA. The approval separately
retains the reviewed base and head SHAs so audit can state exactly what the
human inspected.

## 6. Review release episode and approval authority

### 6.1 ReviewReleaseEpisode

A `ReviewReleaseEpisode` is the single authority container for one PR release
history. Its immutable core identity binds:

- repository and PR number;
- same-repository head-branch identity;
- product identity;
- complete sorted canonical Atlas ticket close-set.

Its append-oriented authority projection additionally binds the current human
approval generation and sole active approval ID. Generation advances without
retargeting the immutable episode core.

One PR has at most one open episode at a time. Replacement of the close-set,
repository/PR/branch relationship or product identity terminates the old
episode's authority; it does not retarget that episode. All tickets in a
multi-ticket close-set project the same episode and may share the same future
`Awaiting Release` state.

V1 eligibility requires a non-empty, complete canonical Atlas ticket close-set
and every identity required by the episode and approval contracts. Direct
operator-scoped maintenance/meta PRs with no minted ticket or issue-bound
close-set, including `ATLAS-NNM`, are ineligible. Atlas invents no substitute
ticket, Linear or close-set identity for them; they remain on the manual
acceptance/rebase/merge path. Automating meta work requires a separately
ratified identity and governance design.

ATLAS-084M and PR #375 are themselves direct meta work and therefore remain
manually accepted, rebased and merged.

### 6.2 ReviewApproval generations

A `ReviewApproval` is a first-class, append-only human-provenance record. One
explicit authenticated operator action creates the next approval generation
and atomically performs the Atlas-owned
`Review Required -> Awaiting Release` transition for the complete close-set;
the fenced Linear projection follows the existing external-write discipline.
Absence of findings, conversation, a GitHub comment/review, green CI, elapsed
time or an agent recommendation cannot create it.

The action means:

> I reviewed this implementation against the current ticket contract and
> approve this reviewed change for governed release subject to the release
> policy.

At minimum the durable record binds:

- approval ID, schema version and record fingerprint;
- episode ID, monotonic `approval_generation`, `previous_approval_id` and
  `supersedes_approval_id` (nullable only where generation/chain semantics
  permit);
- product identity, ticket identities and sorted close-set;
- repository identity, PR number and head-branch relationship;
- reviewed base SHA and reviewed head SHA;
- one `TicketReviewContractFingerprint/v1` per ticket and the aggregate
  close-set contract fingerprint;
- complete reviewed changed-path inventory and the complete `(ticket, path)`
  scope-disposition matrix;
- deterministic reviewed-change identity and its algorithm/version;
- operator actor (`human/operator` while ADR-0009 applies);
- positive review outcome and the governed action/receipt identity; and
- creation time.

The action atomically represents the positive human decisions actually covered
by the review: every criterion accepted, complete changed-path inventory
reviewed, the complete per-ticket scope matrix accepted and blanket semantic
approval.
There is one action, not a sequence of per-criterion `Y` prompts followed by
per-path waivers and another blanket approval. Explicit operator authorization
is still mandatory.

Same-key retries replay the same receipt. An altered request, stale ticket
definition, moved reviewed head, changed close-set, incomplete path inventory
or uncertain provider read writes no successful approval. The record is never
updated or retargeted.

### 6.3 Sole active approval

Approval generation starts at one and is monotonic within an episode. A new
successful human review appends generation `N+1`, names the generation-`N`
approval as both its previous and superseded approval, and becomes the sole
active approval atomically. Two approvals never coexist as current authority.

An approval is active only when all of these hold:

- it belongs to the open episode and is that episode's highest committed
  generation;
- no later approval explicitly supersedes it;
- no append-only revocation or human episode-termination record ends it;
- repository/PR/branch/product/close-set episode identity still matches;
- every current ticket contract fingerprint equals the approved fingerprint;
  and
- the release proof chain, if any, is rooted in that exact approval.

Supersession, revocation, episode identity break or material ticket-contract
drift terminates authority monotonically. Historical records remain immutable
but can never become active again merely because a later condition disappears.
A new human review creates a new generation; it does not revive an old one.
Every proof rooted in a superseded approval becomes historical immediately,
and a new release chain must root in the new active approval. Revocation, when
supported, is an append-only terminal authority record rather than mutation of
the approval.

Human truth remains literal. If the operator reviewed head `H1` with change
`delta`, and Atlas later produces `H2`, the audit statement is:

```text
human reviewed H1 / delta
system proved H2 preserves approved delta
system validated H2
```

Atlas must not append synthetic `HUMAN` evidence claiming that the operator
reviewed `H2`.

## 7. Reviewed path and change semantics

### 7.1 TicketReviewContractFingerprint/v1

For each ticket, semantic review identity is:

```text
sha256(canonical_json(TicketReviewContract/v1))
```

The canonical representation includes exactly these ticket fields:

```text
ticket key
product identity
title
objective
context
ticket type
risk level
component
tags
relevant_docs
acceptance_criteria
non_goals
implementation_notes
test_requirements
documentation_requirements
definition_of_done
source_anchor
```

Canonical JSON uses fixed field names, UTF-8 string bytes exactly as stored,
explicit JSON null for nullable `component`, enum values rather than labels and
no implicit trim/case-fold. `tags` and `relevant_docs` are set-like and encode
as sorted unique strings. `acceptance_criteria`, `non_goals`,
`implementation_notes`, `test_requirements`, `documentation_requirements` and
`definition_of_done` retain stored list order and duplicates because position
can affect review/reference identity. The remaining scalar fields encode
exactly once. An aggregate close-set contract fingerprint hashes the ordered
`(ticket key, per-ticket fingerprint)` pairs in the complete sorted close-set.

Operational/non-contract fields are excluded: current status, timestamps, sync
cursors, external provider IDs, review-cycle counters and completion clocks.
Priority and estimated effort are excluded because they influence scheduling,
not the semantic work the operator reviewed. Dependency, interface,
co-delivery, protected-lane and release-policy revisions are pinned separately
in the interaction/release proof; they are not hidden in this fingerprint.
Any change to an included field invalidates the active approval and requires a
new human review generation.

### 7.2 Complete reviewed path inventory and scope matrix

The approval captures the complete trusted base-to-head changed-path identity
set, including both names for a rename or copy, operation, presence, mode and
object kind. The trusted Git diff and object reads, not UI ordering or
caller-supplied omission, supply the set.

Scope disposition is not global. Atlas constructs the complete matrix:

```text
ReviewPathDisposition
  ticket_key
  path_identity
  disposition = DECLARED_SCOPE | REVIEWED_EXCEPTION
```

Every ticket in the close-set has exactly one deterministic disposition for
every path identity in the review envelope. The same path may be declared
scope for ticket A and a reviewed exception for ticket B. Missing, duplicate or
contradictory cells refuse approval. The machine presents the complete matrix,
and the operator accepts it in the same single governed action; the matrix does
not reintroduce per-cell terminal prompts.

For v1, `DECLARED_SCOPE` means exact normalised membership in that ticket's
current canonical `relevant_docs + source_anchor path` set, matching the active
scope evaluator. Every other matrix cell must be an explicit
`REVIEWED_EXCEPTION`; the complete inventory means the operator no longer has
to answer one prompt per exception.

This inventory and matrix replace repeated compensation for the current
`relevant_docs + source_anchor` heuristic without weakening that heuristic in
the active verifier. A later path addition, deletion, rename/copy identity,
object-kind/mode change or disposition change invalidates autonomous release
authority. It can reach `Review Required` only through the published,
exact-head CI-PASS rule in section 4.

### 7.3 ReviewedChangeManifest/v1 exact path states

The reviewed-change identity is:

```text
sha256(canonical_json(ReviewedChangeManifest/v1))
```

The manifest is derived from exact Git object reads at the reviewed base and
head. For every participating path identity, including both sides of a rename
or copy, the ordered canonical record contains data equivalent to:

```text
path
operation
before_present
before_object_kind
before_mode
before_content_sha256
after_present
after_object_kind
after_mode
after_content_sha256
```

Absence is explicit. For present objects, Atlas hashes the actual relevant
content bytes with SHA-256 rather than relying only on the repository's Git
object hash. Canonical path and operation identities distinguish add, delete,
modify, type/mode change, rename and copy. Ordinary text, binary content,
symlinks, additions, deletions, mode-only changes, renames and copies are
eligible only when both endpoint path states can be represented exactly.
Unsupported or ambiguous objects, including any submodule or special-form case
whose relevant content/state cannot be represented by the versioned contract,
fail closed.

The fingerprint uses SHA-256 over length-delimited canonical bytes and retains
the manifest version. V1 does not strip hunk offsets/context or attempt textual
delta similarity. Git `patch-id`, diff similarity, tree equality,
mergeability, a clean rebase result and LLM judgement remain diagnostic only;
none is authoritative equivalence.

## 8. ReleaseEquivalenceProof

The sole active approval can authorise a later mechanically rebased candidate
only through an append-only `ReleaseEquivalenceProof`. Proofs form one
unbroken authority chain rooted in that exact human approval. The proof is
system-authored, deterministic and bound to one release attempt. Every
required input must be present and every predicate must pass; unknown is
failure for autonomous release.

### 8.1 Required identities

The proof binds:

- root active approval, episode, generation and receipt IDs;
- `parent_authority_type` and `parent_authority_id`, where the parent is either
  the root `ReviewApproval` or the immediately previous successful
  `ReleaseEquivalenceProof`;
- repository, PR number and unchanged same-repository head-branch identity;
- unchanged product/ticket identities and sorted close-set;
- unchanged per-ticket and aggregate review-contract fingerprints;
- root reviewed base `B`, root human-reviewed head `H1` and root
  reviewed-change identity;
- immediately authorised old PR head, its integration-base identity, selected
  live-main `M`, expected old PR head and produced candidate;
- complete old/new path inventories and change-manifest fingerprints;
- integration workspace, toolchain, policy and proof-algorithm identities;
- dependency, co-delivery, protected-lane, interface and interaction policy
  revision/assessment identities;
- previous proof-chain fingerprint and resulting proof-chain fingerprint; and
- proof creation time and terminal result.

At any instant the episode has one `current_authorised_head`: the human-reviewed
head when the chain is empty, otherwise the output candidate of the last
successful proof rooted in the active approval. A failed or abandoned proof
does not advance it.

### 8.2 Governed mechanical integration

The Release Controller creates a clean, disposable, controller-owned workspace
from independently fetched immutable refs. It disables rerere and automatic
conflict reuse. Let `S` be the episode's current authorised head and `S_base`
the integration base recorded by its parent authority. The controller requires
the live PR head to equal `S`, pins the expected-old-head lease to `S`, and
attempts a non-interactive rebase of the exact authorised series from
`S_base..S` onto newly resolved live main `M`. The first proof has `S = H1` and
`S_base = B`; each later proof uses the immediately preceding successful
proof's output head and live-main base.

Autonomous integration is eligible only when the process completes without a
conflict stop, edit, manual continuation, dropped commit, squash, fixup or
unrecorded working-tree mutation. Any conflict or required judgement ends that
attempt; the controller never resolves it. The workspace transcript is bounded
and secret-free, and the resulting object identities are recalculated from Git
rather than trusted from process narration.

Each successful proof advances `current_authorised_head` to its output. Main
may move again before release, in which case a later proof chains from that
head rather than attempting to return to `H1`. The expected-old-head
force-with-lease always names the exact current authorised head. An
unrecognised live-head movement, missing parent, superseded root approval or
proof-chain fingerprint discontinuity breaks authority and fails closed. No
system proof creates or refreshes human evidence.

### 8.3 Equivalence predicates

All of the following are mandatory:

1. repository, PR and head-branch relationship are unchanged;
2. the root approval remains the episode's sole active approval and the parent
   proof chain is complete and unbroken;
3. product/ticket identities and close-set are unchanged;
4. every current per-ticket and aggregate review-contract fingerprint equals
   the active approval;
5. `M` is the independently resolved current protected `main` at proof time,
   and root reviewed base `B` is its proven ancestor without protected-branch
   rewrite;
6. the complete upstream changed-path set for `B..M` has no overlap with any
   identity in the complete reviewed-path envelope;
7. for every reviewed path, its exact pre-change presence, object kind, mode
   and content SHA-256 at `B` equal the corresponding state at `M`;
8. for every reviewed path, its exact post-change presence, object kind, mode
   and content SHA-256 at root reviewed head `H1` equal the corresponding state
   at the newly produced candidate;
9. every path operation and the complete reviewed-path inventory, including
   both rename/copy identities, remain identical to the approved manifest;
10. the complete `(ticket, path)` scope matrix is unchanged;
11. the resulting candidate is based on `M` with exact current-main ancestry;
12. the controller observed no conflict, manual resolution, dropped/reordered
    semantic content or other unreviewed content;
13. the versioned interaction policy independently proves the upstream range
    `B..M` is semantically independent of the reviewed change; and
14. the proof names the exact output candidate, immediate parent authority,
    current authorised old head and expected-old-head lease.

The interaction policy is conservative. It evaluates the complete upstream
and reviewed path sets against versioned protected-lane, interface-usage,
dependency/co-delivery and globally interaction-sensitive registries. Release
requires complete classification coverage, no path overlap, no protected-lane
collision, no incompatible `change/consume` or `change/change` interface
relationship, no dependency/co-delivery ordering violation and no unknown or
stale registry input. File disjointness alone does not prove independence.
`PRInteractionObservation` may supply corroborated input, but a `POSSIBLE`,
`UNKNOWN`, `STALE` or `DISPUTED` observation cannot authorise release.

A clean Git result, GitHub mergeability, tree similarity, equal final tree or
LLM judgement cannot replace any predicate. If deterministic equivalence and
independence cannot both be established, the candidate is not equivalent for
autonomous release.

V1 deliberately refuses approval carry-forward when upstream changes any
reviewed path, even if a text merge would be clean or appear equivalent. A
future wider equivalence family requires separate research, ratification and
activation.

### 8.4 Worked authority chain

```text
Approval A1 generation 1 @ human-reviewed H1 / delta
main moves to M2
Proof P1: parent A1, expected old head H1, H1 -> H2
main moves to M3
Proof P2: parent P1, expected old head H2, H2 -> H3
fresh CI validates H3
release gate may consume H3

new human review
Approval A2 generation 2 supersedes A1
```

When A2 commits, A1 and every proof rooted in A1, including P1 and P2, become
historical atomically. The episode's active root and current authorised head
are reset to A2's reviewed identity. Monotonic generation and explicit
supersession mean A1/P1/P2 cannot resurrect if drift is later reversed or A2
is revoked; another human review must append a new generation rooted in its
own reviewed head.

## 9. Fresh CI and exact-head boundary

Review authority and machine evidence have separate lifetimes.

- Every release candidate must have the complete required system-tier CI set
  pinned to that exact candidate identity.
- When integration mints `H2 != H1`, every CI result for `H1` is historical;
  the controller publishes `H2` through the expected-old-head lease and waits
  for a fresh complete required CI execution at `H2`.
- Agent-tier local results, old CI, prior verification and the equivalence
  proof cannot satisfy those checks.
- An unchanged `H1` may consume its still-current complete exact-head CI only
  when no candidate or main identity moved; it still requires a fresh release
  verification immediately before merge.
- The canonical verifier for the release candidate consumes system evidence at
  `H2` plus the human `ReviewApproval` and system equivalence proof as distinct
  provenance. It never fabricates replacement `HUMAN` evidence at `H2`.

Any candidate movement after CI makes that CI historical for the new head.

Review routing consumes the same evidence boundary. When automatic-release
equivalence/interaction proof fails but the controller can safely prepare and
publish a concrete candidate, that head remains within the release episode
until complete required CI at that exact head is present and PASSED. Only then
may the release-routing owner move it to `Review Required`. A local candidate,
an unsafe/uncompleted publication, missing or non-PASSED CI, or ambiguous
candidate identity cannot use that state. Unsafe publication routes to
`Needs Human`; provider/infrastructure ambiguity holds the release attempt.

An unexpected or out-of-band head first loses `current_authorised_head`
standing. Atlas must reconcile its provenance, release episode and exact-head
CI before any route. If it cannot prove the movement is a recognised episode
publication, it routes to `Needs Human`; it never infers review readiness from
the new head or provider rollup.

## 10. Serial Release Controller

The production Release Controller is a dedicated authority, not an extension
of generic PM synchronization. The managed Atlas cadence may invoke it, but a
repository/protected-branch lease enforces release concurrency `1`.

Development, CI and review remain parallel. Release is JIT and serial:

```text
select one Awaiting Release approval
  -> integrate against live main
  -> prove equivalence and independence
  -> publish with expected-old-head lease when needed
  -> obtain exact-candidate CI
  -> verify the exact release gate
  -> merge the exact authorised head
  -> verify merged proof
  -> release the lease
```

After a merge, the next candidate starts from a newly fetched current `main`.
Earlier diagnostic rebases of queued work create no durable release freshness.

Initial selection is deterministic and conservative. The candidate set contains
only open eligible episodes with exactly one sole active `ReviewApproval`;
historical, superseded, revoked or contract-invalid approvals are absent. Order
by active approval creation time, canonical first ticket key, then PR number.
Candidates with a live hold are skipped only under a documented stable rule;
the receipt names the episode, active approval generation, selection set and
reason. A Phase 16 `QueuePlan` remains advisory and cannot select, rebase or
merge a candidate. Future queue intelligence requires separate activation
before it may influence ordering.

### 10.1 Release occupancy, budget and backpressure

Release capacity is distinct from Phase 15.5 working, CI/integration and review
capacity. One open `ReviewReleaseEpisode` in `Awaiting Release` counts as one
release-occupancy unit, regardless of how many tickets its PR closes. All
tickets in the close-set project that shared episode identity; they do not
consume one slot each.

An operator-owned `release_queue_budget` bounds admitted release occupancy. It
is separate from working budget, CI/integration budget, review budget and
Release Controller execution concurrency. The queue budget may be greater than
one; controller execution concurrency remains exactly one. Initial activation
must name an explicit conservative budget in a governed policy revision. A
missing, invalid or stale budget fails closed and admits no new episode to
`Awaiting Release`; no implementation may infer unlimited capacity.

Delivery admission includes release occupancy in its revalidated snapshot. At
or above the configured budget it admits no additional new delivery merely
because working or review slots are free. Existing in-flight work is neither
destroyed nor demoted. Temporary over-capacity is observable, blocks further
admission and drains through normal release/explicit human routing. Review and
release occupancy remain separate metrics. Moving a PR out of
`Review Required` therefore cannot create unbounded apparent capacity.

### 10.2 Narrow capabilities

The controller may only:

- read exact repository, PR, ticket, episode, sole active approval, proof-chain,
  capacity-policy and live-main identities;
- acquire and release the one-candidate branch lease;
- create/use the governed disposable integration workspace;
- perform one non-interactive mechanical rebase;
- compute and record the equivalence/interaction proof;
- publish the exact result with an expected old-head lease;
- observe fresh required CI;
- invoke canonical exact-candidate release verification;
- merge the exact expected authorised head; and
- append release attempts, proofs, receipts and merged proof without altering
  historical approval generations.

It may not perform arbitrary repository writes, resolve conflicts, mutate
ticket definitions or criteria, change CI or branch protection, weaken policy,
change Symphony capacity, use unrestricted Linear mutation or infer human
approval.

## 11. Exact-candidate merge and race boundary

Automatic merge consumes only the candidate that passed the release gate. At
the final mutation boundary the controller must atomically or immediately
adjacently prove:

- its repository/protected-branch lease is current;
- protected `main` still equals the proof's `M` and is an ancestor of the
  exact release candidate;
- the live PR head exactly equals the episode's current authorised release
  candidate;
- the PR is still open, non-draft, same-repository and targets protected main;
- episode identity, close-set, sole active approval generation, every ticket
  review-contract fingerprint, reviewed path/scope matrix and complete
  equivalence-proof chain are unchanged;
- complete required system-tier CI and release verification pass at that exact
  candidate;
- no head/base/provider identity read is indeterminate; and
- the merge request carries the provider's expected-head/SHA precondition.

Any intervening movement refuses the mutation. A successful provider response
is not sufficient by itself: Atlas re-reads the PR and protected branch,
records the provider merge commit and proves that the exact authorised head was
consumed. Managed completion observes that merged proof before `Done`.

Branch protection, provider rulesets and GitHub mergeability are defence in
depth. They do not replace the Atlas lease, exact-head precondition,
equivalence proof, CI evidence, verifier or merged-proof readback.

## 12. Failure routing

| Observation | Route | Release meaning |
| --- | --- | --- |
| Clean non-interactive integration; complete equivalence/independence proof | continue at `Awaiting Release` | Mechanical authority remains valid. |
| Ticket-contract/criteria/close-set drift while the already-published reviewed head remains concrete, unambiguous and exact-head CI-PASSED | `Review Required` | Approval is inactive; the CI-qualified published candidate requires a new human review generation. |
| Ticket/approval drift without a concrete published exact-head CI-PASSED candidate | hold the release attempt or `Needs Human` when publication/provenance is unsafe | Drift never manufactures review readiness. |
| Clean integration produces a concrete candidate but equivalence/interaction proof cannot authorise automatic release | safely publish under the bounded release path, obtain complete exact-head CI PASS, then `Review Required` | Human review sees a real CI-qualified candidate. |
| That clean candidate cannot be safely published or its identity cannot be completed | `Needs Human` | No `Review Required` transition and no invented candidate authority. |
| New/renamed/copied/unreviewed path or changed `(ticket, path)` disposition | same publish + complete exact-head CI PASS requirement before `Review Required`; otherwise `Needs Human` | Complete review-envelope guard failed. |
| Semantic interaction, incomplete interface coverage, uncertain equivalence or stale/disputed relation evidence | same publish + complete exact-head CI PASS requirement before `Review Required`; otherwise `Needs Human` | Uncertainty is not success. |
| Rebase conflict or any need for manual conflict resolution | `Awaiting Release -> Needs Human`; publish nothing | Controller stops without resolving or exposing a local candidate as review-ready. |
| Fresh exact-candidate CI proves implementation-owned failure | `Changes Requested` | Returns to semantic remediation through the existing route. |
| CI pending, missing, cancelled, infrastructure-failed, malformed or provider-ambiguous | hold `Awaiting Release` | No failure or success is inferred; retry/reconcile under the release-attempt fence. |
| Recognised main movement while the live PR head still equals `current_authorised_head` | hold and start the next chained JIT proof/rebase attempt | Prior release freshness is historical; no stale proof or CI is consumed. |
| Unexpected/out-of-band PR-head movement | reconcile exact provenance, episode and CI; `Needs Human` if not recognised | Never infer review readiness from provider state or a green rollup. |
| Expected-head merge refusal or ambiguous merge response | hold `Awaiting Release` and reconcile exact provider state before any retry | Never blind-retry a possible merge. |
| Exact candidate passes all gates and merged proof verifies | managed completion may reach `Done` | Release is complete only after proof, not request submission. |

Routes are deterministic outputs of typed facts. The controller does not use a
model to turn uncertainty into a success or choose semantic conflict
resolution.

## 13. Supersession ledger

The old rulings remain preserved below. Each is still active production
authority until the implementation and activation stage named in section 14.

| Old ruling | Why it is obsolete for the target | Replacement | Current-runtime disposition |
| --- | --- | --- | --- |
| `pr-acceptance.md` spine and Phase 14: review acceptance is an exact-head sequence of confirmation evidence and blanket approval; any head movement restarts from evidence. | It conflates semantic approval with machine identity refresh and repeats decisions already covered by substantive review. | One append-only `ReviewApproval` binds the reviewed head and deterministic reviewed change; later authority crosses a rebase only through `ReleaseEquivalenceProof`. | Exact-head acceptance session and restart semantics remain enforced. |
| `verification-engine.md`: criterion, scope and blanket human approval records are usable only at the exact head; scope derives from `relevant_docs + source_anchor`. | Exact-head machine evidence remains correct, but the heuristic scope model and repeated human records are not the right identity for approved semantic change. | Approval captures the complete reviewed path inventory and `(ticket, path)` scope matrix once; the release verifier consumes approval and system proof without synthesising new human evidence. | Existing evaluators and evidence shapes remain unchanged. |
| Phase 15.5 binding decisions and disposition: the operator owns every post-review rebase; Atlas never resolves or publishes one automatically. | A clean mechanical integration can be delegated when exact path-state equivalence and deterministic interaction independence are proved. | Dedicated serial Release Controller may perform only non-interactive JIT rebase and lease publication; conflict or unsafe publication returns to humans. | Phase 12 operator lane remains the only production lane. |
| Phase 14/15.5/16 and `pr-acceptance.md`: merge is a manual operator action under a one-PR freeze. | Exact expected-head mutation and merged-proof readback can close the residual human click race while retaining operator review authority. | Dedicated controller may merge only the exact candidate passing the complete release gate and provider expected-head precondition. | Manual merge remains authoritative. |
| Phase 16 section 2.2: the operator retains review acceptance and manual merge; section 4.2 excludes automatic rebase/merge. | Review acceptance remains human, but narrow mechanical release authority is now separately governed. | Operator retains semantic approval; the activated Release Controller owns only the bounded capabilities in section 10. | Phase 16 implementation gains no release authority from this amendment alone. |
| Phase 16 P16-D27 and sections 20-21: `QueuePlan` is advisory with no merge/rebase/ticket-transition authority. | Not obsolete. Model/advisory planning must remain separated from production release authority. | Ruling retained; initial controller ordering is deterministic and does not consume `QueuePlan` as authority. | Unchanged. |
| Cumulative programme v4 sections 1, 3.2, 7.3, 8.12 and 27: human authority includes manual merge and the operator owns post-review rebase/conflict/manual merge. | Semantic review and conflict judgement remain human, but permanent human execution of mechanically provable integration and exact-head merge is unnecessary in the target architecture. | `post-review-release-orchestration.md` sections 6-12: the operator creates the sole active approval; a serial least-privilege Release Controller may chain proven clean rebases and merge only the exact authorised CI-passed candidate. | The cumulative historical statements and all Phase 15/16 evidence remain preserved; manual production rebase and merge remain binding until explicit activation. |
| `symphony-integration.md`: no `Awaiting Release` mapping exists and `Review Required` is the post-CI handoff through acceptance. | A reviewed-but-unreleased candidate needs a distinct non-agent state. | Future `Awaiting Release` Atlas/Linear handoff state, absent from Symphony active states. | Existing state mapping remains unchanged. |

ATLAS-259 and ATLAS-260 remain historical FAIL results for synthetic-candidate
CI attribution. This amendment does not revive their tree/mergeability or
simulated-attestation route. It instead requires a real rebased contributor
head, fresh CI at that head and a separate deterministic change-equivalence
proof.

## 14. Staged rollout and implementation decomposition

One future governed planning batch should contain approximately these five
independently reviewable slices. Real ticket keys are minted only through the
normal planning workflow.

### Slice A - Review acceptance simplification

Add one governed review-accept action by reusing acceptance-session and action
receipt foundations. Capture every
`TicketReviewContractFingerprint/v1`, the aggregate close-set fingerprint,
complete trusted path inventory and complete `(ticket, path)` scope matrix
while retaining the current exact-head/manual-merge lifecycle. Do not add
`Awaiting Release`, rebase or merge authority in this slice.

### Slice B - Awaiting Release and durable approval lifecycle

Add `Awaiting Release`, `ReviewReleaseEpisode`, append-only approval
generation/supersession/revocation, the sole-active-approval rule, Atlas/Linear
mapping and reconciliation fences. Add operator-owned release occupancy,
`release_queue_budget` and delivery-admission backpressure while keeping the
state absent from Symphony active states. No production Git mutation
authority.

### Slice C - Equivalence proof and governed JIT integration

Implement exact SHA-256 before/after path-state
`ReviewedChangeManifest/v1`, the repeated-rebase parent/root proof chain,
interaction coverage policy and clean non-interactive controller workspace.
Prove the decision in shadow first. Same-reviewed-path upstream overlap,
conflict, unsupported content and incomplete classification fail closed. No
automatic merge.

### Slice D - Serial Release Controller and exact-head merge

Add the one-candidate branch lease, deterministic selection from sole active
approvals, current-authorised-head lease publication, fresh exact-candidate CI
observation, the section-4 review-readiness routing invariant, release
verification, provider expected-head merge, ambiguity fence, merged proof and
routing matrix. Execution concurrency remains one and the controller remains
disabled by default.

### Slice E - Live safety proof and activation

Exercise normal release; multiple sequential rebases; superseding human review
and non-resurrection of its old proof chain; multi-ticket close-sets; path
addition/rename/copy and scope-matrix drift; criteria/complete ticket-contract
drift; dependency/interface uncertainty; conflict; CI implementation failure;
CI infrastructure/provider ambiguity; out-of-band head movement; main movement
during CI and immediately before merge; ambiguous merge response;
release-budget backpressure; and correct exclusion of maintenance/meta PRs
against real provider/state boundaries. Only an explicit operator activation
after this evidence may enable automatic release authority. Activation must
name the deployed code, schema, policy, budget, credential/channel inventory,
kill criteria and rollback.

The batch must not expand merely to mirror storage, API and UI layers. It may
combine inseparable foundations only while each ticket retains one primary
authority boundary and independently falsifiable tests.

## 15. Design acceptance conditions

Implementation and activation must preserve all of these conditions:

1. one explicit human review action, never inferred approval;
2. exact distinction among ticket, reviewed-change, candidate and release
   identities;
3. one release episode with at most one monotonic sole active approval;
4. complete ticket-contract fingerprints, reviewed-path protection and
   `(ticket, path)` scope matrix;
5. truthful human provenance at the actually reviewed head and an unbroken
   repeated-rebase proof chain;
6. exact before/after path-state equivalence, no upstream reviewed-path overlap
   and independent interaction proof before authority crosses a rebase;
7. fresh system-tier CI for every rebased candidate;
8. no `Review Required` without a concrete published unambiguous exact-head
   complete CI PASS;
9. bounded release occupancy and serial JIT release against live main;
10. a dedicated least-privilege controller, not generic PM mutation power;
11. exact expected-head merge and merged-proof verification;
12. typed fail-closed routing for drift, conflicts and uncertainty;
13. v1 exclusion of direct unminted maintenance/meta PRs; and
14. explicit activation after implementation evidence.

Local validation of this design is agent-tier confidence only. Complete
GitHub CI at the published exact head remains system-tier authority.

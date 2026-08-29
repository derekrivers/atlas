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
- Symphony's active-state contract is unchanged; and
- no Release Controller, automatic rebase, force-push or merge authority
  exists.

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

`Review Required` has one target meaning: authoritative CI passed for the
published candidate and substantive human engineering review is required.

`Awaiting Release` means that the operator semantically approved the reviewed
change and conditionally authorised Atlas to release that change when every
machine-verifiable release condition is satisfied. It is a non-Symphony
handoff/release state. It is not proof of merge, completion, current-head
freshness, reusable CI or permission to merge whatever commit later occupies
the PR branch.

State-edge ownership is narrow:

| Edge | Sole target owner |
| --- | --- |
| `CI Pending -> Review Required` | system-tier CI reconciler |
| `Review Required -> Changes Requested` | operator semantic finding relay |
| `Review Required -> Awaiting Release` | explicit governed operator review-accept action |
| `Awaiting Release -> Review Required` | Release Controller applying a deterministic drift/uncertainty route |
| `Awaiting Release -> Changes Requested` | Release Controller applying a fresh implementation-owned CI failure route |
| `Awaiting Release -> Needs Human` | Release Controller applying a conflict or operator-judgement route |
| merged proof -> `Done` | managed completion owner, never the Release Controller by assertion |

The implementation must fence Atlas-store and Linear projections through the
existing one-writer/reconciliation discipline. A partial external transition
must remain explicit and retryable; it must not manufacture an approval or
release success.

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

## 6. ReviewApproval

A `ReviewApproval` is a first-class, append-only human-provenance record. One
explicit authenticated operator action creates it and atomically performs the
Atlas-owned `Review Required -> Awaiting Release` transition for the complete
close-set; the fenced Linear projection follows the existing external-write
discipline. Absence of findings, conversation, a GitHub comment/review, green
CI, elapsed time or an agent recommendation cannot create it.

The action means:

> I reviewed this implementation against the current ticket contract and
> approve this reviewed change for governed release subject to the release
> policy.

At minimum the durable record binds:

- approval ID, schema version and record fingerprint;
- product identity, ticket identities and sorted close-set;
- repository identity, PR number and head-branch relationship;
- reviewed base SHA and reviewed head SHA;
- ticket-definition and acceptance-criteria fingerprint;
- complete reviewed changed-path inventory and every scope disposition;
- deterministic reviewed-change identity and its algorithm/version;
- operator actor (`human/operator` while ADR-0009 applies);
- positive review outcome and the governed action/receipt identity; and
- creation time.

The action atomically represents the positive human decisions actually covered
by the review: every criterion accepted, complete changed-path inventory
reviewed, explicit scope exceptions accepted and blanket semantic approval.
There is one action, not a sequence of per-criterion `Y` prompts followed by
per-path waivers and another blanket approval. Explicit operator authorization
is still mandatory.

Same-key retries replay the same receipt. An altered request, stale ticket
definition, moved reviewed head, changed close-set, incomplete path inventory
or uncertain provider read writes no successful approval. The record is never
updated or retargeted. Revocation or supersession, if later required, is a new
append-only operator record.

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

### 7.1 Complete reviewed path inventory

The approval captures the complete trusted base-to-head changed-path identity
set, including both names for a rename or copy, status and file kind. Every
path receives one disposition: ticket-declared scope or explicit reviewed
scope exception. The trusted Git diff, not UI ordering or caller-supplied
omission, supplies the set.

This inventory is the future review envelope. It replaces repeated
compensation for the current `relevant_docs + source_anchor` heuristic without
weakening that heuristic in the active verifier. A later candidate with one
new, renamed, copied or otherwise unreviewed path is outside the approval and
routes to `Review Required`.

### 7.2 Canonical change manifest

The reviewed-change identity is:

```text
sha256(canonical_json(ReviewedChangeManifest/v1))
```

The manifest is derived from the exact reviewed base/tree and head/tree. It
contains the repository object-format identity and an ordered record for every
change: operation, old/new path, old/new mode, object kind and a canonical
content-delta digest. Text deltas retain the exact ordered added/removed bytes
and structural operation while excluding only hunk offsets and unchanged
context that Git legitimately relocates during a rebase. Binary, symlink,
submodule, rename/copy, mode-only and empty-commit cases require an explicitly
versioned deterministic normaliser; an unsupported or ambiguous case is not
equivalent.

The implementation must use collision-resistant SHA-256 digests over
length-delimited canonical bytes and retain the normaliser version. Git
`patch-id`, tree equality, mergeability or a clean rebase result alone is not
the reviewed-change identity. They may be diagnostic inputs, never the proof.

## 8. ReleaseEquivalenceProof

A prior approval can authorise a later mechanically rebased candidate only
through an append-only `ReleaseEquivalenceProof`. The proof is system-authored,
deterministic and bound to one release attempt. Every required input must be
present and every predicate must pass; unknown is failure for autonomous
release.

### 8.1 Required identities

The proof binds:

- approval and receipt IDs;
- repository, PR number and unchanged same-repository head-branch identity;
- unchanged product/ticket identities and sorted close-set;
- unchanged ticket/criteria fingerprint;
- reviewed base `B`, reviewed head `H1` and reviewed-change identity;
- selected live-main `M`, expected old PR head and produced candidate `H2`;
- complete old/new path inventories and change-manifest fingerprints;
- integration workspace, toolchain, policy and proof-algorithm identities;
- interaction/protected-interface assessment identity; and
- proof creation time and terminal result.

### 8.2 Governed mechanical integration

The Release Controller creates a clean, disposable, controller-owned workspace
from independently fetched immutable refs. It disables rerere and automatic
conflict reuse, requires the live PR head to equal the approval's authorised
expected head, and attempts a non-interactive rebase of the exact reviewed
series from `B` onto `M`.

Autonomous integration is eligible only when the process completes without a
conflict stop, edit, manual continuation, dropped commit, squash, fixup or
unrecorded working-tree mutation. Any conflict or required judgement ends that
attempt; the controller never resolves it. The workspace transcript is bounded
and secret-free, and the resulting object identities are recalculated from Git
rather than trusted from process narration.

### 8.3 Equivalence predicates

All of the following are mandatory:

1. repository, PR and head-branch relationship are unchanged;
2. product/ticket identities and close-set are unchanged;
3. the current ticket/criteria fingerprint equals the approval fingerprint;
4. `M` is the independently resolved current protected `main` at proof time,
   and `B` is its proven ancestor without protected-branch rewrite;
5. the resulting candidate is based on `M` with exact current-main ancestry;
6. the new complete changed-path inventory equals the reviewed inventory,
   including rename/copy sides, modes and object kinds;
7. the recomputed `ReviewedChangeManifest/v1` identity for `M..H2` equals the
   approved identity for `B..H1`;
8. the controller observed no manual conflict resolution or unreviewed content;
9. the versioned interaction policy proves the upstream range `B..M` is
   independent of the reviewed change; and
10. the proof names the exact release candidate `H2` and the expected old-head
    lease used for publication.

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

Initial selection is deterministic and conservative: oldest valid
`ReviewApproval.created_at`, then canonical ticket key, then PR number.
Candidates with a live hold are skipped only under a documented stable rule;
the receipt names the selection set and reason. A Phase 16 `QueuePlan` remains
advisory and cannot select, rebase or merge a candidate. Future queue
intelligence requires separate activation before it may influence ordering.

### 10.1 Narrow capabilities

The controller may only:

- read exact repository, PR, ticket, approval and live-main identities;
- acquire and release the one-candidate branch lease;
- create/use the governed disposable integration workspace;
- perform one non-interactive mechanical rebase;
- compute and record the equivalence/interaction proof;
- publish the exact result with an expected old-head lease;
- observe fresh required CI;
- invoke canonical exact-candidate release verification;
- merge the exact expected authorised head; and
- append release attempts, proofs, receipts and merged proof.

It may not perform arbitrary repository writes, resolve conflicts, mutate
ticket definitions or criteria, change CI or branch protection, weaken policy,
change Symphony capacity, use unrestricted Linear mutation or infer human
approval.

## 11. Exact-candidate merge and race boundary

Automatic merge consumes only the candidate that passed the release gate. At
the final mutation boundary the controller must atomically or immediately
adjacently prove:

- its repository/protected-branch lease is current;
- protected `main` still equals the proof's `M` and is an ancestor of `H2`;
- the live PR head exactly equals expected authorised `H2`;
- the PR is still open, non-draft, same-repository and targets protected main;
- close-set, criteria fingerprint, reviewed paths, approval and equivalence
  proof are unchanged;
- complete required system-tier CI and release verification pass at `H2`;
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
| Close-set, ticket/criteria fingerprint, reviewed-path inventory or reviewed-change identity drift | `Review Required` | Human approval does not cover the candidate. |
| New/renamed/copied/unreviewed path | `Review Required` | Complete path-envelope guard failed. |
| Semantic interaction, incomplete interface coverage, uncertain equivalence, stale/disputed interaction evidence | `Review Required`; `Needs Human` when an explicit operator decision is required before a reviewable candidate exists | Uncertainty is not success. |
| Rebase conflict or any need for manual conflict resolution | `Needs Human` | Controller stops without publishing. |
| Fresh exact-candidate CI proves implementation-owned failure | `Changes Requested` | Returns to semantic remediation through the existing route. |
| CI pending, missing, cancelled, infrastructure-failed, malformed or provider-ambiguous | hold `Awaiting Release` | No failure or success is inferred; retry/reconcile under the release-attempt fence. |
| PR head or main moves before publication/merge | hold and restart from a fresh release attempt if identity remains authorised; otherwise `Review Required` | No stale lease or verdict is consumed. |
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
| `verification-engine.md`: criterion, scope and blanket human approval records are usable only at the exact head; scope derives from `relevant_docs + source_anchor`. | Exact-head machine evidence remains correct, but the heuristic scope model and repeated human records are not the right identity for approved semantic change. | Approval captures the complete reviewed path inventory and scope decisions once; the release verifier consumes approval and system proof without synthesising new human evidence. | Existing evaluators and evidence shapes remain unchanged. |
| Phase 15.5 binding decisions and disposition: the operator owns every post-review rebase; Atlas never resolves or publishes one automatically. | A clean mechanical integration can be delegated when exact delta equivalence and deterministic interaction independence are proved. | Dedicated serial Release Controller may perform only non-interactive JIT rebase and lease publication; conflict or uncertainty returns to humans. | Phase 12 operator lane remains the only production lane. |
| Phase 14/15.5/16 and `pr-acceptance.md`: merge is a manual operator action under a one-PR freeze. | Exact expected-head mutation and merged-proof readback can close the residual human click race while retaining operator review authority. | Dedicated controller may merge only the exact candidate passing the complete release gate and provider expected-head precondition. | Manual merge remains authoritative. |
| Phase 16 section 2.2: the operator retains review acceptance and manual merge; section 4.2 excludes automatic rebase/merge. | Review acceptance remains human, but narrow mechanical release authority is now separately governed. | Operator retains semantic approval; the activated Release Controller owns only the bounded capabilities in section 10. | Phase 16 implementation gains no release authority from this amendment alone. |
| Phase 16 P16-D27 and sections 20-21: `QueuePlan` is advisory with no merge/rebase/ticket-transition authority. | Not obsolete. Model/advisory planning must remain separated from production release authority. | Ruling retained; initial controller ordering is deterministic and does not consume `QueuePlan` as authority. | Unchanged. |
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
receipt foundations. Capture criteria fingerprint, complete trusted changed
paths and all explicit scope dispositions while retaining the current
exact-head/manual-merge lifecycle. Do not add `Awaiting Release`, rebase or
merge authority in this slice.

### Slice B - Awaiting Release and durable approval lifecycle

Add `Awaiting Release`, its Atlas/Linear mapping, transition ownership,
append-only `ReviewApproval` persistence and reconciliation fences. Keep it
absent from Symphony active states. No production Git mutation authority.

### Slice C - Equivalence proof and governed JIT integration

Implement the versioned change-manifest normaliser, interaction coverage
policy, proof model and clean non-interactive integration workspace. Prove the
decision in shadow first. Conflict, unsupported content and incomplete
classification fail closed. No automatic merge.

### Slice D - Serial Release Controller and exact-head merge

Add the one-candidate branch lease, deterministic selection, expected-old-head
publication, fresh exact-candidate CI observation, release verification,
provider expected-head merge, ambiguity fence, merged proof and routing matrix.
The controller remains disabled by default.

### Slice E - Live safety proof and activation

Exercise exact success plus head/main races, new paths, criteria/close-set
drift, interaction uncertainty, conflict, CI failure/infrastructure ambiguity,
provider timeouts and ambiguous merge outcomes against real provider/state
boundaries. Only an explicit operator activation after this evidence may
enable automatic release authority. Activation must name the deployed code,
schema, policy, credential/channel inventory, kill criteria and rollback.

The batch must not expand merely to mirror storage, API and UI layers. It may
combine inseparable foundations only while each ticket retains one primary
authority boundary and independently falsifiable tests.

## 15. Design acceptance conditions

Implementation and activation must preserve all of these conditions:

1. one explicit human review action, never inferred approval;
2. exact distinction among ticket, reviewed-change, candidate and release
   identities;
3. complete reviewed-path protection;
4. truthful human provenance at the actually reviewed head;
5. deterministic equivalence plus interaction independence before authority
   crosses a rebase;
6. fresh system-tier CI for every rebased candidate;
7. serial JIT release against live main;
8. a dedicated least-privilege controller, not generic PM mutation power;
9. exact expected-head merge and merged-proof verification;
10. typed fail-closed routing for drift, conflicts and uncertainty; and
11. explicit activation after implementation evidence.

Local validation of this design is agent-tier confidence only. Complete
GitHub CI at the published exact head remains system-tier authority.

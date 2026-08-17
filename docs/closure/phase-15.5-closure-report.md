# Phase 15.5 Closure Report — Parallel Delivery Efficiency and Integration

Date: 2026-08-17
Ticket: ATLAS-263 / Linear ATL-437
Controlled disposition: **PASS**
Live authority disposition at candidate authoring: **PENDING — begins at first
ATL-437 PR publication**
Symphony ceiling: **unchanged at one**
ATLAS-253: **remains `Needs Human` until this exact closure candidate is
accepted and merged**

## Closure rule

This report is the Phase 15.5 closure candidate, not advance authority for its
own future CI run. The deterministic comparison and adversarial matrix pass.
The last gate is ATL-437's live authority window from first PR publication
through its first determinate `CI Pending` exit and subsequent acceptance
disposition.

The publishing agent must enter `CI Pending` and stop in the same turn. The
system-tier reconciler and operator complete a bounded PR-linked receipt without
changing the candidate head. Phase 15.5 closes only if that receipt is PASS and
the operator accepts and merges this exact head. If the receipt is missing,
ambiguous or FAIL, this report remains a recorded controlled PASS with overall
`PENDING_LIVE_AUTHORITY` or FAIL; it does not release ATLAS-253.

This ordering resolves the otherwise circular evidence boundary: the commit
contains the fixed controlled evidence and the immutable rule for evaluating
its own later live window; system/operator evidence supplies the post-publication
facts without an agent-side CI poll or a head rewrite.

## Entry gate

The entry gate passed before the first controlled result:

- ATLAS-262 is `Done` and PR 334 merged to `main` at
  `7a3b59d58f1b4dde64edefe16ea5c57eafd6b649` after successful required checks.
- ATLAS-259 and ATLAS-260 remain documented FAIL. GitHub required results are
  contributor-head-pinned, no independent trusted candidate attestation closes
  the identity gap, and the synthetic no-rewrite route remains retired.
- The ATLAS-261/262 reactivation was traced to Linear's `PR opened -> In
  Progress` GitHub workflow automation. The operator disabled it on 17 August
  2026. `.atlas/atlas.db` held no corresponding trusted CI handoff records, the
  configured state map was correct, and generic PM sync had no authority for
  the transition.
- `WORKFLOW.md`, code and runbooks agree that the agent owns only `PR Open -> CI
  Pending`; the system-tier reconciler alone owns determinate exits.
- The fixed observation windows, workload-independence rule and numerical
  thresholds were present in the ATL-437 contract before the run.

## Fixed evidence package

The executable harness is `scripts/phase_15_5_milestone.py`. The immutable
fixture is `tests/fixtures/phase_15_5/milestone_v1.json`, with identity:

```text
8df6db27792661c9fe5365d20385cceb2398f3bad112e3696e46d6b775cc995b
```

The fixture was ratified at `2026-08-17T08:45:00Z`; measurement begins at
`2026-08-17T09:00:00Z`. The runner bounds the fixture at 128 KiB, an optional
live receipt at 32 KiB and retained output at 96 KiB. It uses a virtual clock,
selected fields and canonical JSON identities. It imports no network,
subprocess, GitHub, Linear, Symphony or database adapter and performs no
repository or external mutation.

The seeded receipt at
`tests/fixtures/phase_15_5/live_authority_seeded_pass.json` exercises the live
receipt validator and fault boundary. It is deterministic test evidence only;
it is explicitly not ATL-437's actual authority window.

## Workload-independence receipt

The same four inputs run exactly once in the same order under both models. The
identities, validation plans, touched families and protected-lane sets were
recorded before measurement:

| Workload | Workload identity | Validation-plan identity | Primary family | Protected lanes |
| --- | --- | --- | --- | --- |
| IND-1 | `83f879bbc149d7eeb1a5c76e312f0048d52482f18a4ef6263b1b2a2284421f27` | `12fa6da81dd152232b1c96edf22b6ea5322e55c61c30faa1ea72bcd5cfe3a123` | `atlas/verification/ci-handoff` | none |
| IND-2 | `bc386e242d6d6ea64b0ddde9afd94a764ec04b3fb0778f4fdbdafeb34bc57a14` | `8dd3756e8c96f6a55e6e91c0c7569d4bf01a09c9fc041ac9ab6cc2abdf112cf1` | `docs/runbooks` | none |
| IND-3 | `ca214e98cb75d1b880274d8f46d261db358eb7c950388393775eded90ac33a05` | `7ce8283a8ace79c925bfe49f2b1c526a7ebffd3581718eb841ac2cec8dfe82a7` | `apps/operator-ui/src/features/delivery-control` | none |
| IND-4 | `665a71e8ea34fb025b3caa4030e99c66e73c078ca0467b2714d50b0982dc7ad1` | `0e31ebbc9964ada8ddcf045fc2141173eee9709a95aa5d0f792693a9bedba00b` | `scripts` | none |

All primary families and touched path sets are pairwise disjoint. Empty
independent protected-lane sets are pairwise disjoint. `LANE-A` and `LANE-B`
are separate from the four-item numerator, share exactly
`operator-admission-hotspot`, and have protected-window identity
`a98d714313f8bab8828b2534af22c45f0b3bdad79f3639f458f92b06509c6352`.

## Controlled comparison receipt

The baseline is a deterministic replay of the documented pre-Phase-15.5 model,
not a production rollback. Its worker retains CI-wait time and performs one
complete local sweep. The Phase 15.5 replay uses each predeclared scoped plan,
publishes once, enters `CI Pending`, releases the worker and leaves complete CI
to system authority.

| Threshold | Baseline | Phase 15.5 | Required | Result |
| --- | ---: | ---: | ---: | --- |
| median agent-active time | 1158 s | 440.5 s | phase <= 85% | 38.04%, PASS |
| worst matched active-time ratio | — | 38.35% | <= 125% | PASS |
| median local-validation time | 480 s | 210 s | phase <= 75% | 43.75%, PASS |
| median CI queue+run time | 450 s | 450 s | phase <= 120% | 100%, PASS |
| maximum normal CI queue+run | — | 540 s | <= 900 s | PASS |
| median review dwell | 225 s | 45 s | phase <= 120% | 20%, PASS |
| completions/aggregate agent-hour | 3.1088 | 8.1679 | >= 1.20x | 2.6273x, PASS |

All four Phase 15.5 fixtures reach the expected accepted completion. There are
zero duplicate publications, repeated complete validation caused by CI wait,
Phase 15.5 full sweeps, agent CI polls, semantic conflicts and mechanical
rebases. Maximum slot-release time is four seconds.

Working, integration and review occupancy peak at 4, 4 and 1 against fixed
budgets 4, 4 and 4. `LANE-B` produces a deterministic hold while `LANE-A` owns
the shared lane, independent work remains admissible, and the contender has
zero publications before release.

The runner retains eight of eight baseline/Phase measured records. Every record
contains parent ticket/workload, head, validation-plan, CI run/evidence, policy,
state-transition and timestamp identities. Local-validation, agent-active and
post-implementation validation/handoff durations are separate fields.

## CI, freshness and reactivation matrix

The CI matrix proves these exact routes:

| Evidence class | Decision | Owner | Transition writes |
| --- | --- | --- | ---: |
| complete passed | Review Required | system-tier reconciler | 1 |
| complete definite implementation failure | Changes Requested | system-tier reconciler | 1 |
| pending | hold | system-tier reconciler | 0 |
| missing | hold | system-tier reconciler | 0 |
| infrastructure | hold | system-tier reconciler | 0 |
| malformed | hold | system-tier reconciler | 0 |
| stale | hold | system-tier reconciler | 0 |
| provider ambiguous/contradictory | hold | system-tier reconciler | 0 |
| partial implementation failure | hold | system-tier reconciler | 0 |

The freshness matrix admits only exact contributor-head/current-main authority.
Mechanically behind, diverged and conflicted candidates route to the
operator-owned rebase lane. Head movement, base movement and provider ambiguity
invalidate or hold the identity. Synthetic candidate identity, composition,
mergeability and tree equality remain diagnostic only.

The adversarial reactivation matrix assigns immediate FAIL to `CI Pending -> In
Progress` and `CI Pending -> PR Open`. Only the separately authorised `Changes
Requested -> In Progress` semantic-remediation route is admitted. This tests the
detector; the actual live threshold remains zero observed reactivations.

## Authority and retention spies

The selected-field repository and external-call spies record:

- zero automatic merge, rebase, push or branch update;
- zero worker cancellation or CI mutation;
- zero plan approval, permission expansion or deployment;
- zero repository/external writes by the comparison harness;
- zero secret, credential-canary, raw provider payload or workspace-path
  retention; and
- a deterministic bounded report under repeated evaluation.

Production-domain tests separately exercise the actual deterministic
validation planner, protected-lane classifier, coherent occupancy snapshot,
CI-handoff reconciler, exact-head classifier, API projection, Operator UI and
workflow contract with mutation/fault spies. The milestone adds composition
evidence; it does not create a second production authority implementation.

## Live authority receipt required at publication

The PR handoff record must pin all of the following:

1. repository, same-repository PR number, `main` base, ticket branch and exact
   validated head;
2. ATL-437 `CI Pending` observation timestamp and Symphony worker-stop
   timestamp, with delta <= 5 seconds;
3. zero agent CI polls and no repeated validation/publication after handoff;
4. complete determinate CI timestamp, reconciler tick duration and exact exit
   timestamp, with one tick and <= 5 minutes;
5. system-tier reconciler ownership of the sole determinate exit;
6. zero `CI Pending -> In Progress`, `CI Pending -> PR Open` or other
   Symphony-active reactivation;
7. confirmation that Linear/GitHub linked evidence but performed no workflow
   state mutation; and
8. subsequent exact-head acceptance disposition without synthetic-candidate
   authority.

The external automation remediation is accepted only if this receipt passes.
Any unexplained transition, stale identity, missing timestamp or ambiguous
owner makes the milestone FAIL. The operator must not repair the evidence by
moving the card or rewriting the branch.

## Documentation and executable evidence ledger

| Contract | Evidence |
| --- | --- |
| Workflow/slot release | `WORKFLOW.md`; `tests/test_workflow_contract.py`; Phase 15.5 harness |
| Fixed comparison and thresholds | `tests/fixtures/phase_15_5/milestone_v1.json`; `tests/test_phase_15_5_milestone.py` |
| CI ownership and fault routes | `tests/test_ci_handoff_reconciliation.py`; milestone CI matrix |
| Protected lane hold | `tests/test_protected_lanes.py`; `tests/test_delivery_snapshot.py`; `LANE-A/LANE-B` receipt |
| Exact-head/rebase-only freshness | `tests/test_pr_integration.py`; milestone freshness matrix |
| Pressure API and console | `tests/test_delivery_control_api.py`; `tests/test_delivery_control_pressure_architecture.py`; Operator UI acceptance/component/e2e suites |
| Prohibited authority | production mutation-spy tests; milestone AST/call/file spies |
| Canonical contract | `ROADMAP.md`; `docs/atlas/implementation-roadmap.md`; `docs/atlas/multi-agent-delivery-control.md`; `docs/atlas/parallel-delivery-efficiency-and-integration-control.md` |
| Operator procedure | `docs/runbooks/local-development.md`; `docs/runbooks/pr-acceptance.md` |
| Actual external remediation | PR-linked ATL-437 live authority receipt, completed after publication |

## Final disposition

The fixed controlled comparison is PASS. The synthetic no-rewrite route remains
retired. The ceiling remains one and no Phase 15/ATLAS-253 ramp occurs here.

At candidate authoring, overall Phase 15.5 status is
`PENDING_LIVE_AUTHORITY`. If the exact ATL-437 publication receipt passes and
the operator accepts and merges this unchanged head, the merge records Phase
15.5 closure and permits the operator to consider releasing ATLAS-253 in a
separate action. Otherwise Phase 15.5 remains open, ATLAS-253 remains `Needs
Human`, and no committed Symphony ceiling changes.

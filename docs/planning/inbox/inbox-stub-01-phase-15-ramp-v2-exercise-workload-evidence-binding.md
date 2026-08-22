---
title: "Phase 15 ramp v2 exercise-workload evidence binding"
objective: >-
  Make the ATLAS-253 milestone validator fail closed unless deliberate
  protected-lane exercises are bound to predeclared real exercise workloads
  classified against the pinned repository protected-lane registry, while
  preserving the >10 independent ordinary workload pool and the validator's
  offline/read-only, non-authoritative semantics.
context: >-
  The current phase-15-ramp-workload-v1 contract accepts arbitrary manifest
  lane strings and lets a receipt claim protected-lane occupancy and passed
  exercise evidence for undeclared identities such as META-GATE-*. The stock
  fixture can therefore validate a complete gate sequence without binding a
  protected-lane owner or blocked candidate to any pre-ratified real workload.
  This is a harness/contract defect, not merely a bad live manifest. This
  bounded repair is an implementation prerequisite to resuming ATLAS-253: the
  milestone remains operator-paused at the proven Attempt-3 ceiling 1 / turns
  10 identity until the repair is accepted and its live v2 contract is
  separately re-ratified. The ticket does not perform that re-ratification or
  any live exercise.
ticket_type: infrastructure
epic_ref: ATLAS-E10
risk_level: critical
component: delivery-control
tags:
  - phase-15
  - milestone-harness
  - protected-lanes
  - evidence-binding
relevant_docs:
  - docs/atlas/agentic-engineering-programme-design.md
  - docs/atlas/multi-agent-delivery-control.md
  - docs/atlas/parallel-delivery-efficiency-and-integration-control.md
  - docs/atlas/phase-16-agent-runtime-and-integration-safety.md
  - docs/runbooks/local-development.md
  - docs/runbooks/operator-environment.md
depends_on:
  - ATLAS-258
acceptance_criteria:
  - >-
    A new live-proof-authoritative v2 manifest/receipt contract preserves more
    than ten ordinary
    dependency-independent workloads with unique path families, mutually
    disjoint touched paths and mutually independent protected lanes, and adds
    the pinned protected-lane registry version and semantic fingerprint plus a
    separate bounded exercise_workloads collection. Each exercise workload
    has stable workload and real ticket identities, exact path family and
    paths, component, tags and declared document/path classifier inputs,
    recomputed lane classification and classification fingerprint, and an
    explicit ordinary-throughput exclusion; every field participates in the
    deterministic manifest fingerprint.
  - >-
    The validator loads the actual digest-pinned repository protected-lane
    registry and recomputes classification through the repository classifier;
    unknown lanes, registry version/fingerprint drift, classification drift or
    disagreement, duplicate identities, orphaned exercise workloads and
    undeclared or substituted identities fail closed. Same-lane exercise
    workloads are valid only when explicitly co-bound to the deliberate Gate 3
    protected_lane_contention exercise.
  - >-
    Exact gate/exercise bindings name each exercise workload and its bounded
    role. Gate 1 protected_lane_ci_pending_hold requires one declared owner
    that appears coherently in protected-lane occupancy through CI Pending;
    Gate 3 protected_lane_contention requires distinct declared owner and
    blocked_candidate identities that recompute into the same lane. A positive
    protected_lane_hold_count without those bindings is insufficient.
  - >-
    Every protected-lane exercise receipt binds observed workload identity,
    real ticket identity, role, protected lane and evidence identity to the
    exact manifest fingerprint. Wrong-gate, wrong-exercise, wrong-role,
    occupancy mismatch, undeclared owner, orphan, duplicate and META-GATE-style
    evidence fail closed, and post-ratification workload substitution changes
    or invalidates the fingerprint.
  - >-
    Existing v1 Attempt-1 and Attempt-2 records remain secret-free,
    deterministic and replayable under an explicitly historical result without
    rewriting their receipts, but v1 cannot return a successful result for a
    new future live gate proof after the v2 repair becomes authoritative.
  - >-
    Under every valid, invalid and historical input the validator remains
    bounded, offline and read-only, retains no secret-bearing material, makes
    no network, repository, database, Linear, Symphony, runtime or policy
    mutation, and reports transition_authorized=false and
    closure_authorized=false.
  - >-
    Contract-valid v2 fixtures and focused tests cover all v2 success and
    failure boundaries, retain the unchanged ordinary-workload rules, replace
    invented fixture lanes/owners, and the five named canonical documents are
    updated so ATLAS-253 live-contract and manifest re-ratification occurs only
    after this implementation is accepted.
non_goals:
  - >-
    No Gate 1 start or observation, Symphony runtime alteration, delivery-policy
    change, WORKFLOW.md ceiling/turn change, admission, ticket-state transition,
    Linear write, live-manifest edit or milestone-branch/PR #340 change.
  - >-
    No creation of the later real Gate-1 owner workload, the two later Gate-3
    contention workloads or any calibration-workload ticket, and no live v2
    manifest re-ratification.
  - >-
    No edit to ATLAS-266..278 contracts, no weakening of ordinary workload
    requirements merely to make a gate pass, and no arbitrary manifest lane
    string treated as authority.
  - >-
    No transition or closure authority, historical Attempt-1/Attempt-2 receipt
    rewrite, manual docs/planning render edit, database migration, dependency
    policy change or unrelated product work.
test_requirements:
  - >-
    Extend tests/test_phase_15_delivery_control_milestone.py with a valid v2
    sequence containing more than ten unchanged ordinary workloads, a declared
    Gate-1 owner and explicit Gate-3 same-lane owner/blocked-candidate binding;
    prove ordinary path/lane independence and stable/order-independent
    manifest, classification and receipt fingerprints.
  - >-
    Add table-driven negative cases for fake/unknown lanes, registry
    version/fingerprint and classifier drift, declared/recomputed disagreement,
    unbound same-lane collision, missing/orphaned/duplicate/substituted
    identities, wrong gate/exercise/role, undeclared META-GATE-style evidence,
    occupancy mismatch, a hold counter without workload-bound evidence and
    post-ratification substitution.
  - >-
    Prove Gate 1 cannot validate without a declared owner occupying its lane
    through CI Pending and Gate 3 cannot validate without distinct declared
    same-lane owner/candidate identities; preserve deterministic failure and
    bounded-output behavior.
  - >-
    Prove historical v1 replay cannot establish a new live PASS, secret
    scanning remains enforced, mutation/network authority spies remain zero,
    and transition_authorized and closure_authorized remain false for every
    result.
  - >-
    Add a contract-valid v2 live-pass fixture with real declared
    protected-lane identities and confine the existing v1 fixture material to
    explicit historical replay coverage; no invented v1 lane or META-GATE-style
    owner may serve as future live-pass evidence.
implementation_notes:
  - >-
    Keep the repair centred on scripts/phase_15_delivery_control_milestone.py,
    tests/test_phase_15_delivery_control_milestone.py and bounded Phase 15
    fixtures. Reuse atlas/pm/protected_lanes.py and its packaged registry as
    the classification authority; refactor only a small pure materialisation
    seam if the existing Ticket-shaped entry point cannot safely consume the
    manifest's exact classifier inputs.
  - >-
    Prefer v2 exercise_workloads entries containing exercise_workload_id,
    ticket_key, touched_path_family, touched_paths, component, tags,
    relevant_docs, documentation_requirements, excluded_from_throughput,
    reconstructed classification and classification_fingerprint. Keep exact
    gate/exercise/role bindings separate so deliberate contention is explicit
    rather than inferred from duplicate lanes.
  - >-
    ATLAS-258 is the delivered classifier prerequisite. Do not declare an
    inverse dependency on ATLAS-253: its future keyless repair cannot be added
    to the existing milestone graph here. The operator checkpoint instead
    keeps ATLAS-253 paused until this repair is minted, implemented and
    accepted, then separately re-ratifies the live v2 manifest.
  - >-
    Preserve canonical JSON ordering, bounded input/output sizes, exact-field
    rejection and secret scanning. Do not add runtime clocks, network reads,
    repository writes or operational-state lookups to validation.
documentation_requirements:
  - docs/runbooks/operator-environment.md
  - docs/atlas/multi-agent-delivery-control.md
  - docs/runbooks/local-development.md
  - docs/atlas/agentic-engineering-programme-design.md
  - docs/atlas/phase-16-agent-runtime-and-integration-safety.md
definition_of_done:
  - >-
    All seven acceptance criteria have focused deterministic evidence; the
    v2 harness fails closed on any undeclared or substituted protected-lane
    identity while preserving the ordinary pool, v1 history and zero-authority
    boundary; every named canonical document is coherent; repository-selected
    validation passes; and ATLAS-253 remains paused pending separate v2
    live-contract and manifest re-ratification.
---

# Phase 15 ramp v2 exercise-workload evidence binding

Repair the evidence contract before any new ATLAS-253 live gate proof.

---
title: "Evidence-backed Planned-to-CI-Pending mirror recovery after governed admission"
objective: >-
  Allow Atlas to recover its local ticket mirror from Planned directly to CI
  Pending only when durable system-owned evidence uniquely proves that Atlas
  previously admitted that exact ticket and the same Linear issue subsequently
  published the exact issue-bound PR, while preserving the existing fail-closed
  ownership rules and CI-handoff authority.
context: >-
  The ATLAS-280 live run exposed a legitimate poll-compression race. Atlas
  selected and admitted the ticket through the governed revision-16 admission
  path, whose confirmed Ready for Agent write intentionally left the local
  mirror at Planned for the next pull. Symphony claimed the same Linear issue
  and reached publication and CI Pending before another PM pull observed any
  intermediate state. Generic pull then correctly rejected Planned to CI
  Pending as out of ownership. Atlas retains the exact successful AdmissionRun,
  its uniquely correlated successful PM receipt and the coherent issue-bound
  GitHub publication, but no sanctioned predicate can currently use that chain.
  DebtItem 26bfc848f7a94287be73c7c2de12ed44 is correct append-only historical
  evidence. ATLAS-253 remains paused and this ticket grants no live recovery,
  milestone, policy, runtime or tracker mutation by itself.
ticket_type: infrastructure
epic_ref: ATLAS-E6
risk_level: critical
component: delivery-control
tags:
  - phase-15
  - pm-sync
  - ci-pending
  - mirror-recovery
  - admission-evidence
relevant_docs:
  - docs/architecture/data-model-and-schemas.md
  - docs/atlas/multi-agent-delivery-control.md
  - docs/atlas/parallel-delivery-efficiency-and-integration-control.md
  - docs/atlas/pm-engine-and-linear-sync.md
  - docs/runbooks/operator-environment.md
  - docs/runbooks/troubleshooting.md
depends_on:
  - ATLAS-249
  - ATLAS-255
  - ATLAS-256
acceptance_criteria:
  - >-
    A dedicated recovery predicate accepts local Planned to observed CI Pending
    only when exactly one successful AdmissionRun selected the exact Atlas
    ticket and ticket UUID, product and external Linear UUID all agree, and one
    uniquely matching successful PM receipt proves admitted=promoted=1 with no
    stale or indeterminate outcome.
  - >-
    Recovery additionally requires one complete, coherent, issue-bound GitHub
    publication for that same Linear issue and exact ticket. Existing genuine
    AgentRun evidence may strengthen the proof, but its absence does not block
    recovery when the canonical publication-equivalence contract applies; the
    recovery path never fabricates an AgentRun.
  - >-
    Missing, duplicate, contradictory or mismatched admission runs, receipts,
    ticket or Linear identities, publications, complete-board observations,
    transition history, or active admission/CI-handoff fences fail closed as
    OUT_OF_OWNERSHIP_TRANSITION. Planned is not added to the generic
    CI_PENDING_POLL_COMPRESSION_SOURCES set.
  - >-
    Successful recovery appends exactly one direct local Planned to CI Pending
    transition with dedicated recovery provenance, invents no Ready for Agent,
    In Progress or PR Open transition or timestamp, performs no Linear write
    and remains idempotent under repeated execution.
  - >-
    Successful recovery atomically appends bounded immutable audit evidence
    identifying the AdmissionRun, PM receipt, ticket and external issue,
    observed Linear state, publication, board fingerprint and deterministic
    recovery identity. No provider payload, issue or PR body, credential,
    token, secret or other unbounded content is retained; existing durable
    repositories are reused unless a narrowly scoped append-only recovery
    record is mechanically required.
  - >-
    The predicate can recover when the same CI Pending state was already
    observed and an OUT_OF_OWNERSHIP_TRANSITION DebtItem was recorded. Existing
    debt, including 26bfc848f7a94287be73c7c2de12ed44, remains historical and
    is never deleted or rewritten, and replay appends neither duplicate state
    transitions nor duplicate recovery evidence.
  - >-
    After local recovery, only the existing ATLAS-256 CI-handoff reconciler may
    evaluate and write CI Pending to Review Required or Changes Requested.
    Deterministic tests prove a subsequent green exact-head cadence uses that
    existing authority, while pending, missing, stale, malformed,
    infrastructure or indeterminate CI remains CI Pending.
non_goals:
  - >-
    No generic Planned to CI Pending permission and no recovery from Backlog,
    Blocked, Needs Human, review or terminal states.
  - >-
    No manual state reconstruction, compensating Linear write, invented
    intermediate transition, fabricated AgentRun, automatic CI acceptance,
    merge or rebase authority.
  - >-
    No ATLAS-280 implementation or PR #350 change, DebtItem deletion or rewrite,
    atlas plan/apply execution, planning-render edit, live recovery execution or
    admission of another ticket.
  - >-
    No ATLAS-253, live-manifest, Symphony, runtime, policy or WORKFLOW change and
    no authority to resume Gate 1.
test_requirements:
  - >-
    Add an ATLAS-280-shaped deterministic positive test with one exact selected
    AdmissionRun, its uniquely correlated successful PM receipt, the exact
    external issue and one coherent publication. Prove the predicate still runs
    after the CI Pending state and historical DebtItem were already recorded.
  - >-
    Add table-driven negative tests for absent, duplicate, mismatched,
    contradictory or non-successful runs/receipts; wrong ticket, product or
    external identity; incomplete/duplicate board pulls; missing, incomplete,
    ambiguous or mismatched publications; conflicting local history; and either
    write fence.
  - >-
    Prove accepted recovery is atomic and idempotent, writes one direct local
    transition plus one bounded immutable evidence identity, writes no Linear
    state, creates no intermediate timestamp or AgentRun, preserves historical
    debt and does not widen generic poll-compression sources or other
    pre-dispatch states.
  - >-
    Extend supported-cadence CI-handoff tests so green exact-head evidence can
    transition only through the existing reconciler after local recovery, while
    pending, missing, stale, malformed, infrastructure and indeterminate
    evidence perform no CI Pending exit.
implementation_notes:
  - >-
    Keep the recovery predicate separate from
    CI_PENDING_POLL_COMPRESSION_SOURCES. Reuse the immutable AdmissionRun and PM
    receipt repositories, exact external-identity join, complete Linear board
    DTO and issue-bound publication resolver. Correlate evidence by bounded
    explicit identities and cardinality, never title, branch, body text,
    approximate timestamps or operator assertion.
  - >-
    Inspect atlas/pm/sync.py, admission_sync.py, admission.py, agent_runs.py,
    ci_handoff.py and ci_handoff_adapter.py plus their storage repositories
    before choosing storage shape. If existing rows cannot durably identify the
    accepted recovery decision, add only the smallest append-only model and
    atomic repository seam necessary for the bounded evidence record.
  - >-
    Preserve the pull ordering and authority split: recovery may update only
    the Atlas mirror; the existing CI-handoff adapter independently re-resolves
    publication and exact-head system evidence before its owner-specific Linear
    exit. A recovery record is evidence of mirror catch-up, not CI success.
  - >-
    Ensure the previously stamped last_observed_linear_state_id does not prevent
    proof-backed reconsideration, while unproved repeated observations retain
    the current deduplicated anomaly behaviour.
documentation_requirements:
  - docs/architecture/data-model-and-schemas.md
  - docs/atlas/parallel-delivery-efficiency-and-integration-control.md
  - docs/atlas/pm-engine-and-linear-sync.md
  - docs/runbooks/troubleshooting.md
definition_of_done:
  - >-
    All seven acceptance criteria have focused deterministic evidence; the
    dedicated recovery remains exact, local-only, immutable, idempotent and
    fail-closed; existing poll compression and ATLAS-256 exit authority remain
    unchanged; canonical documentation is coherent; repository-selected
    validation passes; and ATLAS-253 remains paused.
---

# Evidence-backed Planned-to-CI-Pending mirror recovery after governed admission

Recover only from a uniquely reconstructed governed delivery chain.

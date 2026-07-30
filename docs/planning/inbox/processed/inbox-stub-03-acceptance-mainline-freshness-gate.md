---
title: "Mainline freshness gate and exact-head acceptance restart"
objective: >-
  Make exact current-main ancestry a binding precondition of the canonical PR
  acceptance spine, and require evidence, human confirmations and verification
  to restart at every operator-rebased head before the merge prompt can appear.
context: >-
  `scripts/close_ticket.py` currently begins with `atlas evidence pull`; it can
  therefore write evidence and prompt for human confirmations before proving
  that the PR contains current main. The runbook relies on operator discipline
  and routes a sibling-staled Review Required PR through Changes Requested,
  even when no semantic remediation is needed. The first two Phase 12 tickets
  provide one exact-head classifier and a lease-guarded operator rebase lane.
  This ticket makes them binding in acceptance. Pre-ruled decisions: D-1 the
  initial freshness assessment is read-only and runs before evidence pull,
  confirmation or any operator prompt. D-2 only overall `current` enters the
  acceptance spine; behind, diverged, conflicted, ineligible and indeterminate
  states fail closed and name the operator rebase command. D-3 after evidence,
  confirmation and a PASSED exact-head verification, the script assesses the
  PR again immediately before displaying the merge prompt. The live PR head
  must equal the verified head and initial head, and the live base SHA must
  equal the initial base SHA; movement restarts the spine. D-4 evidence and
  confirmations stay append-only, but existing commit-SHA matching remains
  authoritative: records from the old head are history, never authority for
  the rebased head. D-5 an operator rebase leaves the ticket in Review
  Required; Changes Requested is reserved for semantic/code remediation that
  Symphony must perform. D-6 the merge remains a manual operator action and
  the existing one-PR freeze interval remains binding; this ticket does not
  claim to eliminate a race after the final assessment and before the manual
  GitHub merge.
ticket_type: infrastructure
epic_ref: ATLAS-E10
risk_level: high
component: verification
tags:
  - verification
  - orchestration
  - exact-head
  - acceptance
  - mainline
relevant_docs:
  - "docs/runbooks/pr-acceptance.md"
  - "docs/atlas/symphony-integration.md"
  - "docs/atlas/verification-engine.md"
  - "docs/decisions/0008-ci-sourced-evidence-with-trust-tiers.md"
depends_on:
  - "inbox-stub-01-pr-integration-assessment.md"
  - "inbox-stub-02-operator-pr-rebase-lane.md"
acceptance_criteria:
  - >-
    `scripts/close_ticket.py` calls the shared exact-head assessment after its
    local/token/operator preflight but before `atlas evidence pull`, any Atlas
    write or any operator prompt, and snapshots the eligible current PR's head
    SHA, base SHA, branch identity and repository identity for the rest of the
    run.
  - >-
    Behind, diverged, conflicted, draft/fork/non-main/closed or indeterminate
    initial assessments exit non-zero before evidence, confirmation,
    verification or prompt, print the named state and exact
    `atlas pr rebase prepare --pr N --repo OWNER/REPO` recovery command when
    the PR is eligible for that lane, and perform no GitHub or Linear write.
  - >-
    The existing evidence → confirm → `verify --json` sequence remains ordered
    and requires top-level `status == "passed"` with a valid `head_commit`;
    immediately afterward, before the merge prompt, a second live assessment
    must still be current and its head must equal both the initial head and the
    verified head while its base SHA, branch and repository identities equal
    the initial snapshot.
  - >-
    Any PR-head movement, main movement, eligibility change, compare failure or
    indeterminate mergeability between the two assessments blocks the merge
    prompt and all post-merge steps. Tests prove stale evidence already pulled
    during that interrupted run cannot make a later new-head run skip the
    exact-head human and machine gates.
  - >-
    A completed operator rebase leaves the close-set in `review_required`; on
    rerun, evidence and confirmations from the original head remain append-only
    but every evaluator ignores them for the new SHA, so new-head evidence,
    acceptance-criterion confirmation, manual approval and PASSED verification
    are all required before the merge prompt.
  - >-
    `WORKFLOW.md`, `docs/atlas/symphony-integration.md` and
    `docs/runbooks/pr-acceptance.md` state one ownership rule: agents keep
    ATLAS-168's pre-handoff rebase discipline, operators use the Phase 12 lane
    for mechanical staleness after Review Required, and Changes Requested is
    used only when implementation or other semantic remediation must return to
    Symphony; the one-PR freeze-to-manual-merge residual window is explicit.
  - >-
    Script tests cover current success, every initial fail-closed class, head
    race, base/main race, second-assessment API failure, verified-head mismatch,
    old-head evidence/confirmation rejection, no-prompt/no-write ordering and
    the unchanged merged-proof/schema-upgrade/two-sync completion tail.
non_goals:
  - >-
    No automatic rebase, conflict resolution, branch push, evidence deletion
    or mutation, confirmation deletion or mutation, automatic GitHub merge,
    merge queue, auto-merge, new pre-merge Linear status write,
    verification-matrix redesign, Operator UI control or relaxation of
    ADR-0008 exact-commit matching; the existing post-merge two-sync completion
    tail remains unchanged.
test_requirements:
  - >-
    Extend `tests/test_close_ticket_script.py` with injected assessment
    snapshots and command/prompt spies; add focused evaluator tests only where
    needed to prove old-head records cannot satisfy a new head. No live GitHub
    or Linear calls and `ATLAS_LIVE_TESTS=0`.
  - >-
    Preserve existing closure-tail tests and add explicit assertions that no
    `atlas evidence`, `confirm`, `verify`, merge prompt, checkout, migration or
    sync action occurs before/after the corresponding fail-closed boundary;
    seeded Python defects use `assert 1 == 2` (B011).
implementation_notes:
  - >-
    Call the Phase 12 assessment service in process; do not parse
    `atlas pr status` text/JSON or duplicate its classification inside the
    script. Keep assessment resolution injectable like the existing
    `resolve_context`, command runner, pause and status-reader seams.
  - >-
    Store the first assessment as the run's immutable freshness snapshot. The
    second assessment is a fresh GitHub read, not a reused object or local
    remote-tracking ref.
  - >-
    Preserve the current manual merge boundary and the post-merge check that
    GitHub's merged PR head equals the verified head. The new gate strengthens
    the pre-merge side; it does not move merge authority into Atlas.
documentation_requirements:
  - >-
    Replace the post-verdict `Changes Requested` fallback consistently in
    `WORKFLOW.md`, `symphony-integration.md` and `pr-acceptance.md`; document
    the exact restart sequence and retain the existing ADR-0008
    evidence-at-head and manual-merge rules.
definition_of_done:
  - >-
    All seven acceptance criteria are evidenced by named tests; the full
    Python gate sweep and doc linter are green; the three canonical workflow
    surfaces agree in the same change; the PR title carries the minted ticket
    key.
---

# Mainline freshness gate and exact-head acceptance restart

A rebased head is new work for acceptance purposes: evidence, confirmation and
verification all restart at that exact commit.

---
title: "No-rewrite exact-base acceptance assessment"
objective: >-
  Accept a clean PR against current `main` from exact integration-candidate
  evidence without rewriting its branch, while retaining the operator rebase
  lane for conflicts, missing evidence and every indeterminate case.
context: >-
  A successful feasibility result allows Atlas to distinguish branch freshness
  from integration freshness. The accepted identity becomes the tuple of
  repository, PR, contributor head, live base and provider integration
  candidate. Evidence, confirmations and readiness bind to that complete tuple.
  Atlas remains advisory: it does not update, rebase or merge the branch.
ticket_type: infrastructure
epic_ref: ATLAS-E9
risk_level: critical
component: integration
tags:
  - phase-15-5
  - exact-base
  - acceptance
  - rebase
relevant_docs:
  - "docs/atlas/evidence-pipeline.md"
  - "docs/atlas/parallel-delivery-efficiency-and-integration-control.md"
  - "docs/atlas/review-acceptance-console.md"
  - "docs/atlas/symphony-integration.md"
  - "docs/atlas/verification-engine.md"
  - "docs/runbooks/pr-acceptance.md"
depends_on:
  - "inbox-stub-06-synthetic-merge-feasibility.md"
  - "ATLAS-244"
acceptance_criteria:
  - >-
    The canonical assessment returns typed `exact_branch`,
    `exact_integration_candidate`, `rebase_required` or `indeterminate` using
    the feasibility-approved identity algebra and no weaker provider signal.
  - >-
    An exact integration candidate is ready only when every required CI check
    is PASSED for that candidate and the live repository, PR, contributor head,
    base and candidate identities remain unchanged before and after verdict.
  - >-
    Contributor-head movement, base movement, candidate replacement, conflict,
    missing required check, provider timeout or malformed response invalidates
    authority and never falls back optimistically to head-only evidence.
  - >-
    Acceptance sessions, machine evidence, human confirmations and the stored
    verdict record the complete integration identity; older head/base candidate
    history remains visible but cannot authorise the current merge.
  - >-
    `rebase_required` routes only to the existing operator-owned lease-guarded
    lane, while exact clean candidates may enter the one-PR freeze without a
    branch rewrite; neither route performs a mutation from assessment.
  - >-
    Executable route and external-call inventories prove Atlas gains no GitHub
    merge/update, Git rebase/push, Linear review transition, Symphony control or
    automatic acceptance authority.
non_goals:
  - >-
    No merge queue, automatic merge, automatic rebase, force push, conflict
    resolution, weakening of the one-PR freeze or reuse of evidence after base
    movement.
test_requirements:
  - >-
    Exact-identity tests cover clean branch, clean synthetic candidate, every
    movement seam, conflicts, missing/failed checks, timeout/malformed reads and
    post-PASSED revocation.
  - >-
    Seeded live-API/browser tests prove current readiness for a clean candidate
    without branch mutation and prove the rebase fallback remains fail closed.
implementation_notes:
  - >-
    Implement only if the predecessor spike records PASS for the chosen provider
    contract. Preserve the existing exact-branch path and rebase lane as safe
    fallbacks rather than replacing them.
documentation_requirements:
  - "docs/atlas/evidence-pipeline.md"
  - "docs/atlas/parallel-delivery-efficiency-and-integration-control.md"
  - "docs/atlas/review-acceptance-console.md"
  - "docs/atlas/symphony-integration.md"
  - "docs/atlas/verification-engine.md"
  - "docs/runbooks/pr-acceptance.md"
definition_of_done:
  - >-
    All six criteria have exact-identity and adversarial evidence, the clean
    no-rewrite path is advisory and current-base-safe, prohibited mutations are
    mechanically absent, full applicable gates pass and the PR title carries
    the minted ticket key.
---

# No-rewrite exact-base acceptance assessment

Integration freshness can remove needless rebases without relaxing identity.

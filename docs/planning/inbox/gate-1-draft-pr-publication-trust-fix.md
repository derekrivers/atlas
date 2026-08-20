---
title: "Accept coherent draft GitHub publications at the CI-handoff trust boundary"
objective: >-
  Allow the production CI-handoff adapter to trust a coherent issue-bound
  Linear GitHub publication whose pull request is still draft, while preserving
  the existing fail-closed repository, PR, base-branch and attachment identity
  checks.

context: >-
  The first live ATLAS-253 Gate 1 attempt exposed a production compatibility
  defect at ceiling one. ATLAS-264 published draft PR #341 at exact head
  4aa61c550f49927dc25c445b0b8ec52566a9c06e and GitHub CI completed
  successfully, but Atlas repeatedly held the ticket in CI Pending before
  domain reconciliation. Linear returned one complete GitHub attachment with
  canonical URL https://github.com/derekrivers/atlas/pull/341 and coherent
  repoLogin=derekrivers, repoName=atlas, number=341, numeric PR/repository ids,
  linkKind=closes and targetBranch=main. The only differing field was the
  truthful PR lifecycle value status=draft. atlas.linear.client currently
  requires status=open, so the complete attachment is classified contradictory,
  github_publications_complete becomes false, the CI-handoff adapter fails
  closed before evidence ingestion/reconciliation, PM receipts become partial
  and a determinate green CI result cannot reach Review Required.

ticket_type: bug
epic_ref: ATLAS-E10
risk_level: high
component: delivery-coordination

tags:
  - phase-15
  - ci-handoff
  - linear
  - github
  - fail-closed
  - gate-1

relevant_docs:
  - "WORKFLOW.md"
  - "docs/atlas/evidence-pipeline.md"
  - "docs/atlas/parallel-delivery-efficiency-and-integration-control.md"
  - "docs/runbooks/operator-environment.md"

depends_on: []

acceptance_criteria:
  - >-
    A coherent Linear GitHub attachment whose metadata status is draft is
    accepted as a trusted live pull-request publication when every existing
    repository, PR number, URL, link kind, target branch and numeric identity
    check also passes.
  - >-
    Existing coherent status=open publications remain accepted with identical
    canonical repository/PR identity.
  - >-
    Closed, merged, unknown or malformed publication statuses remain rejected;
    accepting draft must not weaken any repository, URL, PR-number, base-branch,
    link-kind, pagination-completeness or metadata-consistency check.
  - >-
    The production CI-handoff adapter can proceed from an issue-bound coherent
    draft publication into exact-head evidence ingestion and domain
    reconciliation instead of returning trusted_publication_ambiguous.
  - >-
    Regression coverage reproduces the Gate-1 failure shape: one complete
    draft GitHub attachment for a CI Pending ticket with green exact-head CI is
    not discarded solely because the PR remains draft.
  - >-
    The change adds no fallback inference from title, branch name, comments or
    arbitrary GitHub search; Linear's issue-bound publication plus independent
    GitHub exact-head revalidation remain the authority boundary.

non_goals:
  - >-
    No automatic conversion of draft PRs to ready-for-review, no manual Linear
    status workaround, no relaxation for closed/merged PRs, no automatic merge,
    no Symphony ceiling change and no Gate-1 transition authority.

test_requirements:
  - >-
    Linear-client tests prove draft and open coherent publications are accepted,
    while closed, merged, unknown/malformed statuses and every existing
    contradictory identity case fail closed.
  - >-
    CI-handoff adapter/integration coverage proves a trusted draft publication
    reaches evidence ingestion and normal reconciliation without adding any new
    external write owner.
  - >-
    Existing CI-handoff, evidence, Linear-client, import-linter and relevant
    workflow-contract tests remain green.

implementation_notes:
  - >-
    Keep the fix at the publication-validation boundary. Draft and open are both
    live candidate PR states; do not infer publication identity elsewhere or
    bypass the existing independent GitHub head verification performed before
    the CI Pending exit.
  - >-
    The Gate-1 reproduction showed Linear status=draft is truthful provider
    state, not malformed data. Preserve fail-closed handling for every other
    contradiction.

documentation_requirements: []

definition_of_done:
  - >-
    The Gate-1 draft-publication reproduction is covered by deterministic tests,
    coherent draft/open publication identity is accepted without weakening the
    trust boundary, all selected validation passes, complete CI passes on the
    exact published head, and the PR title carries the minted Atlas ticket key.
---

# Accept coherent draft GitHub publications at the CI-handoff trust boundary

A draft pull request is still a live, exact-head CI publication. Atlas must
validate its identity rigorously without confusing GitHub review readiness with
publication trust.

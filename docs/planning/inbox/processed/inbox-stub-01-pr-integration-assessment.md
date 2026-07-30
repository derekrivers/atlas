---
title: "Exact-head PR mainline integration assessment"
objective: >-
  Give every operator acceptance command one read-only, typed answer to whether
  an open pull request's exact head contains the exact current main commit, so
  stale review evidence and GitHub's approximate mergeability fields cannot be
  mistaken for mainline freshness.
context: >-
  Phase 11 proved the three-agent delivery loop but repeatedly left a trailing
  Review Required PR stale after a sibling merged. ATLAS-168 correctly makes
  agents rebase before PR open, before every push and before Review Required;
  it deliberately sends post-handoff staleness through Changes Requested
  because Atlas has no operator-owned integration lane. Phase 12 replaces only
  that post-handoff fallback. This ticket establishes the shared read-only
  primitive on which both the rebase driver and the acceptance gate depend.
  Pre-ruled decisions: D-1 the GitHub REST compare endpoint is called with the
  exact `base.sha...head.sha` obtained from the same PR snapshot; branch names
  and locally cached remote-tracking refs are not freshness evidence. D-2 the
  result separates ancestry from mergeability: ancestry is one of current,
  behind, diverged or indeterminate; mergeability is mergeable, conflicted or
  indeterminate. D-3 the overall integration status is `current` only for an
  open, non-draft, same-repository PR targeting `main`, with compare
  `behind_by == 0`, merge-base SHA equal to the PR base SHA, and mergeability
  known not-conflicted. GitHub's temporary `mergeable: null` fails closed as
  indeterminate; a missing compare field or contradictory payload is a typed
  boundary error, and neither case can yield current. D-4 the assessment is
  immutable and carries every SHA and ref needed to prove what was assessed;
  downstream code never reparses the raw GitHub payload. D-5 this ticket is
  read-only: it performs no Git, GitHub, Atlas-store or Linear write and does
  not decide whether a rebase may be published.
ticket_type: infrastructure
epic_ref: ATLAS-E10
risk_level: medium
component: atlas.github
tags:
  - github
  - orchestration
  - exact-head
  - mainline
relevant_docs:
  - "docs/atlas/symphony-integration.md"
  - "docs/runbooks/pr-acceptance.md"
  - "docs/decisions/0008-ci-sourced-evidence-with-trust-tiers.md"
depends_on:
  - "ATLAS-168"
acceptance_criteria:
  - >-
    `GitHubClient` and `GitHubRESTClient` expose an exact-SHA compare operation
    over `GET /repos/{owner}/{repo}/compare/{base}...{head}` using the existing
    conditional-request, bounded rate-limit and secret-free typed-error
    contract; malformed or incomplete object responses raise
    `GitHubAPIError`, never `KeyError` or an inferred default.
  - >-
    One immutable integration-assessment type carries repository identity, PR
    number/state/draft flag, head ref/SHA/repository, base ref/SHA/repository,
    merge-base SHA, ahead/behind counts, compare status, mergeability and the
    derived ancestry, eligibility and overall integration statuses.
  - >-
    Eligibility is explicit and fail-closed: only an open, non-draft,
    same-repository PR targeting the literal base ref `main` is eligible;
    closed, merged, draft, fork-head and non-main PRs are returned as named
    ineligible states rather than accepted or allowed to traceback.
  - >-
    Overall `current` is emitted only when the exact head contains the exact PR
    base (`behind_by == 0` and `merge_base_commit.sha == base.sha`) and
    mergeability is known not-conflicted; `mergeable: null`, a typed boundary
    error or contradictory counts can never produce `current`.
  - >-
    Behind, diverged, conflicted and indeterminate outcomes remain distinct in
    both the typed assessment and presentation, while preserving the raw
    ahead/behind counts and SHAs needed to diagnose the result.
  - >-
    `atlas pr status --pr N --repo OWNER/REPO` renders the assessment without
    mutation and supports `--json`; current exits zero, every eligible
    not-current or indeterminate result exits non-zero, and malformed repo,
    unknown PR, missing token and transport failures use Atlas's clean
    one-line precondition/error contract with no traceback or secret.
  - >-
    Deterministic tests cover current/ahead, behind, diverged, conflicted,
    mergeability-null, fork, draft, non-main, missing-field and API-error
    fixtures, and prove the compare call uses the two exact SHAs from the PR
    snapshot rather than branch names.
non_goals:
  - >-
    No branch fetch, clone, worktree, rebase, push, GitHub write, Linear read or
    write, Atlas-store write, CI polling, evidence pull, confirmation,
    verification, merge or Operator UI/API route.
test_requirements:
  - >-
    Extend the existing stubbed-urllib GitHub client tests and add
    orchestration/CLI tests with injected fakes; no live GitHub calls and
    `ATLAS_LIVE_TESTS=0`.
  - >-
    Existing evidence, verification and PR-context tests pass unmodified apart
    from the minimum fake-protocol additions required by the new read method;
    seeded Python defects use `assert 1 == 2` (B011).
implementation_notes:
  - >-
    Keep transport parsing in `atlas.github`, derivation in a small
    orchestration service, and formatting/exit-code policy in `atlas.cli`; the
    CLI must not duplicate the classifier used by later tickets.
  - >-
    Compare exact 40-hex SHAs from one fetched PR object. Do not use
    `mergeable_state`, a local `origin/main`, or a branch-name comparison as a
    substitute for ancestry.
  - >-
    Model ancestry and mergeability as separate enums or equivalent typed
    fields so a conflicted PR can still report its exact ahead/behind relation
    without collapsing diagnostic information.
documentation_requirements:
  - >-
    Document the exact-head definition, state vocabulary and read-only CLI in
    `symphony-integration.md` and cross-reference it from the acceptance
    runbook without yet changing the binding acceptance sequence.
definition_of_done:
  - >-
    All seven acceptance criteria are evidenced by named tests; the full
    Python gate sweep and doc linter are green; canonical documentation lands
    in the same change; the PR title carries the minted ticket key.
---

# Exact-head PR mainline integration assessment

One shared classifier decides whether a PR is genuinely current with `main`.

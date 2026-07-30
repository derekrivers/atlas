---
title: "Operator-owned lease-guarded PR rebase lane"
objective: >-
  Let the operator rebase a mechanically stale Review Required pull request
  onto current main in a durable isolated worktree, resolve any conflicts
  deliberately, and publish the rewritten head only when both the PR branch
  and main still equal the SHAs originally assessed.
context: >-
  The Phase 11 run showed that implementation is no longer the main
  bottleneck: sibling merges repeatedly made correct PRs stale and sent them
  through a full Symphony Changes Requested cycle for a mechanical rebase.
  ATLAS-168 remains binding before Review Required; this lane starts only
  after handoff and is operator-owned. Pre-ruled decisions: D-1 the command
  surface is `atlas pr rebase prepare --pr N --repo OWNER/REPO`, followed when
  needed by `continue --workspace PATH`, `publish --workspace PATH` or
  `abort --workspace PATH`. D-2 work occurs in a detached linked worktree
  beneath the gitignored `.atlas/rebase-workspaces/` root; the operator's
  current branch, index and working tree are never changed. D-3 the manifest
  pins repository identity, PR number, branch ref, original head SHA, base ref
  and SHA, merge-base SHA and state transitions. D-4 conflicts are never
  resolved automatically: the stopped rebase and exact unmerged paths remain
  in the worktree for the operator; `continue` only proceeds after the index
  has no unresolved entries and may stop again on a later conflict. D-5
  publication refetches the PR and remote refs, rejects any head or base
  movement, and uses only the explicit expected-value lease
  `--force-with-lease=refs/heads/<branch>:<original-head-sha>`; bare
  `--force`, implicit `--force-with-lease` and GitHub Update branch are
  forbidden. D-6 a successful publish is verified back through the shared
  exact-head assessment, writes a durable receipt, and does not change Linear
  or Atlas ticket status. D-7 fork PRs and automatic conflict resolution are
  deferred.
ticket_type: infrastructure
epic_ref: ATLAS-E10
risk_level: high
component: orchestration
tags:
  - orchestration
  - git
  - rebase
  - operator
  - mainline
relevant_docs:
  - "docs/atlas/symphony-integration.md"
  - "docs/runbooks/pr-acceptance.md"
  - "docs/runbooks/operator-environment.md"
depends_on:
  - "inbox-stub-01-pr-integration-assessment.md"
acceptance_criteria:
  - >-
    `prepare` refuses before local Git mutation unless the shared assessment
    reports an open, non-draft, same-repository PR targeting `main` whose
    determinate state requires integration (behind, diverged or conflicted),
    the PR title/body resolves to at least one existing Atlas ticket, and every
    ticket in that close-set is currently `review_required`; already-current
    and indeterminate PRs are named no-op/refusal outcomes, and no Linear call
    or status transition occurs.
  - >-
    `prepare` creates a detached linked worktree only beneath
    `.atlas/rebase-workspaces/` at the assessed original head and writes a
    versioned manifest pinning repo slug/root, PR number, head ref/SHA, base
    ref/SHA, merge-base SHA, workspace path and lifecycle state; the operator's
    primary checkout branch, HEAD, index, tracked files and local branch refs
    are unchanged.
  - >-
    A clean `git rebase <pinned-base-sha>` reaches a publishable state; a
    conflict exits non-zero without remote mutation, records and prints the
    exact `git diff --name-only --diff-filter=U` paths, and preserves the
    stopped rebase. `continue` refuses while unresolved index entries remain,
    continues non-interactively when they are staged, and records every
    conflict set if a later commit stops again.
  - >-
    Before publishing, the driver refetches the live PR, exact current base and
    remote head and requires them to equal the manifest's original head and
    pinned base. A moved PR head, moved main, closed/drafted PR, changed branch
    or repository identity, missing remote ref or incomplete live snapshot
    aborts before push and leaves the worktree recoverable. The old PR's
    pre-push mergeability is diagnostic only—the pinned identities and SHAs,
    not a fresh mergeability guess, govern the lease gate.
  - >-
    The only remote update is an argv-based Git invocation equivalent to
    `git push --force-with-lease=refs/heads/<branch>:<original-head-sha> origin
    <rebased-head-sha>:refs/heads/<branch>`; tests inspect the exact argv and
    prove lease rejection and a last-moment remote-head race cause zero remote
    ref change.
  - >-
    Immediately after a successful push the manifest atomically records a
    `push_succeeded_unverified` state. Bounded refetching then confirms GitHub
    reports the exact rebased head and the shared assessment reports it current
    with exact current main; rerunning `publish` from that state re-verifies and
    never repeats the old-head lease push. Only after confirmation is a receipt
    persisted beneath `.atlas/rebase-receipts/` with old head, pinned base,
    merge base, new head, branch, conflict paths and timestamps and the managed
    linked worktree removed; every ticket remains `review_required`.
  - >-
    `abort` operates only on a canonical path contained beneath the configured
    rebase-workspace root with a matching Atlas manifest, aborts an in-progress
    rebase when present, and removes that named linked worktree through Git;
    traversal, symlink escape, missing/mismatched manifests, the primary
    worktree, any manifest recording a successful push and already-published
    receipts are refused without deletion.
non_goals:
  - >-
    No automatic conflict resolution, commit-content judgement, fork PR
    support, merge commit or GitHub Update branch, CI polling, evidence pull,
    acceptance confirmation, verification, PR merge, Linear mutation, Atlas
    ticket transition, Symphony dispatch/resume, Operator UI control, primary
    checkout mutation, bare `--force` or implicit lease.
test_requirements:
  - >-
    Integration tests use temporary repositories and local bare remotes to
    cover clean rebase, one and multiple conflict stops, continue, abort,
    branch-head race, main race, exact lease rejection, publish confirmation
    and receipt/worktree cleanup; GitHub and ticket-store reads use injected
    fakes and `ATLAS_LIVE_TESTS=0`.
  - >-
    Safety tests snapshot the primary checkout's branch, HEAD, index, status
    and local refs before and after every success/failure path and exercise
    path traversal, symlink escape and foreign-manifest rejection; seeded
    Python defects use `assert 1 == 2` (B011).
implementation_notes:
  - >-
    Use a detached worktree at the original head; never check out the PR branch
    in the primary worktree and never create/reset a local branch merely to
    rebase it. All Git commands use explicit argv with `shell=False`.
  - >-
    Treat the manifest as a small state machine such as prepared,
    conflicts_pending, ready_to_publish, push_succeeded_unverified and
    published. Write manifest and receipt updates atomically so interruption
    cannot make an unknown workspace look publishable or repeat a completed
    remote update.
  - >-
    Reuse `parse_close_set` and the exact-head assessment service. Validate the
    GitHub head ref as a normal `refs/heads/` branch in the same repository
    before constructing the refspec; never interpolate unchecked refs into a
    shell command.
  - >-
    Publication is the sole remote-write boundary. Perform all deterministic
    local and GitHub preconditions before it, then rely on the explicit
    expected-SHA lease as the final atomic race guard.
documentation_requirements:
  - >-
    Document command-by-command recovery, conflict ownership, manifest/receipt
    locations, exact lease semantics, exit states and the no-Linear/no-merge
    boundary in `symphony-integration.md`, `pr-acceptance.md` and
    `operator-environment.md`.
definition_of_done:
  - >-
    All seven acceptance criteria are evidenced by named deterministic tests;
    the full Python gate sweep and doc linter are green; canonical docs land in
    the same change; the PR title carries the minted ticket key.
---

# Operator-owned lease-guarded PR rebase lane

Mechanical post-handoff integration becomes an operator action, not a new
Symphony implementation cycle.

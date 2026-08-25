---
name: atlas-pr-acceptance
description: |
  Guide the governed operator acceptance and manual-merge sequence for an Atlas
  PR in Review Required. Use when Codex must identify the currently valid
  acceptance step, enforce exact-head freshness, pull evidence, confirm, verify,
  route mechanical staleness, record merged proof, and observe managed
  completion without assuming operator authority.
---

# Accept an Atlas PR

Read the current canonical authority and follow it. This skill owns procedure
and navigation, not policy. If this skill conflicts with canonical repository
authority, the repository authority wins and this skill is defective.

## Authorities and composition

Read `docs/runbooks/pr-acceptance.md` in full at execution time. Read the
exact-head and rebase contracts in `docs/atlas/symphony-integration.md` and the
operator authority rule in `docs/decisions/0009-single-operator-governance.md`.

Load and follow the `atlas-pr-review` skill for the review step. Its verdict is
reviewer input, not operator approval or merge authority.

## Establish the valid step

Require the exact repository, PR, ticket close set, current contributor head,
current live `main`, ticket state, and acceptance-criteria identity. The ticket
must be `Review Required` through the Atlas-owned CI handoff, not merely because
GitHub looks green.

Use `uv run atlas pr status --pr <N> --repo <owner>/<repo>` for the canonical
read-only exact-head assessment. If the eligible PR is mechanically behind,
diverged, or conflicted before evidence, route it through the exact
`atlas pr rebase prepare`, `continue`, and `publish` lane in the runbook. Do not
send mechanical staleness through semantic remediation.

Any indeterminate identity, moved head, moved `main`, changed close set, changed
criteria, wrong ticket state, or ineligible PR fails closed at the step where it
is observed.

## Follow the acceptance spine

Execute only the next step permitted by the runbook:

1. Review through `atlas-pr-review` and pin its recommendation to the exact head.
2. Prove the contributor head contains current `main`, then freeze the head.
3. Run `uv run atlas evidence pull --pr <N> --repo <owner>/<repo>`.
4. Let the operator run `uv run atlas confirm --pr <N> --repo <owner>/<repo>
   --operator <id>` for the current criteria.
5. Run `uv run atlas verify --pr <N> --repo <owner>/<repo>` and require an
   explicit `PASSED` verdict with a valid exact `head_commit`; exit status alone
   is not authority.
6. Repeat the live exact-head/current-main assessment and require it to match
   the frozen and verified identities.
7. Preserve the one-PR freeze while the operator manually merges the verdict
   commit in GitHub. This skill never performs or infers operator approval.
8. After GitHub reports merged, run `atlas verify` again to record merged proof.
9. Observe the Atlas store read-only until the managed PM owner reports the
   close set Done, or report the bounded timeout without compensating writes.

Any head movement after evidence restarts from evidence for the new exact head.
Never substitute a GitHub rollup, local validation, browser status, stale
verdict, or cached readiness for Atlas's system-tier evidence and verifier.

Acceptance does not deploy, migrate, invoke PM sync, mutate Linear directly,
operate Symphony, or drag a ticket to Done.

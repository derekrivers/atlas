---
name: atlas-pr-review
description: |
  Perform an Atlas-standard review of one published PR candidate or one frozen
  pre-publication maintenance candidate. Use when Codex must verify the exact
  branch diff against its governing work contract and documents, run
  repository-selected validation and seeded-defect probes, inspect pins and
  guards, and issue a reviewer-tier verdict without accepting or merging.
---

# Review an Atlas PR

Read the current canonical authority and follow it. This skill owns procedure
and navigation, not policy. If this skill conflicts with canonical repository
authority, the repository authority wins and this skill is defective.

## Authorities and composition

Read `docs/runbooks/review-doctrine.md`, `docs/runbooks/reviewer-session.md`,
and `docs/runbooks/operational-practice.md` at execution time. Read
`docs/decisions/0008-ci-sourced-evidence-with-trust-tiers.md` for evidence
authority.

Load and follow the `atlas-validation` skill for candidate validation. Do not
copy or independently select validation commands here.

## Establish the candidate

1. Review a published PR from a fresh checkout of its head. For a frozen
   pre-publication maintenance candidate, use a disposable isolated checkout of
   the exact frozen head. Never review from an agent report, cached attachment,
   or the primary writer's mutable checkout.
2. Record the exact repository, PR number when one exists, base SHA,
   contributor head SHA, branch identities, and current `main` identity.
3. Enumerate the complete base-to-head changed-path identity set, including
   both paths for renames and copies.
4. Resolve the exact canonical Atlas ticket or non-key maintenance-unit
   contract and current acceptance criteria from its authoritative source.
   Read the ticket's Context Pack when one exists and every governing canonical
   document. A maintenance meta-label grants no Linear lifecycle authority.
5. Confirm the diff matches the approved work scope and contains no
   undeclared dependency, migration, manifest, protected-surface, or authority
   change.

## Verify the work

- Load and follow `atlas-validation` for the exact candidate, ticket
  requirements, and ticket-declared tests.
- Inspect implementation behavior against every acceptance criterion,
  non-goal, governing contract, and recorded operator ruling.
- Perform the seeded-defect probes required by `reviewer-session.md` only in a
  disposable isolated reviewer checkout: make the smallest realistic temporary
  defect, prove the relevant guard fails, restore the exact candidate, and
  record both failing and restored passing results. A read-only reviewer must
  instead report the required but unperformed probe; it must never alter the
  primary writer's checkout or fabricate evidence.
- Inspect guard-shaped deliverables against their ruled matching boundary.
- Check relevant repository pins and guards, including the acceptance-ticket
  and schema-export enumeration pins unless their change was approved.
- Label extra checks diagnostic. Never leave probe edits in the candidate.

Reviewer-local results are reviewer-tier confidence. Require complete CI at the
published exact head for system-tier authority; GitHub green status alone is not
an Atlas acceptance verdict.

## Verdict

Use the exact verdict forms in `review-doctrine.md`. Report:

- exact base/head and changed paths;
- ticket or maintenance-unit and canonical authorities reviewed;
- findings with precise path/line evidence and severity;
- acceptance-criterion evidence, validation-plan results, seeded probes, pins,
  guards, and any diagnostic-only checks;
- unverified or unavailable evidence; and
- one repository-standard recommendation.

A reviewer recommends. Only the operator approves, confirms, accepts, or merges.

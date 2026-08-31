---
name: atlas-validation
description: |
  Determine and execute Atlas's repository-owned validation scope for one exact
  base/head candidate. Use when Codex must validate an implementation, review or
  remediation candidate, reproduce its deterministic validation plan, or report
  exact local checks without confusing them with system-tier CI authority.
---

# Validate an Atlas candidate

Read the current canonical authority and follow it. This skill owns procedure
and navigation, not policy. If this skill conflicts with canonical repository
authority, the repository authority wins and this skill is defective.

## Authorities

Read `AGENTS.md`, `docs/runbooks/local-development.md`, and the applicable
publication procedure: `docs/runbooks/symphony-agent-execution.md` for a
canonical ticket, or `docs/runbooks/operational-practice.md` and
`docs/runbooks/agent-ticket-prompt.md` for hand-dispatched maintenance. Read
`docs/decisions/0008-ci-sourced-evidence-with-trust-tiers.md` for evidence
authority.

The deterministic selector is `uv run atlas validation-plan`. The repository
registry and CLI own selection logic; never reproduce that logic in this skill,
shell filters, or model judgment.

## Freeze the inputs

1. Establish the exact full base and head Git object IDs required by the owning
   workflow. Do not validate an unfrozen working tree as a publication candidate.
2. Enumerate the complete base-to-head changed-path identity set using the
   read-only, NUL-delimited Git command specified by the runbook. Include both
   old and new paths for renames and copies.
3. Collect every explicit validation requirement and declared test file from
   the governing ticket or maintenance-unit contract. Supply them through the
   CLI's `--ticket-requirement` and `--ticket-test` flags; those stable flag
   names do not create a ticket identity.
4. Invoke `uv run atlas validation-plan` with the exact base, head, every
   `--changed-path`, every `--ticket-requirement`, and every `--ticket-test`.
   Use `--json` when a typed record is useful.
5. Require the CLI's diff and explicit-test proofs to succeed. Treat every
   selected profile, ordered command, test target, and fallback reason as
   authoritative for this candidate.

## Execute the plan

- Execute the exact plan with `uv run atlas validation-run` using the same
  base/head, changed paths, ticket requirements and ticket tests supplied to
  `validation-plan`. The repository, not the agent, determines execution
  groups.
- Every selected command remains mandatory. Commands within a repository-owned
  group run in their defined order; independent repository-owned groups may run
  concurrently. The agent must not add, remove, merge, split or otherwise alter
  those groups.
- Run every explicit test target in emitted order, even when a broader command
  already contains it.
- Never omit, replace, or narrow a selected command.
- Run `full-sweep` only when the plan selects it or the operator explicitly
  requires it. Do not add an unselected full sweep as ritual.
- Label extra targeted checks as diagnostic. They neither replace selected
  checks nor widen the evidence tier.

A selected-command or explicit-test failure prevents publication. If a fix
changes the head, discard the old plan as historical and calculate a new plan
for the new exact candidate.

## Report the evidence tier

Record the exact base/head, complete changed paths, registry identity when
reported, governing work-contract inputs, selected profiles, ordered commands,
explicit test targets, fallback reasons, and each result.

Implementation-agent results are agent-tier confidence. Reviewer-local results
are reviewer-tier confidence. Complete CI at the accepted exact identity remains
system-tier authority and runs unchanged regardless of local plan width.

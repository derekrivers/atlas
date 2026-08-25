---
name: atlas-investigate
description: |
  Establish fresh, evidence-backed Atlas repository and operational ground
  truth before architectural, programme, incident, milestone, or current-state
  claims. Use when Codex must investigate Atlas identities, reconcile conflicting
  observations, diagnose a workflow boundary, or verify repository, ticket, PR,
  database, CI, or Symphony runtime state.
---

# Investigate Atlas

Read the current canonical authority and follow it. This skill owns procedure
and navigation, not policy. If this skill conflicts with canonical repository
authority, the repository authority wins and this skill is defective.

## Authorities

Read, in order:

1. `AGENTS.md`.
2. `docs/MANIFEST.md` to resolve the current canonical document set.
3. `docs/runbooks/operational-practice.md` for the authority map and
   identity-first method.
4. `docs/runbooks/reviewer-session.md` when making repository or Symphony
   claims.
5. The specialist design or runbook that owns the behavior under investigation.

Use `docs/runbooks/troubleshooting.md` for symptom-driven diagnosis and
`docs/runbooks/operator-environment.md` for environment, credential, database,
and runtime facts. Do not treat either as authority outside its stated scope.

## Establish identities

1. Record the repository root, origin URL, symbolic branch, exact `HEAD`, and
   working-tree status using the commands in `operational-practice.md`.
2. When freshness against `main` matters, fetch `origin/main` and record its
   exact SHA before reasoning about ancestry or staleness.
3. Resolve the exact Atlas ticket key, Linear issue identifier, PR repository
   and number, base and contributor head, and relevant CI run/check identities.
4. Prove the database actually queried, including any `ATLAS_DATABASE_URL` or
   `--db` override. Never compare results from stores whose identities differ.
5. For runtime claims, record the process/service, workflow, policy, and
   observed runtime identities required by the owning runbook.

Do not infer any of these identities from a prior conversation, completion
report, cached output, branch name, ticket title prose, or remembered path.

## Gather bounded evidence

- Read canonical documents for intent and ownership.
- Read the Atlas store and its typed command output for operational state.
- Read Linear, GitHub, CI, and Symphony only for the claims those systems own.
- Inspect the current upstream Symphony repository separately when upstream
  Symphony behavior is material; Atlas documents do not prove upstream code.
- Prefer bounded machine-readable output. Preserve exact SHAs, identifiers,
  timestamps, and evidence-tier fields.
- Keep the investigation read-only until the owning authority permits a named
  mutation.

If two observations use different identities, report them as incomparable
rather than contradictory. If canonical documents genuinely conflict after
applying `docs/MANIFEST.md`, stop and name the conflict.

## Report

Return a concise result containing:

- the question and investigated scope;
- exact repository, ticket, PR, database, CI, and runtime identities that
  matter;
- observed facts, each tied to its source or command;
- inferences, labeled explicitly and separated from observations;
- unknown, stale, conflicting, or unavailable evidence; and
- the next action permitted by the owning authority, if any.

Do not convert an inference into a lifecycle, acceptance, deployment, or
operator decision.

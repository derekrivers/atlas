# ADR-0008: CI-sourced evidence with trust tiers

## Status

Accepted

## Context

The evidence schema currently allows any actor, including an agent, to
submit an Evidence record with `status: PASSED`. An agent-authored evidence
record is still a claim, which contradicts the core rule "no evidence = no
completion". A trust model is required, and a transport for CI results must
be chosen. The decision is to source check results from CI (GitHub Actions)
rather than have Atlas execute checks inside agent workspaces.

## Decision

**Trust tiers.** Evidence status authority depends on the creating actor:

- `system` (CI ingestion, Atlas-executed commands): may set any status.
- `human` (the operator): may set any status; used for `MANUAL_APPROVAL`.
- `agent`: capped at `PENDING`. Agent-submitted evidence becomes `PASSED`
  only when corroborated by a system-tier record for the same check and
  commit, or by explicit human approval.

**CI as the system-tier producer.** GitHub Actions runs tests, lint, and
coverage. Results are ingested as normalised Evidence records carrying:

- `commit_sha` (required) — the exact code state attested;
- `external_run_id` (workflow run / check run ID);
- `payload_hash` — SHA-256 of the raw payload at ingestion;
- the existing `evidence_type`, `status`, `summary`, `raw_payload`.

**Evidence is append-only.** Records are never updated or deleted; a new
result for the same check and commit is a new record. Verification reads the
latest system-tier record per (check, commit).

**Transport: pull first, push later.** Receiving webhooks requires a public
endpoint, which a local single-operator MVP does not have. The MVP therefore
polls the GitHub Checks/Workflow Runs API on a schedule and normalises
results into the same Evidence payload a webhook would produce. When Atlas
is hosted, an HMAC-verified webhook receiver replaces polling with no schema
change. The ingestion module is written transport-agnostic from day one.

## Rationale

This mirrors the harness-engineering arrangement: agents drive the work, but
completion signals come from the environment (CI), not from agent
self-report. Pinning evidence to `commit_sha` makes "auditable" literal —
every verification decision can be replayed against the exact code state.

## Consequences

- Evidence model gains `commit_sha`, `external_run_id`, `payload_hash`;
  `created_by_type` determines the trust tier.
- The Verification Engine requires system-tier evidence for `TESTS`,
  `LINT`, and `BUILD` check types; agent claims alone can never satisfy them.
- Phase 6 ticket scope changes: "evidence ingestion" means the GitHub
  polling client plus the normaliser, not per-tool parsers inside agent
  workspaces.
- A repository must have CI configured before any ticket in it can be
  verified — CI setup moves to Phase 0.

## Alternatives considered

- Atlas executes checks in agent workspaces (Symphony-hook style):
  rejected for now; duplicates CI, and workspace results are not tied to a
  pushed commit. May be added later as a faster pre-CI signal.
- Webhooks from day one: rejected; requires hosting before the core loop is
  proven.
- Trusting agent-submitted results: rejected; circular by definition.

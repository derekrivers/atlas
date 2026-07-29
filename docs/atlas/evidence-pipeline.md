# Evidence Pipeline Design (Phase 6)

Status: Active design document for Phase 6. Implements ADR-0008; the
Evidence schema lives in `data-model-and-schemas.md` §3.7.

## Poller

Pull-based GitHub client (transport-agnostic normaliser so a webhook
receiver can replace it without schema change — ADR-0008):

- Every tick (default 120s): for each ticket in `pr_open`,
  `review_required`, or `changes_requested` with a linked PR, fetch
  workflow runs and check runs for the PR head SHA, and PR reviews.
- Conditional requests (ETags) to stay inside rate limits; backoff on
  secondary-rate-limit responses.
- Dedup key: `(external_run_id, payload_hash)` — re-polling an unchanged
  run creates nothing; a re-run of the same workflow creates a new
  append-only record.
- List endpoints follow every GitHub `Link: rel="next"` page. Conditional
  requests replay cached state on `304 Not Modified`; repository dedup, not an
  empty-response sentinel, suppresses unchanged evidence.

## Job-name convention (CI contract)

Evidence typing is driven by CI job names, which makes the mapping a
repo-owned contract rather than parser heuristics. Jobs in
`.github/workflows/` must be named with a recognised prefix:

| Job name prefix | EvidenceType        |
| --------------- | ------------------- |
| `test`          | TEST_RESULT         |
| `lint`          | LINT_RESULT         |
| `build`         | BUILD_RESULT        |
| `coverage`      | COVERAGE_REPORT     |

Unrecognised jobs are ingested as BUILD_RESULT with a warning so nothing
is silently dropped. The doc linter checks workflow job names against this
table.

Each CI record also stores the exact `job_name` and GitHub lifecycle
`source_event_at` (`updated_at` for workflows, `completed_at`/`started_at` for
checks). Append-only observations sharing the same non-null `external_run_id`
are lifecycle snapshots of one execution: an ordered later snapshot supersedes
an unordered earlier snapshot only within that execution. An execution with no
ordered snapshot remains unorderable, and unrelated executions — including
records without an `external_run_id` — are never collapsed. Verification then
resolves the current execution independently per job by GitHub lifecycle time;
UUIDs, payload hashes, and Atlas ingest timestamps never determine CI recency.

## Status normalisation

| GitHub conclusion            | EvidenceStatus |
| ---------------------------- | -------------- |
| success                      | PASSED         |
| failure                      | FAILED         |
| cancelled, stale             | WARNING        |
| timed_out                    | FAILED         |
| skipped, neutral             | NOT_APPLICABLE |
| (run still in progress)      | PENDING        |

PR reviews: `APPROVED → PASSED`, `CHANGES_REQUESTED → FAILED`,
`COMMENTED → WARNING`, all as PR_REVIEW evidence with the reviewer
recorded in `raw_payload`.

Documentation evidence: Atlas inspects the PR file list; changes under
`docs/` produce a DOCUMENTATION_UPDATE record (PASSED) listing the touched
paths. Tickets with documentation requirements and no such record will
fail verification (Phase 7) — the evidence layer only records.

## Tier and pinning enforcement

All poller-created records are `created_by_type: system` with
`commit_sha` (PR head), `external_run_id`, and `payload_hash` mandatory —
ingestion rejects records missing any of them. The repository guard is
blanket on the system tier: `EvidenceRepo.add` rejects *any* system-tier
record missing any of the three regardless of `evidence_type` — there is no
CI-evidence-type allowlist. Agent-submitted records remain capped at PENDING
by the knowledge-core repository rule.

## Retention

`raw_payload` is capped at 64KB; a larger payload is replaced by a compact
marker containing its original byte count, full payload hash, and `source_uri`
for retrieval from GitHub. Evidence rows are never deleted; a retention review
is a Phase 10+ concern.

## CLI

`atlas evidence pull [--ticket KEY]` (manual tick),
`atlas evidence list <KEY> [--type T]`, `atlas evidence show <ID>`.

## Open items

- Coverage thresholds: ingestion records the number; whether a minimum is
  *required* is a Verification Engine rule, not an evidence rule.
- Multi-repo support (future products may live in the same monorepo per
  ADR-0003; if that changes, the poller gains a repo dimension).

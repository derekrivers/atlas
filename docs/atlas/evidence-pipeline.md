# Evidence Pipeline Design (Phase 6)

Status: Active design document for Phase 6. Implements ADR-0008; the
Evidence schema lives in `data-model-and-schemas.md` §3.7.

## Poller

Pull-based GitHub client (transport-agnostic normaliser so a webhook
receiver can replace it without schema change — ADR-0008):

- Every tick (default 120s): for each ticket in `pr_open`, `ci_pending`,
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
paths. New observations persist that exact projection in nullable
`Evidence.docs_paths`, independently of `raw_payload`, as a sorted unique tuple
of 1–256 canonical repository-relative `docs/` paths whose individual lengths
do not exceed 240 characters. Invalid, empty, duplicate, unsorted, non-`docs/`,
absolute, traversal, backslash or control-character paths are rejected rather
than truncated or normalised into guessed coverage.

The structured source identity is `docs:v2:<head_sha>`. Its version separates a
fresh observation from the legacy `docs:<head_sha>` identity, so an unchanged
head whose old row was already capped can append one recovery record. The
unchanged `(external_run_id, payload_hash)` dedup contract then makes repeated
identical v2 pulls idempotent. `payload_hash` remains the hash of the canonical
full upstream documentation subset before retention; commit, source, product
and system-actor pins are unchanged. Historical rows are never backfilled or
rewritten. Tickets with documentation requirements and no proving record fail
verification (Phase 7) — the evidence layer only records.

## Tier and pinning enforcement

All poller-created records are `created_by_type: system` with
`commit_sha` (PR head), `external_run_id`, and `payload_hash` mandatory —
ingestion rejects records missing any of them. The repository guard is
blanket on the system tier: `EvidenceRepo.add` rejects *any* system-tier
record missing any of the three regardless of `evidence_type` — there is no
CI-evidence-type allowlist. Agent-submitted records remain capped at PENDING
by the knowledge-core repository rule.

## CI-pending handoff consumption

The system-tier CI handoff reconciler consumes these append-only observations;
it never calls a CI execution or derives authority from a GitHub rollup, a
command exit code or an agent completion message. The production adapter first
binds the ticket's stable Linear issue id to one validated GitHub attachment,
then invokes `drive_evidence_pull` for that exact repository/PR. `PullResult`
continues to expose newly persisted per-source lists for CLI counts and also
carries the resolved head plus every exact observed row, including unchanged
rows reused by `(external_run_id, payload_hash)` dedup. This gives the handoff a
bounded publication-to-evidence attribution without changing normal evidence
records from product scope to ticket scope.

For that repository, PR, full lowercase head SHA and observed evidence-id set,
the reconciler derives the immutable external source identities and reloads
their append-only lifecycle observations before each assessment. A newer
payload for an observed source therefore changes the deciding evidence ids and
holds the write; unrelated product evidence is not admitted merely because it
shares the head. The reconciler resolves the repository-owned required-check
matrix and evaluates only its system-tier checks: tests, lint and, when the
matrix requires it, documentation. Every required member must be represented
by current-head system evidence before the set can pass. The pure classifier
repeats the product guard: a ticket-scoped record participates only for its
exact `ticket_id`. Evidence from another product, another pull observation or
an explicitly different ticket cannot satisfy or fail the handoff even when it
shares the same commit SHA.

The projection distinguishes `passed`, `implementation_failure`, `pending`,
`missing`, `infrastructure`, `stale`, `malformed` and `indeterminate`. Only a
complete set of passed checks can route to human review. An implementation
failure is actionable only when the complete determinate set contains a
current-head `failure` conclusion; a partial failure plus any missing or
uncertain member remains held. Cancelled/timed-out provider work, old-head
records, unordered source metadata, tied contradictory observations and
unknown conclusions never become an implementation verdict. Evidence remains
commit-pinned history after a new push. Immediately before the write fence, the
reconciler reloads the product projection and repeats the assessment. A changed
classification, check result or deciding evidence-id set records
`evidence_changed`, performs no Linear mutation and leaves a later fresh tick to
classify the new authoritative set.

## Exact-head acceptance-session composition

The Phase 14 acceptance evidence action is an application-layer consumer of
this pipeline, not another ingestion path. Given only a durable session ID and
authenticated operator command context, it resolves repository, PR, close-set,
product and pinned head from canonical state and calls `drive_evidence_pull`
in-process with the injected `GitHubClient` and `EvidenceRepo`. It never spawns
`atlas evidence pull`, parses CLI output or duplicates normalisation.

The shared REST boundary validates source shape before mapping: envelope
endpoints must return an object containing their required list field, while
bare-array endpoints must return a top-level list. Malformed, cyclic or
off-origin pagination is a malformed-source failure, never an empty successful
pull or a transport failure. The canonical pull also requires the returned PR
number, base repository and full 40-character head to match the exact request
before any evidence source is normalised or persisted.

The action runs the shared acceptance-session freshness comparator immediately
before and after the bounded pull. A stale or indeterminate pre-check performs
no evidence request. Post-pull movement leaves already appended evidence at its
exact commit as immutable history and stales the session; it cannot promote the
moved session or make evidence at a different head authoritative.

After a fresh post-check, `EvidenceRepo.list_for_product_commit` re-reads only
the product/head projection. Reviews remain pinned to the commit each reviewer
attested, so a pull can append reviews for older heads as canonical history;
those records are valid but cannot enter the session's exact-head projection.
Every newly returned current-head record must be present in that canonical
projection. The session retains a bounded aggregate: total and exact-head-new
source counts, trust-tier counts, status counts, complete/exact-head pin counts
and booleans, and oldest/latest GitHub source-event timestamps. Canonical
evidence remains here, including raw payloads subject to the retention cap;
session state and operator receipts contain no evidence IDs, summaries, source
URIs, payloads, job logs, credentials or foreign errors. Existing
`(external_run_id, payload_hash)` dedup means an unchanged source can advance a
fresh session once with `new_count: 0` and the existing exact-head aggregate.

The governed action distinguishes transport, authentication, exhausted
rate-limit and malformed-source outcomes. These append a typed, secret-free
receipt but do not advance the session. Same-key replay never re-enters this
pipeline; after freshness is assessed again, a new key may retry. The session
transition and receipt commit atomically, while evidence already committed by
the append-only pull is never deleted or rewritten if receipt storage fails.

## Synthetic-candidate attestation assessment

ATLAS-259's provider-native assessment is **FAIL**: the exact synthetic
candidate was observable, but the repository's required successful Check Runs
were attached to the contributor head and the candidate had none. Contributor
head results therefore cannot be relabelled as candidate evidence.

ATLAS-260's governed system-tier attestation assessment is **FAIL**. Its
deterministic harness proves only that a proposed evaluator fails closed when
given a bounded, independently verified attestation. The harness itself creates
the claimed candidate mapping and marks its provenance as fixture simulation;
it does not exercise a trusted producer/signer lifecycle, GitHub OIDC/Sigstore
verification, or an independently observed candidate-to-required-job binding.
The missing identity edge from ATLAS-259 therefore remains missing.

This pipeline does not ingest or trust candidate attestations. The current
no-rewrite approach is retired: no candidate normaliser, storage contract or
verification authority may proceed from ATLAS-260. A later phase may reconsider
the question only with a materially different governed trust mechanism that
produces bounded, cryptographically and provider-verifiable evidence outside
contributor control. Credentials, raw provider envelopes, arbitrary workflow
payloads and logs remain excluded. ATLAS-260 changes no current-head Evidence
record, handoff projection or acceptance-session authority; the exact-head/
operator-rebase path remains authoritative.

## Retention

`raw_payload` is capped at 64KB; a larger payload is replaced by a compact
marker containing its original byte count, full payload hash, and `source_uri`
for retrieval from GitHub. The cap does not alter the separately bounded
`docs_paths` projection, so a large documentation payload can retain exact path
coverage without retaining patch bodies. A legacy small DOCUMENTATION_UPDATE
may still expose paths through a valid retained `raw_payload["files"]`; a
legacy capped row with neither form remains unprovable. Evidence rows are never
deleted or rewritten; a retention review is a Phase 10+ concern.

## CLI

`atlas evidence pull [--ticket KEY]` (manual tick),
`atlas evidence list <KEY> [--type T]`, `atlas evidence show <ID>`.

## Open items

- Coverage thresholds: ingestion records the number; whether a minimum is
  *required* is a Verification Engine rule, not an evidence rule.
- Multi-repo support (future products may live in the same monorepo per
  ADR-0003; if that changes, the poller gains a repo dimension).

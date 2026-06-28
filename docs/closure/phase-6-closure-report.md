# Phase 6 Closure Report — Evidence System

Status: **CLOSED** as of 2026-06-28. The Evidence System Epic (ATLAS-61
through ATLAS-70, plus the live-discovered ATLAS-130) is complete: the
evidence schema with trust-tiered, append-only, commit-pinned storage; the
GitHub polling transport with transport-agnostic normalisers; all three
ingest sources (CI checks, PR reviews, documentation); the `atlas evidence`
CLI; the 64KB retention cap; and clean cold-database handling. The pipeline
has been **run end-to-end against a real, unrelated repository and
externally validated against ground truth** (see §3).

This report closes Phase 6 on the evidence machinery being **built and
proven on real data**. The one part of ATLAS-70's spirit that is not here —
system-tier evidence *corroborating* an agent's PENDING claim at read time —
is, by the design doc's own framing, Phase 7 (Verification Engine) work, not
storage; it is carried forward in §4. The reasoning for closing here is in §5.

Design doc: `docs/atlas/evidence-pipeline.md` (implements ADR-0008).

---

## 1. Milestone evidence

The Phase 6 milestone is met when a CI run on a fixture PR is ingested as
commit-pinned system-tier evidence, and an agent-submitted PASSED record is
stored as PENDING (in practice: rejected, the stricter reading). Status by
claim:

| Claim | Asserted by | Status |
| --- | --- | --- |
| A CI run is ingested as **commit-pinned system-tier** evidence | `map_check_to_evidence` + `ingest_checks` produce SYSTEM-tier records carrying the pin triple; `EvidenceRepo.add` refuses any system-tier record missing `commit_sha`/`external_run_id`/`payload_hash` (ATLAS-61/63). Anchor: ATLAS-70 milestone test | **PASS** (deterministic, CI) |
| An **agent-authored PASSED** record is not trusted | `EvidenceRepo.add` raises `TrustTierError` on agent-tier non-PENDING and persists nothing; agent PENDING is stored (ATLAS-61). Anchor: ATLAS-70 milestone test | **PASS** (deterministic, CI) |
| The **pin triple** is hashed over the full payload and is tamper-evident | `payload_hash` = SHA-256 over canonicalised `raw_payload`; the dedup key is `(external_run_id, payload_hash)` (ATLAS-62) | **PASS** (deterministic, CI) |
| **Reviews** map `APPROVED/CHANGES_REQUESTED/COMMENTED → PASSED/FAILED/WARNING` as PR_REVIEW; a review with no `commit_id` is **skipped, never pinned to "None"** | `normalise_review[s]` + `map_review_to_evidence` (ATLAS-65) | **PASS** (deterministic, CI) |
| **Docs** evidence is absence-based: `docs/` touched → one PASSED DOCUMENTATION_UPDATE listing paths; no docs change → **no record** | `normalise_pr_files` returns `None` when no `docs/` path; `map_docs_to_evidence` (ATLAS-66) | **PASS** (deterministic, CI) |
| Unrecognised CI jobs are **never silently dropped** | job-name table + `BUILD_RESULT` fallback with a warning; recognised vs fallback distinguished only by the warning, and that distinction is tested (ATLAS-64) | **PASS** (deterministic, CI) |
| `atlas evidence pull/list/show` **runs the pipeline end to end** | the CLI resolves `PRODUCT_KEY`, fetches via `fetch_pull_request` head SHA, normalises + ingests all three sources, reads back; offline-testable via injected `FakeGitHubClient` (ATLAS-67) | **PASS** (deterministic CI **+ live**, §3) |
| Oversized `raw_payload` is **capped at 64KB without breaking the pin** | `EvidenceRepo.add` replaces >64KB payloads with a self-describing marker after the pin guard; `payload_hash` never recomputed; exactly-at-cap stored verbatim (`>` not `>=`) (ATLAS-69) | **PASS** (deterministic, CI) |
| `atlas evidence` on a **cold database** fails cleanly, not with a traceback | a missing-schema `OperationalError` maps to `EXIT_PRECONDITION` across pull/list/show (ATLAS-130) | **PASS** (deterministic, CI **+ live**) |

---

## 2. Delivered

| Ticket | Delivered |
| --- | --- |
| ATLAS-61 (#109) | Evidence schema with trust fields + append-only + the two load-bearing guards in `EvidenceRepo.add`: agent-tier capped at PENDING (by rejection), and system-tier records refused unless commit-pinned. The guard every later source relies on. |
| ATLAS-62 (#110) | `atlas/github/` — `GitHubClient` Protocol + urllib `GitHubRESTClient` (Bearer auth, ETag/304, bounded backoff, `per_page=100` + Link-next warning), the frozen `NormalisedCheck`, the conclusion→status table, and `payload_hash` over the canonicalised payload. The transport-agnostic webhook-swap shape. |
| ATLAS-63 (#111) | `atlas/evidence/` — the first mapper: `evidence_type_for_job` (seeded with `test`), pure `map_check_to_evidence`, thin `ingest_checks`. First real producer of pinned system-tier evidence; `created_by_id` = `github-actions` (each component owns its actor id, not `pm-engine`). |
| ATLAS-64 (#112) | `lint`/`build`/`coverage` rows + the unrecognised→`BUILD_RESULT`+warning fallback that makes the mapper total; the dead `None`/skip path removed, three ATLAS-63 tests re-pinned. |
| ATLAS-65 (#113) | Review evidence — `fetch_pr_reviews` + `normalise_review[s]` (distinct `NormalisedReview`, the review-state table incl. DISMISSED→NOT_APPLICABLE/unknown→WARNING), `map_review_to_evidence`. Generalised `_get` to a `result_key=None` **bare-array** path; a null-`commit_id` review is skipped, never pinned to the string `"None"`. |
| ATLAS-66 (#114) | Documentation evidence — `fetch_pr_files` + the aggregate `NormalisedDocs` + `normalise_pr_files` (one-per-PR, `status`-agnostic `docs/` predicate, synthesised `docs:{head_sha}` pin, hash over the docs subset only), `map_docs_to_evidence`. No-docs → `None` → no record. |
| ATLAS-67 (#115) | The `atlas evidence pull/list/show` CLI. Added `fetch_pull_request` (object, not bare array) to resolve the head SHA; a **pure `_send` transport extraction** sharing ETag/304/backoff between array and object fetches (object-304 raises, array-304 → `[]`, conditional request still shared); offline under `FakeGitHubClient`; every precondition a clean `EXIT_PRECONDITION`. |
| ATLAS-130 (#116) | Live-discovered: `atlas evidence` tracebacked on a never-migrated database. A shared `except OperationalError` at the dispatch boundary maps it to `EXIT_PRECONDITION` across pull/list/show — narrow catch (`IntegrityError` is a different class, not masked), broad wrap chosen consciously to cover write-time `no such table: evidence`. |
| ATLAS-69 (#117) | The 64KB retention cap at `EvidenceRepo.add`: oversized `raw_payload` replaced by a self-describing marker (`_truncated`/`_original_bytes`/`_payload_hash`/`_source_uri`); size measured under the hash's canonicalisation; the pin and `payload_hash` untouched; no schema change. |
| ATLAS-70 (#TBD) | **Satisfied by construction** — the tier rules shipped in ATLAS-61 and the round-trips in ATLAS-63–66; delivered here as a single consolidating milestone anchor test (CI half → system-tier pinned; agent PASSED → rejected, PENDING → stored). No production code. |

---

## 3. The harness ledger — what the phase taught, and the live proof

**The pipeline was run for real, not just in CI.** `atlas evidence pull --pr 7
--repo derekrivers/symphony-todo-app` fetched live from GitHub and persisted
3 records (checks 2 / reviews 0 / docs 1). One record was then independently
cross-checked against the GitHub API: the persisted `commit_sha`,
`external_run_id`, `source_uri`, and the `success → passed` status all matched
the real workflow run #25878415204 on its actual head commit. The evidence the
system produced is accurate to ground truth — the strongest available
generalisation signal, on a repository that is not Atlas itself.

Three findings the live runs surfaced, each carried into §4:

- **Job-name typing is Atlas-centric (confirmed three times on real data).**
  Real foreign repos name workflows `CI`, `pre-commit`, `CodeQL`, `lock`,
  `pitch-surface`, etc. — none match Atlas's `test`/`lint`/`build`/`coverage`
  prefixes, so most checks fall through to the `BUILD_RESULT` fallback.
  Confirmed on the cli/cli replay, the symphony-todo-app live counts, and the
  externally-validated `CI → build_result` record. This is the concrete,
  real-data form of the over-fit-to-itself risk flagged in the product
  assessment. It is **not a bug** (the fallback is designed for it), but it
  matters the moment Phase 7 verification keys on a precise `evidence_type`.

- **Bootstrap gap.** `pull` requires a pre-migrated database **and** a
  hand-seeded `ATLAS` product; nothing in the CLI creates either. ATLAS-130
  made the *error* clean (no traceback), but there is still no `atlas db init`
  / product-seed command — a first-time operator must migrate and seed by hand.

- **Green gates miss cold-start defects.** Every test fixture calls
  `db.create_all()`, so the "empty/unmigrated database" path was never
  exercised — which is exactly why the cold-DB traceback (ATLAS-130) shipped in
  ATLAS-67 despite a fully green suite. Recorded so the lesson outlives the fix.

Engineering disciplines reinforced this phase: verify-against-the-pushed-branch
(every PR cloned fresh and re-gated, never trusting completion reports);
seeded-defect proof on every behavioural guard; pure-extraction fencing on
merged transport code (the `_send` refactor changed zero existing transport
tests); and "fix wrong behaviour with a missing sentence in a doc/runbook, not
a chat correction" (the `created_by_id` and null-`commit_id` corrections were
folded into runbooks, not patched after the fact).

---

## 4. Carry-forwards (owners and homes)

| Item | Owner / home | Status |
| --- | --- | --- |
| **Read-time corroboration / promotion** — system-tier evidence corroborating an agent's PENDING claim (the part of ATLAS-70's *spirit* beyond the storage cap) | Phase 7 — Verification Engine | **Open by design** — the design doc frames corroboration as a verification-time concern ("from a system-tier record or human approval"); it is correctly the next phase, not a Phase-6 gap. |
| **Job-name typing over-fit** | Phase 7 verification design | **Open** — decide whether verification needs a precise `evidence_type` (then the foreign-repo `BUILD_RESULT` collapse matters and the job-name convention needs widening/config) or only pass/fail-per-commit (then the fallback bucket is fine). Thrice-confirmed on real data (§3). |
| **Bootstrap command** (`atlas db init` / product seed) | Small follow-up ticket, pre-operator-handoff | **Open** — ATLAS-130 made the cold-DB error clean but added no bootstrap path; `pull` still needs a hand-seeded product. Its own small ticket (orthogonal to retention). |
| **ATLAS-130 debt entry** | `docs/tech-debt/debt-register.md` | **Open** — the "green gates, broken first use" lesson is captured in the generated debt entry but not yet committed to the register. Place it (this PR or its own commit). |

---

## 5. Why close here

Phase 6's charter is the **evidence machinery**: typed, trust-tiered,
append-only, commit-pinned storage, fed by real GitHub sources through a
runnable command. That machinery is built, deterministically tested, and —
uniquely for this phase — **validated against live ground truth on a
non-Atlas repository**. Every Phase-6 ticket is merged or satisfied by
construction.

Holding the phase open for read-time corroboration would conflate two
charters: Phase 6 *records* evidence; Phase 7 *acts on* it. The design doc
draws that line deliberately, and the roadmap places Verification immediately
after ATLAS-70. The three live findings (§3) are real and tracked (§4), but
none is a defect in the recording machinery — they are inputs to how Phase 7
should *consume* it. Closing here keeps the boundary honest and hands Phase 7
a proven, real-data-exercised evidence substrate to build verification on.

Phase 7 — Verification Engine — is where this evidence becomes load-bearing on
"done".

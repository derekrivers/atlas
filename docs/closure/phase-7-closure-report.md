# Phase 7 Closure Report — Verification Engine

Status: **CLOSED** as of 2026-06-28. The Verification Engine Epic (ATLAS-71
through ATLAS-77, plus ATLAS-80; ATLAS-78/79 retired) is complete: the
verification schema and required-check matrix resolver; all five per-check
evaluators (machine, documentation, acceptance, scope, human_approval); the
ticket- and PR-completion validators that compose them into one verdict; and
the `atlas verify` CLI that resolves a PR, runs the verdict, persists the
per-check `VerificationCheck` rows, and renders a human + JSON report.

This report closes Phase 7 on the **verdict machinery being built and proven
falsifiable** — every behavioural guard verified by a seeded defect, every PR
re-gated against its pushed branch. The two parts that make the verdict
*drive delivery* — the interactive operator-confirmation capture that writes
the human-tier acceptance/scope/human approvals, and the PM transition that
acts on a PASSED verdict — are, by the design doc's own framing, the next
increment (operator-confirmation capture) and a separate concern (the PM
Engine acts on the verdict, §`Principle`); both are carried forward in §4.
The reasoning for closing here is in §5.

Design doc: `docs/atlas/verification-engine.md`.

---

## 1. Milestone evidence

The Phase 7 milestone is met when a ticket with passing **agent-claimed**
evidence but no system-tier evidence cannot reach `done`, and the same ticket
completes once CI lands system-tier evidence at the PR head commit. Status by
claim:

| Claim | Asserted by | Status |
| --- | --- | --- |
| An **agent-claimed** machine result alone never completes a ticket; a **system-tier** result pinned to the head commit does | Two-layer trust model: `EvidenceRepo.add` rejects agent-tier non-PENDING at *write* (ADR-0008); `evaluate_machine_check` filters to system-tier at *read* (ATLAS-75). Anchors: ATLAS-75 unit test (uncapped agent-PASSED ignored) + the ATLAS-80 milestone CLI test (capped agent PENDING → ticket PENDING; CI system-tier at C → PASSED) | **PASS** (deterministic, CI) |
| **TESTS/LINT** require system-tier evidence pinned to C; latest-per-type; PENDING on absence, never FAILED | `evaluate_machine_check` (ATLAS-75) | **PASS** (deterministic, CI) |
| **Documentation** is coverage-vs-requirements when requirements exist, existence otherwise; system-tier, commit-pinned | `evaluate_documentation_check` reading covered paths from the docs evidence payload (ATLAS-74) | **PASS** (deterministic, CI) |
| **Acceptance criteria** are confirmed per-criterion by a human-tier MANUAL_APPROVAL pinned to C, keyed by `acceptance_criterion_hash` (SHA-256 of the criterion text); subset-complete | `evaluate_acceptance_criteria` (ATLAS-72) | **PASS** (deterministic, CI) |
| **Scope**: out-of-scope files require a per-file human-tier waive/fail pinned to C (`scope_decision_path`); latest decides; FAIL > PENDING > PASS | `evaluate_scope` (ATLAS-73) | **PASS** (deterministic, CI) |
| **human_approval** is a blanket human-tier MANUAL_APPROVAL at C carrying **neither** discriminator (distinct from an acceptance confirmation or a scope decision); latest decides, status passes through | `evaluate_human_approval` (ATLAS-76, OP-A) | **PASS** (deterministic, CI) |
| The **ticket verdict** is PASSED only when every *required* check is PASSED; FAILED if any required check FAILED; else PENDING. A `required=False` check (deferred SECURITY) never gates. A required type with no evaluator **holds the verdict at PENDING**, never silently passes | `evaluate_ticket` + the behavioural dispatch-completeness guard (ATLAS-76) | **PASS** (deterministic, CI) |
| The **PR verdict** aggregates the per-ticket verdicts (PASSED iff every closed ticket PASSED; FAILED if any FAILED; else PENDING); **per-ticket isolation** — a confirmation scoped to ticket A cannot satisfy ticket B | `evaluate_pr` over the shared `fold_statuses` (ATLAS-77) | **PASS** (deterministic, CI) |
| `atlas verify --pr <N>` resolves the close-set from the PR **title** `(ATLAS-NN)`, runs `evaluate_pr` at the head commit, **persists** one append-only `VerificationCheck` per check, and reports (human + `--json`) | `_verify_command` + the pure `parse_close_set` / `verification_checks_for` helpers (ATLAS-80) | **PASS** (deterministic, CI) |

---

## 2. Delivered

| Ticket | Delivered |
| --- | --- |
| ATLAS-71 (#120) | Verification foundation: the `VerificationCheck` model + `VerificationCheckType`, the append-only `VerificationCheckRepo`, migration `0017`, the required-check matrix YAML, and the pure resolver `required_checks(ticket) -> tuple[RequiredCheck, ...]`. The matrix drives every later evaluator; SECURITY surfaces only at `risk_level == critical` as `required=False` (OP-4 deferral). |
| ATLAS-75 (#121) | `evaluate_machine_check` — TESTS/LINT against the latest **system-tier** evidence pinned to C; agent-tier ignored; PENDING (never FAILED) on absence; status pass-through. The read-side half of the two-layer trust model. |
| ATLAS-74 (#122) | `evaluate_documentation_check` — covered paths read from the docs evidence payload; coverage mode when requirements exist, existence mode otherwise; system-tier + commit-pinned; defensive on capped payloads. |
| ATLAS-72 (#123) | `evaluate_acceptance_criteria` + the canonical per-criterion convention: a human-tier MANUAL_APPROVAL pinned to C, keyed by `acceptance_criterion_hash` (SHA-256 of stripped criterion text). The exported hash + the discriminator pattern the later evaluators reuse. |
| ATLAS-73 (#124) | `evaluate_scope` — in-scope = `relevant_docs` ∪ the `source_anchor` path; out-of-scope files resolved by a per-file human-tier waive/fail (`scope_decision_path`); three-outcome precedence (FAIL > PENDING > PASS); latest-decision-per-file; distinct-path dedup. No `allowed_paths` field (OP-2). |
| ATLAS-76 (#125) | `evaluate_ticket` — resolves `required_checks`, dispatches each to its evaluator via an adapter table, composes the verdict over the gating checks. Folded in the missing `evaluate_human_approval` (OP-A, discriminator-by-absence). The **behavioural** dispatch-completeness guard (driven from the resolver's output, both emission paths) ensures a required type without an evaluator holds the verdict rather than passing. |
| ATLAS-77 (#126) | `evaluate_pr` — pure outer-loop aggregation over the tickets a PR closes, ordered by `ticket.key`. Extracted the shared `fold_statuses` (single source of truth for the verdict fold; `_compose_verdict` refactored to call it, behaviour-identical). Per-ticket isolation is the load-bearing property. |
| ATLAS-80 (#127) | The `atlas verify --pr <N>` CLI (verify + record + report). Pure helpers `parse_close_set(title, body)` (title `(ATLAS-NN)` primary, `#NN` PR-number never captured) and `verification_checks_for` (CheckOutcome → VerificationCheck, terminal `completed_at`); an impure handler mirroring `evidence pull`'s GitHub-client/owner-repo construction; append-only persistence; documented exit-code contract (EXIT_OK on any verdict, EXIT_PRECONDITION on operational failure). Non-interactive; writes no Evidence (OP-A). |

---

## 3. The harness ledger — what the phase taught

**Every behavioural guard was proven falsifiable.** Each PR was cloned fresh
from the pushed branch and re-gated (ruff, mypy, import-linter, the full
pytest suite, the doc-linter); each safety property was then confirmed by
*seeding a deliberate defect and watching the guard go red* — the fail
precedence, the latest-decision-per-file ordering, the discriminator-by-absence
filter, the behavioural dispatch-completeness guard (pulling a real dispatch
arm reds the resolver-driven test), the per-ticket isolation (collapsing the
`ticket_id` filter reds with the exact "both PASSED" leak), the close-set
grammar (a `(#126)`-must-not-match case), and the terminal-vs-PENDING
`completed_at` mapping. The full suite closed the phase at 1456 passed / 6
skipped.

Findings and disciplines this phase:

- **The two-layer trust model, made explicit.** "No agent-manufactured
  completion" is enforced twice: `EvidenceRepo.add` rejects an agent-tier
  PASSED record at *write* (an agent cannot even store the claim), and the
  machine evaluator filters to system-tier at *read*. During ATLAS-80 review a
  milestone seed appeared not to bite; the investigation **exonerated** the
  test — the realistic CLI path is already neutralised at layer 1, and layer 2
  is independently guarded by ATLAS-75's unit test (which constructs uncapped
  agent-PASSED evidence directly). Verify-don't-trust cut both ways: a
  suspected gap, dug into, turned out to be defence-in-depth working as
  designed.

- **Single source of truth by extraction, not coordination.** The ticket and
  PR verdicts are the same fold rule one level apart; ATLAS-77 extracted
  `fold_statuses` and refactored the ticket-level `_compose_verdict` to call
  it, rather than letting two copies drift — guarded by ATLAS-76's existing
  tests staying green through the refactor.

- **Real-data correction of a planning assumption.** The verify close-set was
  initially specified to parse body closing-keywords (`Closes ATLAS-NN`).
  Checking the repo's actual merged PRs showed the convention is the
  `(ATLAS-NN)` key in the **title**; a body-keyword parser would have silently
  matched nothing and under-covered every PR. Corrected to title-primary
  before the runbook was finalised — a quiet-failure class caught by grounding
  in real data, not inference.

- **human_approval was an unbuilt evaluator.** The resolver could emit
  HUMAN_APPROVAL but no evaluator existed (72–75 covered the other four). Found
  while designing the ATLAS-76 composition, folded in as a tiny evaluator
  rather than a separate ticket (OP-A) — the composition could not be pure glue
  without it.

---

## 4. Carry-forwards (owners and homes)

| Item | Owner / home | Status |
| --- | --- | --- |
| **Interactive operator-confirmation capture (OP-3)** — the acceptance checklist + scope/human waive-fail that *write* the human-tier confirmations pinned to C | Next ticket (the operator will key it) | **Open by design** — the most consequential follow-up. Until it lands, acceptance/scope/human checks read PENDING, so **no real ticket reaches a PASSED verdict** (acceptance is always required, with no path to confirm it). `atlas verify` reports this honestly. |
| **verify → PM `review_required → done` wiring** | Phase 7.5 / early Phase 8 | **Open** — `verification-engine.md` states the PM Engine performs this on a PASSED verdict, but no code consumes the verdict; the engine computes and persists a verdict that nothing yet acts on. The cheapest seam to close while context is fresh. |
| **SECURITY evaluator** (ATLAS-71 gap) | A later ticket | **Open by design** — the resolver surfaces SECURITY only at `risk_level == critical`, as `required=False` so it never gates; no evaluator in v1. When built, reconsider moving the risk→security threshold into the matrix YAML (a doc change). Listed in `verification-engine.md` Open items. |
| **`--strict` / `--exit-code` CI-gating mode** (ATLAS-80) | A small follow-up | **Open** — deliberately deferred so v1 does not make the normal PENDING state "fail". FAILED → non-zero, for gating a merge in CI. The exit-code contract is documented. |
| **Documentation glob/prefix matching** (ATLAS-74) | A later ticket | **Open** — the doc evaluator uses existence/exact-path matching; directory/prefix/glob coverage is deferred. |
| **Coverage minimums; LLM-assisted acceptance assessment** | Phase 8+ | **Open by design** — both in `verification-engine.md` Open items; start with "coverage must exist", and design LLM-assisted assessment when the operator becomes the bottleneck, not before. |
| **64KB retention cap leaves coverage unconfirmable for very large doc PRs** (ATLAS-69) | Pre-existing (Phase 6 carry-forward) | **Open** — a doc PR whose file list is capped cannot have its coverage fully confirmed; the doc evaluator is defensive about it. |
| **Pre-existing debt-register items** — ATLAS-42 priority mapping; ATLAS-42→50 non-idempotent issue-create window; ATLAS-52 `relevant_docs` path format; ATLAS-53 lesson-facet wording; the two ATLAS-56 ContextPack gaps; ATLAS-50 Phase-4 external-flip (~60s live test); ATLAS-130 bootstrap gap (`atlas db init`/seed) | `docs/tech-debt/debt-register.md` | **Tracked** — none introduced by Phase 7; all carried in the register. |

---

## 5. Why close here

Phase 7's charter is the **verdict machinery**: the required-check matrix, the
five per-check evaluators, the ticket- and PR-completion validators, and the
runnable `atlas verify` command that records and reports the verdict. That
machinery is built, deterministically tested, and — uniquely this phase —
**every behavioural guard is proven falsifiable by a seeded defect**. Every
Phase-7 ticket is merged; ATLAS-78/79 were retired.

Holding the phase open for the interactive confirmation capture and the PM
transition would conflate three charters. Verification *computes* a verdict
(Phase 7); operator confirmation *supplies* the human-tier evidence the verdict
reads (the next increment, deliberately deferred under OP-A so the read-only
verify stays fully testable); and the PM Engine *acts* on the verdict (a
separate concern the design doc draws out in its `Principle`). The engine is a
pure evaluator by design — "it never creates evidence, never transitions
tickets itself". Closing here keeps that boundary honest and hands the next
increment a proven, seed-verified verdict engine to drive.

Phase 8 — Symphony orchestration — is where this verdict becomes the gate on
autonomous delivery.

# Phase 10 Closure Report — Operator API (Read Surface)

**Status: CLOSED.** Milestone passed by live execution against the Atlas
store. Repository at `331930b` (ATLAS-202, #254, merged). Every count below
was read from that commit or from `.atlas/atlas.db`; no count is estimated.

---

## 1. What Phase 10 delivered

A read-only, versioned HTTP projection over Atlas operational state. The
surface is deliberately thin: routes select one lower-layer operation and
present its result; cross-source assembly belongs in `atlas.orchestration`.

### E12 — Operator API (Read Surface)

| Key | PR | Delivered |
| --- | --- | --- |
| ATLAS-187 | #223 | `atlas.api` skeleton, application lifespan and base infrastructure |
| ATLAS-188 | #224 | Review-queue coordinating service in `atlas.orchestration` |
| ATLAS-189 | #225 | Unversioned `GET /api/reviews` endpoint |
| ATLAS-190 | #226 | Unversioned `GET /api/tickets` board endpoint with status filter |
| ATLAS-191 | #227 | HTTP presenters extracted from API dependencies |
| ATLAS-192 | #228 | Root documentation pointers reconciled with delivered state |
| ATLAS-194 | #236 | `/api/v1` prefix and canonical `StrEnum` response schemas |
| ATLAS-197 | #241 | AST sensor mechanically enforcing the API contains-no-logic rule |
| ATLAS-199 | #251 | Ticket-dependency and critical-path read projections |
| ATLAS-200 | #249 | Ticket-evidence read projection |
| ATLAS-201 | #252 | Lessons read projection |
| ATLAS-202 | #254 | Operator system-status projection |

ATLAS-189 and ATLAS-190 are named as they shipped. The versioned paths did
not exist until ATLAS-194 mounted `/api/v1` once at the application boundary.

### Cross-epic deliveries

| Key | Epic | PR | Delivered |
| --- | --- | --- | --- |
| ATLAS-193 | E5 | #233 | Storage forbidden from importing the Linear or GitHub adapters |
| ATLAS-195 | E3 | #235 | CLI disposition path for a stale proposed PlanRun |
| ATLAS-196 | E1 | #245 | SRC001/SRC002 source-anchor integrity sensor |
| ATLAS-198 | E1 | #242 | PATH and PHASE documentation-integrity checks |
| ATLAS-203 | E6 | #248 | Mapped Linear state asserted immediately after issue creation |

All 17 records are `done` in the live store. ATLAS-193 onward carry no
`external_github_issue_id`; their PR numbers above are from merged history,
not inferred from the store.

### Hand-delivered meta work

This work is absent from the ticket store but was a material part of the
phase's delivery cost. PR titles are authoritative for meta labels.

| Label | PR | Delivered |
| --- | --- | --- |
| ATLAS-029M | #229 | Claimed-key namespace reconciliation through ATLAS-192 |
| ATLAS-030M | #230 | Forbidden-storage-adapter-import stub |
| ATLAS-031M | #231 | Repair of four dangling store anchors |
| ATLAS-032M | #234 | API-v1 and stale-PlanRun disposition stubs |
| ATLAS-033M | #237 | Operator API phase design |
| ATLAS-034M | #238 | Operator API roadmap phase |
| ATLAS-035M | #240 | ATLAS-194/195 stub retirement and second sensor-wave seed |
| ATLAS-036M | #243 | ATLAS-196/197/198 stub retirement and render catch-up |
| ATLAS-037M | #244 | Single-ticket detail projection |
| ATLAS-038M | #246 | Final API-wave and Linear create-state stubs |
| ATLAS-039M | #247 | ATLAS-199..203 mint |
| ATLAS-040M | #250 | Acceptance-chain driver with independent merge gate |
| ATLAS-041M | #253 | Symphony workflow-gate and concurrency amendments |
| ATLAS-042M | #255 | Operator-environment incident record |

At the phase's first delivered commit, ATLAS-187 at `e75b685`, pytest
collected exactly 1,939 tests and `pyproject.toml` contained one
import-linter contract. At HEAD pytest collected exactly 2,078 tests and
the configuration contained three contracts: **1,939 → 2,078 (+139)** and
**1 → 3 (+2)**.

---

## 2. Milestone evidence (the closure gate)

Roadmap test: *"an operator can read the review queue and ticket board over
HTTP at /api/v1 with no direct database or CLI query, with the API
contains-no-logic rule mechanically enforced."*

Proven live on 2026-07-25 at 22:10:32+01:00 against the store at HEAD:

```text
uv run atlas api serve --host 127.0.0.1 --port 8765
curl --fail-with-body http://127.0.0.1:8765/api/v1/reviews
curl --fail-with-body http://127.0.0.1:8765/api/v1/tickets
```

Both HTTP reads returned `200 OK`. The review response contained zero
reviews. The ticket-board response contained 159 tickets: 98 `done`, 53
`needs_human_decision`, and 8 `rejected`. Those observations came from the
HTTP response bodies; the milestone execution made no direct database query
and invoked no Atlas query command.

The last clause is separate mechanical evidence, not an assertion about the
route implementations. ATLAS-197's `tests/test_api_architecture.py` sensor
walks the API AST and enforces one service or repository operation followed
by presentation. Its seeded probes
`test_api_no_logic_sensor_fires_on_seeded_extra_service_call`,
`test_api_no_logic_sensor_fires_on_seeded_two_repositories`, and
`test_api_no_logic_sensor_fires_on_seeded_domain_status_branch` each prove
that the sensor fires on the prohibited shape. That seeded-probe evidence
landed in #241 and remains in the HEAD test suite.

Every milestone clause therefore has distinct evidence: live HTTP reads for
the operator surface and a deliberately tripped architecture sensor for the
contains-no-logic constraint.

---

## 3. Seed disposition

Phase 10 had no numbered roadmap seed list to reconcile. It opened
retroactively after ATLAS-187..192 had shipped and the claimed-key incident
had been repaired. The initial design named ATLAS-187..191 as the delivered
surface; ATLAS-192 reconciled its documentation, ATLAS-194 versioned it, and
ATLAS-197/199..202 completed and constrained the E12 read contract.
ATLAS-037M delivered ticket detail outside the store-backed sequence. The
five cross-epic records in §1 are phase deliveries, not E12 seeds.

---

## 4. Closure-findings batch

There was no separate closure-findings ticket batch. Findings discovered
during the phase were delivered inside ATLAS-193..203 or through the
ATLAS-029M..042M meta ledger. The incidents and the machinery they produced
are recorded in §5 rather than relabelled as an additional batch.

---

## 5. Incident ledger

The phase's dominant work was repairing the operator loop around delivery:

- **Claimed-key namespace burn.** ATLAS-187..192 were claimed outside the
  key counter. The store and renders disagreed about the next legal key,
  making further hand-dispatched claims unsafe. ATLAS-029M reconciled the
  counter and records; `WORKFLOW.md` now makes the key authority exclusive
  under “Ticket key identity.” The roadmap/store namespace ruling remains
  open (§6).
- **Dangling store anchors blocked all minting.** Four ticket
  `source_anchor` values pointed to paths that did not exist, so planning
  could not proceed. ATLAS-031M repaired the four records. ATLAS-196 then
  added SRC001/SRC002 so unresolved store and render anchors fail
  mechanically instead of stopping a later minting session without a
  named cause.
- **Mint working-tree loss, three occurrences.** `atlas apply` wrote the
  durable store and also wrote planning renders and stub retirements into
  the working tree. A subsequent `git reset --hard` discarded the latter
  while leaving the store advanced, three times. The operator-environment
  runbook now has a binding “Minting: apply writes to two places” section,
  including the immediate commit rule and the divergence symptoms.
- **Pre-merge verification stranded delivered tickets.** `verify` ran
  before GitHub reported the PR merged. PASSED verdicts were stored, but an
  unmerged PR correctly produced no `PR_MERGED`; after the human merge the
  tickets remained stranded. ATLAS-040M's `scripts/close_ticket.py`
  acceptance chain has an independent GitHub merge gate before it invokes
  the final verify and sync sequence.
- **Linear's default state stranded blocked tickets.** Creation sent no
  workflow state, so Linear assigned the board default. A blocked ticket
  pulled back as `needs_human_decision`, outside both promotion and push,
  and had no automatic recovery. ATLAS-203 now asserts the mapped Atlas
  state immediately after confirmed creation while leaving later workflow
  ownership with Linear.
- **Wrong identifier in a PR title.** One PR title carried the Linear
  identifier instead of the Atlas key. The acceptance chain would have
  resolved an empty close set. Review caught the error before acceptance;
  no mechanical title/close-set guard exists yet (§6).
- **Stale-base reversion.** One PR was based on stale main and its diff
  would have reverted three merged PRs. Review caught it before merge.
  Rebase discipline is documented; the identified branch-protection
  control remains an administrator decision (§6).

These were not API-domain failures. They were failures in naming, minting,
anchoring, acceptance ordering, external workflow creation and integration
discipline. Each either produced fixed machinery or remains explicitly
owned below.

---

## 6. Carry-forwards (with owners and scope)

These remain open; none is closed by the Phase 10 status change:

- **Roadmap/store key identity — operator and roadmap governance.** The
  operational half of ATLAS-029M is delivered: the counter is reconciled
  and future allocation is ruled. The roadmap namespace collision class
  represented by ATLAS-91/97/107 still needs a canonical ruling. Fenced
  roadmap notation contains it mechanically but does not restore stable
  identity.
- **The 53-ticket park — operator triage.** Exactly 53 store records are
  `needs_human_decision`. **None** has a PR identifier, a `completed_at`
  value or a review cycle. They are undelivered records awaiting a ruling,
  not completed work. This report does not triage, attest or reclassify
  them.
- **Writeable API — future writeable-API phase.** Writes do not enter
  incrementally. Authentication, actor context and a threat model must be
  designed and land together as the phase's entry condition.
- **GUI and browser review — operator and future GUI phase.** Whether an
  agent's review includes a browser-based step remains unresolved. Phase
  10 neither designs nor implements it.
- **ATLAS-107 debt register entity — operator and debt governance.**
  ATLAS-107 remains `needs_human_decision`; its first-sensor gate has not
  been ruled met. The hand-maintained `docs/tech-debt/debt-register.md`
  does not silently complete that store ticket.
- **PR-title key guard — workflow/CI owner.** Mechanically require the
  Atlas key, rather than the Linear identifier, to define a PR close set.
  Review is the only current guard.
- **Stale-base merge protection — repository administrator.** Decide and
  configure the identified branch-protection control that prevents a
  mergeable stale diff from reverting already-merged work.
- **Unchecked operator scripts — operator/tooling maintainer.** Ruff
  covers the repository, but mypy and its pre-commit hook cover only
  `atlas tests`. Three Python scripts now perform store-touching or
  chain-driving work outside type checking. Audit existing findings before
  extending the typed gate; decide before accepting the next Python
  operator-tooling ticket, as recorded in ATLAS-040M's completion report.
- **PATH command-line blind spot — doc-linter owner.** ATLAS-198's PATH
  check exempts command lines. A repository path inside a backticked command
  such as `uv run python scripts/...` is therefore not guarded against a
  rename. #255 review verified the blind spot in both directions; extending
  PATH coverage must preserve legitimate command examples without making
  the current exemption invisible.

---

## 7. Critical success criteria — self-assessment

1. **Atlas generates its own backlog through plan/apply with stable
   identity — HELD WITH ONE OPEN EXCEPTION.** ATLAS-193..203 were minted
   through plan/apply. ATLAS-187..192 were the claimed-ahead incident;
   ATLAS-029M reconciled them and made the counter authoritative. The
   roadmap namespace collision remains a stable-identity defect (§6);
   fences contain it but do not resolve it.
2. **Atlas refuses unverifiable completion — HELD.** The acceptance chain
   retains system-tier evidence and a commit-matched `PR_MERGED` as separate
   gates. The pre-merge incident did not bypass that rule: it stranded
   tickets precisely because the merge evidence was absent, and ATLAS-040M
   repaired the operator sequence without weakening the gate.
3. **Every operational record is traceable to intent — HELD AFTER
   REPAIR.** ATLAS-031M repaired four dangling anchors and ATLAS-196 now
   checks store and render anchors mechanically. The 17 Phase 10 ticket
   records resolve in the live store; the meta work is separately labelled
   because it intentionally has no store record.
4. **The doc linter keeps canon internally consistent — HELD AND
   STRENGTHENED.** ATLAS-198 added PATH and bidirectional PHASE checks. This
   report and the roadmap CLOSED flip land together. The command-line PATH
   exemption remains a named coverage blind spot (§6).
5. **Product work begins only after criteria 1–4 hold — HELD FOR THIS
   PHASE.** Phase 10 is infrastructure, not product work. Its open
   exceptions are recorded as governance and tooling carry-forwards rather
   than represented as delivered product capability.

---

## 8. The honest close

Phase 10 proved that Atlas's read surface is complete for the ruled v1
contract and mechanically constrained. An operator started the real server
against the real store, read both milestone resources over `/api/v1`, and
received the whole 159-ticket board and the current empty review queue
without a database or query-CLI read. The architecture sensor is not a prose
promise: seeded forbidden shapes make it fire.

It did not prove a write surface, authentication, actor attribution, a threat
model, remote deployment, pagination, health semantics, a GUI or browser
review. Those boundaries remain exactly where the design places them.

The phase's dominant cost was not API code. Most of the incident ledger is
machinery around delivering work safely: ticket-key authority, resolvable
anchors, the two-place mint write, acceptance ordering, Linear creation
state, PR identity and stale-base control. The read API finished while the
operator loop repeatedly demonstrated ways correct code could be misnamed,
lost, stranded or reverted. Closing honestly means recording both results:
the read surface is complete and mechanically constrained, and the delivery
system around it consumed more corrective attention than the surface itself.

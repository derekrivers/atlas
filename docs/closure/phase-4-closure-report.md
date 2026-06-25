# Phase 4 Closure Report — PM Engine

Status: **CLOSED** as of 2026-06-25 (build complete 2026-06-23). The delivery-
coordination build is done and CI-evidenced — seventeen PRs (#63–#83), each
under both evidence tiers (agent completion reports corroborated by
system-tier CI pinned to head commits, per ADR-0008). The PM Engine pulls
status from Linear, pushes definitions under ADR-0006 field ownership,
promotes ready tickets, logs the full anomaly stack, ingests follow-ups
end-to-end (produce → operator commits → plan reads → apply retires), records
true status-transition history, reports delivery metrics including true
per-state cycle time, records tick crashes
durably, and drives the whole loop on a cadence with create-on-crash.

One scoped ticket was **deliberately deferred** rather than built: ATLAS-46
(roadmap synchronisation) raises an unanswered `roadmap.mmd` field-ownership
question and is the only Phase 4 line with no design detail, so it goes to its
own design pass (§6/§7) — an operator decision on record, not a silent
omission. The phase is **closed**: its live milestone evidence is recorded (§1). The
Linear→Atlas pull was exercised live via the promotion round-trip — Atlas wrote
`Ready for Agent` to Linear and `_pull` read that state back within the tick,
flipping ATLAS-1 to `ready_for_agent` — and the ATLAS-45 `fetch_comments` smoke
(`live_fetch_comments`) was run green against real Linear (2026-06-25). The
Leg-1 evidence is a promotion write-back, not an external flip; that nuance is
tracked in `docs/tech-debt/debt-register.md`.

---

## 1. Milestone evidence

The roadmap milestone is two-directional (line 260): *a status change in
Linear is reflected in Atlas within one sync cycle, and a definition change in
Atlas is reflected in Linear, with no other field crossing.* It splits along
the evidence tiers — the field-ownership invariant is deterministic and
CI-proven; the end-to-end live round-trip is operator-run.

| Claim | Asserted by | Status |
| --- | --- | --- |
| Definition change Atlas → Linear, **no other field crossing** | ADR-0006 field-ownership tests + the `sync_tick` push leg (ATLAS-41/42/43) | **PASS** (deterministic, CI) |
| Ready tickets promoted into `Ready for Agent` from the projected graph | readiness predicate reused, single-writer promotion (ATLAS-43) | **PASS** (deterministic, CI) |
| The full anomaly stack logs one row per observation, idempotent across ticks | `logged_since` / `recorded_since` dedup over DebtItem and TickFailure (ATLAS-118/119/120/44/125) | **PASS** (deterministic, CI) |
| Every real status transition is captured durably | `TicketStatusTransition` appended atomically inside the sole status writer (ATLAS-121) | **PASS** (deterministic, CI) |
| The follow-up loop closes — stubs read as input, retired on apply | `collect_inbox_documents` merged for provenance; apply retires to `processed/` (ATLAS-45/122) | **PASS** (deterministic, CI) |
| **Status change in Linear reflected in Atlas within one sync cycle** | **operator-run live, 2026-06-25**: promotion wrote `Ready for Agent` to Linear and `_pull` read it back within the tick, flipping ATLAS-1 to `ready_for_agent` (ATLAS-50; ADR-0008 system-tier; see debt register — promotion write-back, not external flip) | **PASS** (operator-run live) |
| `fetch_comments` reads a real tagged comment end-to-end | **operator-run live, 2026-06-25**: `live_fetch_comments` leg run green against real Linear (ATLAS-45; ADR-0008 system-tier) | **PASS** (operator-run live) |

All seven milestone claims are now evidenced — five CI-proven, two by recorded
operator-run live legs (2026-06-25). The live evidence is no longer owed.

---

## 2. Delivered

| Ticket | Delivered |
| --- | --- |
| ATLAS-41 (#65) | Linear integration under ADR-0006 field ownership — `LinearGraphQLClient`, the definitions-out / status-in boundary the milestone's "no other field crossing" half asserts. |
| ATLAS-42 (#67) | Bidirectional `sync_tick` with a `Ticket.linear_synced_at` cursor; pull status, push definitions, per-tick reconciliation. |
| ATLAS-43 (#68) | Ready-state detection and the **one sanctioned outbound write** — a dedicated `set_state` mutation, the only path by which Atlas changes Linear state. |
| ATLAS-116/117 (#63/#64) | The delivery-anomaly model `DebtItem` (append-only, one row per observation) and its recurrence predicate; `AnomalyType` relocated to its model. The precedent for every model-before-writer split that followed. |
| ATLAS-118 (#69) | Out-of-ownership transition logging — the first `DebtItem` writer; one row per unmapped transition, deduped via the last-observed cursor. Report-only. |
| ATLAS-119 (#70) | Dwell-breach logging against per-state horizons (in_progress 24h, pr_open 48h, review_required 7d). Report-only. |
| ATLAS-120 (#71) | Review-cycling detection — >3 `changes_requested → pr_open` round trips routes to Needs Human via the sanctioned `set_state`. The one anomaly that acts. |
| ATLAS-44 (#76) | Stale-block detection — a report-only detector reusing the promotion graph; a BLOCKED ticket with no live blocker logs one `STALE_BLOCK` row. |
| ATLAS-45 (#73) | Follow-up ingestion, **producer half** — scans tagged comments, writes one inbox stub per comment, deduped by source-comment id. Writes no Linear/Atlas state, does not commit. |
| ATLAS-47 (#72) | Delivery metrics CLI — `atlas pm report`, a pure reader; cycle time honestly labelled the current-dwell proxy until ATLAS-126 delivered the true metric. |
| ATLAS-123 (#75) | Encoded the resolved AT-7 pair metric — `ANCHOR_COVERAGE_FLOOR = 0.50` live; content coverage recorded-not-asserted. Resolved the AT-7 carry-forward Phase 3.5 §7 left open. |
| ATLAS-125 (#78) | Tick-failure record — `TickFailure` (append-only, system-attributed, **ticket-less**) and the `recorded_since` dedup predicate; tick-failure count in `atlas pm report`. |
| ATLAS-50 (#79) | PM scheduler — the recurring loop driving `sync_tick` on a cadence with create-on-crash and graceful SIGTERM/SIGINT shutdown after the in-flight tick. |
| ATLAS-121 (#80) | State-transition history — `TicketStatusTransition` (append-only, FK-backed), appended **atomically** inside the sole status writer's real-change branch with `from_status` captured before reassignment. The capture half; consumed by ATLAS-126. |
| ATLAS-122 (#81) | Follow-up **consumer** — `atlas plan` reads the committed inbox as a *separate* merged-for-provenance source (corpus globs untouched); `atlas apply` retires consumed stubs to `processed/` on applied or rejected. Closes the ATLAS-45 loop. |
| ATLAS-126 (#83) | True per-state cycle time from the transition log — `pairwise` over each ticket's transitions yields completed episodes only (the initial and current-open episodes excluded by construction), retiring the ATLAS-47 dwell proxy. Closes the cycle-time arc 47 → 121 → 126. |

---

## 3. The harness ledger — what the phase taught and where it was encoded

- **A record lives where the model's invariants let it.** Create-on-crash could
  not reuse `DebtItem` (it requires a `ticket_id`; a tick crash has none), so
  `TickFailure` was a new ticket-less model (ATLAS-125); `TicketStatusTransition`,
  by contrast, *is* ticket-scoped and so is FK-backed (ATLAS-121). Each model's
  invariants decided its shape — the inverse of Phase 3.5's "verify the shape of
  a redundancy before deduplicating."
- **Model before writer, every time.** ATLAS-116 preceded its three writers;
  ATLAS-125 preceded ATLAS-50; ATLAS-121 landed its model with full schema weight
  beside a few lines of writer wiring. The schema diff stays auditable in its own
  PR; the writer diff stays small.
- **The Linear boundary is an allow-list, not detector discretion.** Exactly one
  sanctioned outbound write (`set_state`), used by one anomaly (review-cycling);
  the follow-up producer writes only inbox stubs and does not commit; every other
  detector is report-only. Observe widely, act narrowly and only where sanctioned.
- **Attribution flows from the layer that knows it.** ATLAS-121's transition
  needed a `created_by_id`, but `CREATED_BY = "pm-engine"` lives in `pm` and
  storage cannot import up the spine. The fix was not a comment-linked duplicate
  in storage — it was passing the actor in from the sole caller, so storage never
  presumes its caller's identity and `CREATED_BY` stays single-sourced. The
  inline write still commits atomically with the status change.
- **A fail-closed gate must be tested against the substrate, not its assumption.**
  ATLAS-122's committed-inbox gate looked correct but `git status --porcelain`
  collapses a wholly-untracked directory to one entry — so a brand-new inbox would
  have *silently dropped* its stubs rather than failing closed. `--untracked-files=all`
  closed the hole. Paired with the `fnmatch` `*`-crosses-`/` trap (a glob would
  re-read `processed/` forever, fixed by parent-path equality), the lesson is the
  same as Phase 3.5's "a guard must be shown to break": verify the tool's actual
  behaviour, not the behaviour you assumed.
- **A spec cross-references its mechanism's owner; it does not restate it.**
  ATLAS-122 made `atlas plan`'s input set honest by pointing §2.1/§2.2 at the
  pm-engine doc that owns follow-up ingestion — single-source by structure, not a
  second description that drifts.

---

## 4. The phase's defining lesson

**When the engine first reaches into a live external system, safety is a
bounded write allow-list, not detector judgement.** Phase 4's standing risk was
that any of five detectors might "fix" what it saw in Linear. The design held
the line: definitions push out, status pulls in, nothing else crosses; one
mutation (`set_state`) is sanctioned and used by one route; the follow-up
producer is write-isolated to an inbox it cannot even commit. The human-steers /
agent-executes contract survived contact with a live system because the boundary
was enumerated and enforced, not left to each detector's discretion at the
moment of observation.

---

## 5. The design doc and the charter gate

Phase 4 entered carrying Phase 3.5 §7's open question: did the 77-line
`pm-engine-and-linear-sync.md` clear the "design doc first" bar? It did, by
growing into it — the doc now carries the five-step sync loop, the
anomaly-and-dwell section, follow-up ingestion (producer and consumer), delivery
metrics, and the scheduler/cadence section, each extended as its ticket landed
and each the single source for its mechanism. The charter gate is answered: the
spec was sufficient as a spine and was kept current as the authority.

---

## 6. The live milestone — recorded

Phase 3.5 closed on a deterministic milestone the moment CI was green. Phase 4's
milestone was not deterministic: the Linear→Atlas round-trip within one cycle and
the ATLAS-45 `fetch_comments` smoke are system-tier live evidence under ADR-0008,
deferred for connectivity and kept as explicit owed proof. Both were run and
recorded on 2026-06-25 (§1), so the phase is closed. CI green was necessary but
not sufficient; the live legs supplied the rest.

The scope question is **resolved, not open**: ATLAS-121 and ATLAS-122 were built
(the cycle-time-capture and follow-up-consumer halves their producers needed);
ATLAS-46 was deferred by operator decision to its own design pass. ATLAS-46 is
the only Phase 4 line with no design detail and the only one raising an
unsettled question — `roadmap.mmd` is single-writer under ADR-0007, so syncing
it with Linear needs a field-ownership ruling (which direction wins, what
crosses) the way ADR-0006 settled it for tickets. Building it before designing it
would be exactly what this phase's discipline argues against; it does not gate
the milestone and is cleanly separable.

---

## 7. Carry-forwards (owners and homes)

| Item | Owner / home | Status |
| --- | --- | --- |
| AT-7 bar threshold | Phase 3.5 §7 → ATLAS-123/124 | **Resolved** — anchor floor 0.50 live; containment-aware content bar pinned as the unified exact∪content floor 0.80 (ATLAS-124, #103, §7.2) |
| State-transition capture for true cycle time | ATLAS-47 → ATLAS-121 → ATLAS-126 | **Resolved** — capture landed (121) and is consumed as true cycle time (126); the proxy is retired |
| ATLAS-46 roadmap synchronisation | Phase 4 → its own design pass | **Deferred (operator decision)** — needs a `roadmap.mmd` ⇄ Linear field-ownership ruling first; no design detail yet |
| ATLAS-126 historical cycle time from the transition log | PM Engine (seeded by ATLAS-121) | **Resolved** — `atlas pm report` now computes true per-state cycle time over completed episodes; the dwell proxy is retired |
| Planner promotion of inbox stubs | PM Engine (named by ATLAS-122) | **Open, observation** — ingestion + lifecycle landed; whether the planner reliably promotes a stub to a ticket may want a prompt-template refinement, seed only if live runs show it needed |
| ATLAS-45 live smoke / ATLAS-50 live milestone | Operator (ADR-0008 system-tier) | **Resolved** — both live legs run and recorded 2026-06-25 (§1 rows now PASS); phase CLOSED. Leg-1 is promotion write-back, tracked in the debt register as a cheap future tightening |
| ATLAS-124 content-coverage bar pinning | Planning track | **Resolved** — second capture taken; unified exact∪content floor 0.80 pinned, citation excluded as a diagnostic (#103, §7.2) |
| Atlas → Linear priority mapping | PM Engine; floating since ATLAS-42 | **Open** — needs an Atlas priority convention pinned first (Linear's inverted 0–4 enum) |
| Transient-transport retry (RemoteProtocolError on long runs) | PM Engine; distinct from ATLAS-109 content retry | **Open, benign** — promote if long-run crashes recur in the TickFailure record |

---

## 8. Phase 5 readiness

Phase 5 (Context Renderer) is the natural unlock for multi-agent parallelism:
the readiness predicate already computes the parallelisable frontier
deterministically, but prompt authoring stays serial until the renderer exists.
The import-linter spine guards the addition — a `planning/pm →
dependencies/storage/core` edge is allowed, an inverted one fails the build — so
the renderer can import the readiness and PM layers without risk of inversion.

The honest gate before Phase 5 begins is §6: run and record the live milestone.
A renderer built on an engine whose own milestone is unproven inherits that gap;
closing Phase 4 cleanly first keeps the layering — and the evidence trail —
trustworthy under what Phase 5 multiplies. ATLAS-46 and ATLAS-124 are
homed carry-forwards, not Phase 5 blockers.
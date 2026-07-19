# Phase 9 Closure Report — The Learning System

**Status: CLOSED.** Milestone passed by live controlled experiment.
Repository at `c29b0f0` (playbook #213 merged). Reviewer-tier assessment
across a full gate sweep and a live milestone execution; every number
below is measured, not estimated.

---

## 1. What Phase 9 delivered

The first backward-closing loop in Atlas: delivery outcomes become
inputs to future delivery, gated by the operator. Nine E11 tickets, all
`done`, each through the full acceptance spine (review → evidence →
confirm → verify → merge-at-verdict):

| Key | PR | Delivered |
| --- | --- | --- |
| ATLAS-99 | #195 | Extractor — LLM over bounded evidence bundle → DRAFT lesson |
| ATLAS-65 | #196 | Retrieval — ACTIVE-only, tag/type matching, cap 3, total order |
| ATLAS-100 | #199 | Promotion lifecycle — review/promote/reject/archive/merge/--stale |
| ATLAS-101 | #201 | Renderer integration + citation feedback + DRAFT-rejecting validation |
| ATLAS-102 | #200 | Deterministic pattern detection (tag recurrence ≥3) |
| ATLAS-103 | #203 | Playbook drafting from ACTIVE lessons onto a review branch |
| ATLAS-104 | #198 | Delivery analytics — `atlas lessons report` |
| ATLAS-105 | #202 | Organisational memory search |
| ATLAS-106 | #197 | Continuous scheduler |
| ATLAS-107 | — | Code-quality debt register — deferred with reason (gate unmet) |

Test suite grew 1828 → 1924. New package `atlas/learning/` registered
in the import spine.

---

## 2. Milestone evidence (the closure gate)

Roadmap test: *"a completed fixture ticket produces a DRAFT lesson; the
lesson appears in context packs only after operator promotion."*

Proven live on 2026-07-17 as a controlled experiment — same ticket,
same renderer, same store, one operator ruling as the only variable:

| Render of ATLAS-63 | `historical_lessons` |
| --- | --- |
| Before promotion (lesson `30cec9d0` DRAFT) | `[]` |
| After `lessons promote 30cec9d0 --confidence 0.8` | `['30cec9d0-…']` |

The pack diff showed the `## Lessons` section appearing, token estimate
946 → 1213, and **nothing else changed**. The negative half — the DRAFT
invisible despite a perfect two-tag match — was captured before
promotion and cannot be re-created; it is the load-bearing evidence that
ADR-0009's gate is real, not nominal. Confirmed simultaneously at all
three ACTIVE-only exits: retrieval (`[]`), search (`no lessons found`),
and playbooks (`no ACTIVE lessons`, exit 2).

**The full loop then executed end to end** (2026-07-18): three ACTIVE
`linear-sync` lessons were synthesised into a canonical playbook, which
registered itself in `docs/MANIFEST.md`, passed the recursive doc-linter,
and merged to `docs/atlas/playbooks/linear-sync.md` as canon the planner
ingests (#213). Docs → Delivery → Lessons → Docs, closed, with PR numbers.

---

## 3. Seed disposition (criterion 3 reconciliation)

The roadmap's Phase 9 seed keys collided with real minted tickets meaning
different things (roadmap `ATLAS-91`="Lesson extraction"; store
`ATLAS-91`="Phase 7 milestone test"). The Phase 9 tickets were minted from
`learning-system.md` anchors, not roadmap anchors, so the documents
diverged without any gate noticing — the acceptance pin checks only the
roadmap's internal uniqueness. Reconciled below; the roadmap section is
rewritten to the delivered keys in the same closure PR.

| Roadmap seed | Disposition |
| --- | --- |
| 91 Lesson extraction | Delivered — **ATLAS-99** (#195) |
| 92 Failure pattern detection | Delivered — **ATLAS-102** (#200) |
| 93 Success pattern detection | **Carried forward** — see §6 |
| 94 Playbook generation | Delivered — **ATLAS-103** (#203) |
| 95 Knowledge enrichment | **Retired** — never scoped into E11; see §6 |
| 96 Delivery analytics | Delivered — **ATLAS-104** (#198) |
| 97 Lesson promotion CLI | Delivered — **ATLAS-100** (#199) |
| 99 Organisational memory search | Delivered — **ATLAS-105** (#202) |
| 100 Continuous scheduler | Delivered — **ATLAS-106** (#197) |
| 117 Code-quality debt register | Parked — **ATLAS-107**, gate unmet |

Also delivered, not Phase 9 roadmap seeds: **ATLAS-65** (retrieval; its
seed is Phase 6 roadmap-53) and **ATLAS-101** (renderer integration).

---

## 4. Closure-findings batch

Nine defects surfaced by *using* the system during closure — none
visible to any automated gate, all caught by review or live operation.
All minted, delivered, and merged the same session:

| Key | PR | Defect closed |
| --- | --- | --- |
| ATLAS-170 | #205 | One-shot CLI commands printed nothing on success (4 operator-hours lost to silence) |
| ATLAS-171 | #207 | Agent could complete verified work and lose it at push (`inherit=core` credential boundary) |
| ATLAS-172 | #206 | `related_ticket_ids` conflated provenance with citation; merge destroyed provenance |
| ATLAS-173 | #210 | Promotion gate could only see a lesson's title (`atlas lessons show`) |
| ATLAS-174 | #209 | Schema drift crashed mid-tick, discarding LLM spend |
| ATLAS-175 | #208 | Playbooks were canon no manifest could see (non-recursive MAN005) |
| ATLAS-176 | #211 | REJECTED unmappable on a live board (British `cancelled` vs live `canceled`/`duplicate`) |
| ATLAS-177 | #204 | Free-form extractor tags made lessons unreachable and pattern detection unable to fire |
| ATLAS-178 | #212 | Skip itemisation flooded tick output (reviewer spec defect) |

---

## 5. Incident ledger

The session's live incidents, each converted to fixed machinery:

- **Migration drift, ×3.** `alembic upgrade head` skipped after a
  migration-carrying merge → mid-tick `OperationalError`, twice with LLM
  spend incurred then discarded. Closed by **ATLAS-174**; its own drift
  struck a *read* path at 20:00 (post-fix) and was correctly exempted by
  174's D-5 — the fix narrowing its own blast radius on the same class.
- **Credential stranding.** An agent completed verified ATLAS-102 work
  and lost the push to a 403; recovered from `~/code/atlas-workspaces/
  ATL-250` as commit `286dc9a`. Root cause: `inherit=core` strips the
  operator's `GITHUB_TOKEN`; agents use on-disk credentials. Closed by
  **ATLAS-171**. The 403 then struck the *operator* (no `before_run`
  hook) — see carry-forwards.
- **Duplicate module delivery.** ATLAS-104 and ATLAS-102 both built
  `patterns.py` because AC-3 referenced pattern detection with no
  dependency edge. Resolved by apply-and-cancel (#200 superseding #198's
  copy). Root-cause carried forward.
- **Two reviewer spec defects**, both caught and repaired same-session:
  the citation-metric conflation (ATLAS-170 D-2 over-itemised → ATLAS-178)
  and the runbook reference to an unlanded file (ATLAS-170 D-6). Recorded
  because *reviewer error is a real category* — the strongest evidence
  for why the human-review role compresses but does not eliminate.

**Anomaly baseline (clean, all explained):** 2 `out_of_ownership_
transition` (both 2026-07-05 — the original recorded symptom of the
REJECTED-unmappable defect ATLAS-176 later closed; the system flagged the
bug twelve days before the fix), 1 `pack_render_failure` (the ATLAS-169
incident), 3 tick failures (2 migration-drift, 1 pre-Phase-9 Linear 400).
Every anomaly traces to a fixed or explained incident.

**Extraction trigger 2 proven reachable.** `atlas preflight` C2 passed
live with the Duplicate entry restored to `LINEAR_STATE_MAP` — the check
that failed all phase. Board-side rejections can now become `rejected`
status and feed failure-lesson extraction for the first time.

---

## 6. Carry-forwards (with owners and scope)

These are open decisions and known gaps, recorded so none closes by
omission:

**Scope:**
- **Seed 93 — Success pattern detection.** Carried forward, scoped:
  extend `detect_pattern_candidates` to `SUCCESS_PATTERN`, threshold TBD.
  *Explicit low priority* — a recurring failure tag is an actionable
  harness signal (a missing doc or lint rule); a recurring success tag is
  affirming but rarely prompts action. Mint when priority allows.
- **Seed 95 — Knowledge enrichment.** Retired: never scoped into E11,
  appears in no ADR or design doc. The adjacent real gap (empty
  `related_adr_ids` on every lesson; no lesson-to-lesson linking) may be
  scoped independently if wanted.
- **Roadmap/store ticket-identity collision.** The delivered E11 tickets
  `ATLAS-65/99/100/101–107` share key numbers with the roadmap's
  pre-existing Phase 2.5 planner tickets of the same numbers — two
  distinct sets of delivered work under one key each. The closure PR
  handles this mechanically (the roadmap lists the E11 keys inside fenced
  blocks so the enumeration parser does not double-count them against
  their planner namesakes, while the coverage sensor still resolves every
  key), but the underlying collision is a criterion-1 stable-identity
  violation: `ATLAS-101` denotes two different delivered tickets depending
  on which document you read. Resolve in Phase 10 by renumbering one set
  or introducing an epic-scoped key namespace. **Carried forward.**

**Learning-loop integrity (surfaced by the first real promotion session):**
- **Lessons go stale silently.** A lesson extracted from a *rejected*
  ticket (ATLAS-155) described a fix that was later shipped *differently*
  (ATLAS-176 dropped `cancelled`); the ACTIVE lesson then contradicted the
  merged code, and the playbook built from it inherited the contradiction.
  `review --stale` keys on citation count, not on whether the described
  code still exists. The loop has no mechanism to notice reality moving
  out from under a promoted lesson. **Highest-value carry-forward.**
- **Tag vocabulary / pattern density.** 27 DRAFT lessons, max failure-tag
  recurrence 2 vs threshold 3 → pattern detector reports zero candidates
  and structurally will until the corpus shares vocabulary. ATLAS-177
  anchors *new* tags but the backfill corpus is free-vocabulary. Retagging
  the existing corpus is operator work at promotion (edit-then-promote).

**Operability (each cost real session time):**
- **No `atlas lessons edit`.** Edit-then-promote is a first-class design
  path but requires raw SQL against the store, which fails silently on a
  WHERE miss and races the live process (needed `.timeout`). Should be a
  supported command using the CLI's own connection.
- **No canonical DB path.** Repeated "did it take? / which DB?" confusion
  traced to the CLI not resolving one store deterministically or printing
  which file it used. A report and a query can silently diverge.
- **Operator-side credential preflight.** ATLAS-171 fixed the agent's
  blind spot; the same 403 then hit the operator, where no hook runs.
  `atlas preflight` should probe GitHub write access.
- **Stub-minted tickets carry `tags=[]`.** Every stub ticket enters the
  graph with no tags/component, making ATLAS-177's anchoring inert for
  exactly the tickets minted most, and making them near-unreachable as
  retrieval targets. Inbox stub front matter needs `tags`/`component`.
- **Stubs cannot declare dependency edges.** The 173→172 and 102/104
  couplings had to be held by hand in Needs Human because `plan --stubs`
  mints edge-less tickets. This is the concurrency ceiling's real cause —
  the scheduler can only govern parallelism it can see. Highest-leverage
  operability item.

**Process docs owed (the closure meta-PR):**
- Acceptance runbook (`docs/runbooks/pr-acceptance.md`) — drafted, needs
  the *Done-is-a-hand-motion* and *alembic-upgrade-after-migration*
  amendments, plus the silence-discipline paragraph ATLAS-170 D-6 could
  not land.
- `operator-environment.md` — the two-auth-channel note, the
  version-pinned-plugin-patch correction, delete the stale urlopen note.
- The missing-dependency-edge lesson (§5) as a planning-integrity rule.
- Two proposed follow-ups (`ATLAS-173-1`, `ATLAS-174-1`) sit untracked in
  the inbox awaiting triage before the next `plan` run.
- The 53-ticket park triage (delivered-but-unattested vs genuinely open).

---

## 7. Critical success criteria — self-assessment

1. **Atlas generates its own backlog with stable identity** — HELD WITH
   ONE EXCEPTION. Every Phase 9 ticket minted through plan/apply. The §3
   seed-key collision was roadmap doc drift and is fixed; but closing it
   surfaced a genuine identity collision — the delivered E11 keys
   65/99–107 also name delivered Phase 2.5 planner tickets (§6). That is a
   real stable-identity violation, mechanically contained in this PR and
   carried forward for a proper resolution, not a doc-only artifact.
2. **Refuses unverifiable completion** — HELD. Every merge gated on a
   verify verdict at the verdict commit; the acceptance spine ran on all
   19 PRs.
3. **Every operational record traceable to intent** — HELD after §3
   reconciliation. Two apply commits carry a literal `PlanRun <uuid>`
   placeholder (reviewer error, recorded); the store holds the
   authoritative PlanRun rows.
4. **Doc linter keeps canon consistent** — HELD and strengthened:
   ATLAS-175 closed a blind spot (subdirectory canon) the linter's own
   docstring claimed to cover.
5. **Product work begins only after 1–4** — the Learning System is the
   last infrastructure phase; criteria 1–4 hold.

---

## 8. The honest close

Phase 9's code was done and green before this session; what this session
did was **close the loop** — prove the second milestone clause live, run
the first real promotion session, and discover that a working,
fully-tested system still had nine defects visible only in use and a set
of learning-loop integrity gaps visible only once a human curated real
lessons. Every one was converted to fixed machinery or a scoped
carry-forward the same session.

The sharpest lesson of the phase is one the phase taught about itself:
**the system extracted a lesson predicting the exact defect that later
blocked it, and the only reason the prediction didn't help is that nobody
had promoted it yet.** That is the argument for Phase 9 and the argument
for the operator gate, in a single fact. The Learning System does not
make Atlas intelligent; it makes what its operator decides is worth
keeping survive, compound, and reach the next agent who needs it —
provided the operator does the keeping. This session was the operator
doing the keeping, for the first time, on the system's account of itself.

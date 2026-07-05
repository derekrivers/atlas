# Smoke B Closure Report — Phase 8 Milestone (Autonomous Delivery, base case)

> Operator note (delete on commit): reconcile the evidence ids, timestamps,
> and seam lines below against `smoke-b-closeout.md` (the Phase 7 machine
> capture) before committing; this report was drafted from the operator-relayed
> live outputs and the capture is the canonical record. Suggested home:
> `docs/closure/smoke-b-closure-report.md`. Phase 8 is NOT closed by this
> report — ATLAS-82/84/85/86/88/90 remain; this closes the milestone's base
> case ("Smoke B is the ATLAS-90 base case: base-case before generaliser").

Smoke B ran the Phase 8 milestone test live, twice: a hand-authored inbox
stub flowed stub → mint → sync → dispatch → PR → evidence → confirmation →
merge → Done against real Linear, real Symphony, real GitHub, and real CI,
with exactly three manual acts — the three designed human gates (apply,
confirm, merge). The first fixture (ATLAS-109) proved the loop through
dispatch and exposed a verification dead end by design collision; the second
(ATLAS-110) completed the loop to Done on both sides. Main's head is now the
loop's own artifact: `6f75649` — "ATLAS-110: Document delivery loop (#153)".

## 1. Milestone evidence

Milestone (roadmap, Phase 8): *a ready, context-rich fixture ticket flows
pack → Symphony → PR → evidence → verification without manual steps other
than the defined human gates.* One qualifier holds: packs are not yet
consumed (ATLAS-82 pending); dispatch ran on the issue's definition fields,
per WORKFLOW.md's designed fallback. "Context-rich" was carried by the
definition fields, not an embedded pack — see F-7.

| Claim | Asserted by | Status |
| --- | --- | --- |
| A committed inbox stub is **deterministically promoted** to one proposed ADD (no model on the path) | `promote_inbox_stubs` (ATLAS-108 capability, PR #148); Phase 1 v2: exactly one ticket ADD (`new:109`, "Document the delivery loop under docs/") in a 32-ADD/260-MODIFY diff | **PASS** (live, twice) |
| `apply --add-only` **mints against a populated, DONE-heavy store** touching nothing existing | ATLAS-109/110/111 capability chain (PRs #149/#150/#151): MODIFY + PROPOSE_ARCHIVE + frozen-source CONFLICT + existing↔existing dep ADDs all skipped and reported; only the fixture materialised | **PASS** (live, twice) |
| **Human gate #1** (apply) is interactive and consequential | v2 run: gate read before `y`; the earlier ATLAS-108 spurious mint (a `y` on an unread 2-ADD diff) is the counterfactual that proves the gate is load-bearing | **PASS** (with F-4 lesson) |
| The **ATLAS-143 title seam** holds live: embedded key, byte-exact render, round-trip | Phase 2 checkpoints 2.2a/2.2b, both runs: `'ATLAS-110: Document the delivery loop under docs/'` byte-exact; `parse_close_set` → exactly `(ATLAS-110,)` | **PASS** (live, twice) |
| **Ready for Agent promotion** is the PM Engine's sole write | Phase 2 checkpoint 2.3, both runs; state read back by the pull | **PASS** (live, twice) |
| **Symphony walks its owned edges and stops at handoff** (ADR-0008 boundary) | Phase 3 watcher, both runs: `ready_for_agent → in_progress → pr_open → review_required`, then stop; v2: 18:19:41Z → 18:31:42Z | **PASS** (live, twice) |
| The **agent PR carries the ticket key** through the real parser and the CI gate | Checkpoint 3.2, both runs (parse_close_set + `check_pr_title.py` agree); v2 PR #153 "ATLAS-110: Document delivery loop" | **PASS** (live, twice) |
| CI evidence is ingested **system-tier with the full pin triple** | Phase 4 v2: 12 rows at `b285f21c…`, 12 system-tier, 0 pin violations (checked store-side by commit, see F-6) | **PASS** (live) |
| A **docs/ change produces DOCUMENTATION_UPDATE at C** | Phase 4 v2: `docs: 1`; `documentation_update passed docs:b285f21c…` — the inverted v1 `docs: 0` | **PASS** (live) |
| The verdict **cannot pass before operator confirmation** (no gate bypass) | Phase 4, both runs: verdict PENDING with system-tier checks green and `acceptance_criteria` pending — the designed negative test | **PASS** (live, twice) |
| **Human gate #2** (confirm) records human-tier evidence per criterion; verify recomputes to PASSED | Phase 5 v2: three interactive confirmations → `manual_approval` evidence ×3 pinned to C → `verdict for ATLAS-110: passed` | **PASS** (live) |
| **Human gate #3** (merge) is out-of-band; verify **observes** and records commit-pinned PR_MERGED | Phase 6 v2: 6.0 merged by operator; 6.2 `PR_MERGED at C` system-tier | **PASS** (live) |
| **Done flows Linear-first, pull reconciles Atlas** (single writer, one-tick latency) | Phase 6 v2: tick 1 `Linear → done · Atlas → review_required`; tick 2 `done · done` | **PASS** (live) |
| An agent **cannot manufacture** a doc pass or an acceptance pass | documentation check is system-tier-only; acceptance is human-tier-only; both held under live pressure (v1's PENDING wall was this working) | **PASS** (live) |

## 2. Delivered (the capability chain the smoke forced)

| Ticket / PR | Delivered |
| --- | --- |
| ATLAS-108 → real key 108* (#148) | Deterministic inbox-stub promotion: front-matter-declared `ProposalTicket`, zero LLM on the path, fail-closed `StubPromotionError`, A-1 self-contained epic re-statement. *Key provenance carries the OP-5(b) smear: the PR label predates the mint; the store's real ATLAS-108 is the spurious duplicate (F-4). |
| ATLAS-109 label (#149) | Opt-in `apply --add-only`: skips MODIFY and PROPOSE_ARCHIVE (no removals ever), refuses CONFLICT, skip counts reported at the gate. Cleared the MODIFY wall. |
| ATLAS-110 label (#150) | Add-only partitions CONFLICT by `would_have_been`: frozen-source (moot) skipped, identity/tie still refused in every mode. Cleared the DONE-backlog CONFLICT wall. |
| ATLAS-111 label (#151) | Add-only scopes dependency ADDs to fixture-incident edges (`new:` endpoint); existing↔existing edges skipped and reported; `_apply_command` catches `GraphValidationFailed` → typed violations + EXIT_PRECONDITION; `phase_1.sh` exit mapping made truthful. Cleared the graph-mutation hole (found live as a hallucinated cycle, ATLAS-24→23→22→24). |
| ATLAS-110 (real key, #153) | The fixture itself: `docs/delivery-loop.md`, one heading, one paragraph, ADR-0008 named. Merged as `6f75649`. |

Abandoned with cause: fixture v1 (real key ATLAS-109, PR #152, closed
unmerged) — structurally unverifiable (F-1); superseded by the docs/-resident
v2 per OP-9(a).

## 3. The harness ledger — what the smoke taught

- **F-1 (design collision, resolved by OP-9a):** `ticket_type: documentation`
  ⇒ documentation check unconditionally required (matrix) ⇒ findings mode ⇒
  needs system-tier DOCUMENTATION_UPDATE at C ⇒ only producer is the docs
  mapper ⇒ which counts only `docs/**` — and fixture v1's own non-goal
  forbade touching anything but root README.md. Every component correct;
  the specs disagreed about whether root README is documentation. Whether the
  docs-mapper prefix should widen to README is a fair future question,
  decided on its merits, not under a smoke.
- **F-2 (walls are capabilities):** the populated-store mint required three
  successive add-only refinements (MODIFY skip → frozen-CONFLICT partition →
  dep-ADD scoping), each found by the system failing closed, none foreseen at
  review. Reviewer lesson, carried forward: when a change is scoped by entry
  *type*, check its meaning per entry *kind* too.
- **F-3 (fail-closed worked every time it was needed):** DirtyInputError,
  ConflictRefusalError, GraphValidationFailed (pre-commit, store untouched),
  StubPromotionError semantics, the PENDING-before-confirm wall, and the
  trust-tier guards all refused correctly under live pressure. No silent
  wrong write occurred at any point in either run.
- **F-4 (LLM noise vs the human gate):** the re-plan proposed a duplicate of
  completed work ("Evidence CLI cold-database … (ATLAS-130)"), it slipped a
  `y` on an unread diff, and minted as the store's real ATLAS-108. Disposition:
  Canceled in Linear → pulled back as `rejected` (terminal, frozen). Lesson:
  the gate is the guard; read the ADD lines. Candidate instrumentation: an
  expected-ADD-count assertion in `phase_1.sh` (ledger L-7).
- **F-5 (connector deadlock is account-level):** headless dispatch burns
  attempt 1 on a `codex_apps` GitHub-connector elicitation
  (`turn_input_required`; Symphony's crash-retry is the correct refusal —
  verified upstream in `app_server.ex`/`orchestrator.ex`). The local
  `~/.codex/.tmp/...github/.app.json` fix is DISPROVEN (file read `{"apps":{}}`
  a minute before a run whose first attempt still elicited); the persistent
  grant is the account-linked connector (`connector_76869538…`, identical
  across both incidents). v2 recovered because the resumed agent pivoted to
  shell git — adaptation, not a fix. OP-11: remove the connector at account
  level; probe with one dispatch before trusting a session.
- **F-6 (instrumentation must not misread a healthy system):** `phase_4.sh`
  4.1 queried evidence by `ticket_id`, which pulled rows never carry (binding
  is verify-time via the close-set) — a false FAIL over a correct store. Same
  genus as the `keys_in_render()` phantom keys. Instruments encode
  assumptions; the smoke audits the instruments too.
- **F-7 (promotion anchor durability):** a promoted ticket's `source_anchor`
  points into the inbox, which the pack corpus (`collect_input_documents`,
  §2.1 set) has never contained — every promoted ticket is born
  pack-unrenderable (both fixtures were). Harmless today (nothing consumes
  packs; dispatch runs on definition fields, proven live twice); becomes
  load-bearing at ATLAS-82. The fix ticket must precede ATLAS-82.
- **F-8 (namespace discipline, again):** the smoke's number collisions —
  GitHub PR #146 typed as a ticket key; roadmap seed "ATLAS-130" surfacing
  inside a duplicate ticket's title; `new:109` (a positional index) adjacent
  to real key 109 — all resolved by one rule: the counter is the only key
  authority; every other number is a different namespace.
- **F-9 (stale boilerplate asserts falsehoods):** `atlas verify`'s "Note
  (OP-A)" claims no operator confirmations exist, printed directly beneath
  three human-tier confirmations PASSING the acceptance check. A sentence
  that predates the capability it denies (L-6).

## 4. Carry-forwards (owners and homes)

| Item | Owner / home | Status |
| --- | --- | --- |
| L-1 `keys_in_render()` phantom prose keys | instrumentation-cleanup ticket | Open |
| L-2 `phase_4.sh` 4.1 ticket-binding query → head-commit query | instrumentation-cleanup ticket | Open |
| L-3 Phase-0 dispatch preflight: connector/elicitation probe (supersedes the disproven `.app.json` check) | instrumentation-cleanup ticket | Open (shape depends on OP-11) |
| L-4 `phase_1.sh` stub content/path parametrization (`--stub-file` should carry the path) | instrumentation-cleanup ticket | Open |
| L-5 phase scripts exec bits (fixed ad hoc for 3/4; sweep the family) | instrumentation-cleanup ticket | Open |
| L-6 `atlas verify` stale "Note (OP-A)" boilerplate | small code ticket | Open |
| L-7 `phase_1.sh` expected-ADD-count gate assist (F-4) | instrumentation-cleanup ticket | Open (operator call — the gate may be guard enough) |
| Promotion anchor durability (F-7) | its own design-pass ticket; **must precede ATLAS-82** | Open |
| `LinearGraphQLClient._execute` no timeout | its own ticket (pre-existing finding; phase ceilings mitigate) | Open |
| OP-11 account-level GitHub connector removal + probe dispatch | operator (Codex account) | Open — before the next dispatch session |
| ATLAS-1 stale-status reconciliation (parked in Needs Human — durable, honest) | operator decision; likely a status-history pass over bootstrap-era tickets | Open |
| Store ATLAS-108 (spurious duplicate) | resolved — Canceled → `rejected`, terminal | Closed |
| Fixture v1 / real ATLAS-109 / PR #152 | resolved — Canceled + PR closed unmerged, superseded by v2 | Closed |
| OP-5(b) dev-PR label smear (#148–#151 labels vs real 108/109/110 keys) | accepted provenance quirk; documented here | Closed (documented) |
| `lint-pr-title` in branch-protection required checks (WARN 6.0 — token scope blind spot) | operator: verify once in GitHub settings | Open (one glance) |
| Docs-mapper scope question: should root README count as documentation? | future spec discussion, on its merits | Open (unscheduled) |
| ATLAS-82 / 84 / 85 / 86 / 88 / 90 | Phase 8 remainder; Smoke B is ATLAS-90's base case | Open |

## 5. The run record

- **Fixture v2:** store key **ATLAS-110** "Document the delivery loop under
  docs/" (`documentation`, epic ATLAS-E1); stub
  `docs/planning/inbox/smoke-b-fixture-v2.md` → `processed/`.
- **PR #153**, head **C = `b285f21c84e79a12e7605baf4411b251950776b6`**,
  merged by operator as **`6f75649`**.
- **Evidence at C:** 12 rows, all system-tier, pin triple complete; incl.
  `documentation_update` (`docs:b285f21c…`, id `ce29be1f…`); + 3 human-tier
  `manual_approval` confirmations (`793991b3…`, `89add3c8…`, `8ae5a853…`);
  + `PR_MERGED` at C (6.2).
- **Verdict:** PENDING pre-confirm (both runs, by design) → **PASSED**
  post-confirm → Done on both sides (tick 1 Linear, tick 2 Atlas).
- **Seams:** 2.2a/2.2b/2.3 PASS (both runs) · 3.2 PASS (both runs) ·
  6.2 PASS · Phase 7 capture: `smoke-b-closeout.md`.
- **Human acts, total:** apply `y` (×3 criteria-informed decisions across the
  runs' gates), confirm ×3, merge ×1 — plus environment/board hygiene. No
  manual step touched the delivery path outside the three gates.

# Phase 11 Closure Report — Operator UI (Read Surface)

**Status: CLOSED.** Milestone passed by the seeded live-API browser suites and
operator execution of the local API/UI path. Repository at
`108e9e8f1bc45b63d53739948735f8f54e95c9b8` (ATLAS-218, #283, merged).
Every ticket state below was checked against Linear and every PR mapping
against merged GitHub history on 2026-07-30.

---

## 1. What Phase 11 delivered

A read-only browser instrument over the Phase 10 Operator API. The application
lives at `apps/operator-ui/`, couples to Atlas only through `/api/v1`, and
keeps presentation composition in the browser. It introduces no UI writes,
authentication, Linear writes, GitHub writes, or second source of operational
truth.

### E13 — Operator UI (Read Surface)

| Key | PR | Delivered |
| --- | --- | --- |
| ATLAS-209 | #282 | Accessibility and responsive pass |
| ATLAS-210 | #269 | Application shell: navigation, theme toggle and command palette |
| ATLAS-211 | #272 | Ticket board view |
| ATLAS-212 | #271 | Operator UI CI pipeline |
| ATLAS-213 | #274 | Critical path view |
| ATLAS-214 | #276 | Dependency graph view |
| ATLAS-215 | #270 | Playwright end-to-end harness over a seeded live API |
| ATLAS-216 | #278 | Epic grouping on the ticket board |
| ATLAS-217 | #277 | Lessons view with draft triage |
| ATLAS-218 | #283 | Open-source readiness for the Operator UI |
| ATLAS-219 | #264 | Generated OpenAPI TypeScript client with a CI drift guard |
| ATLAS-220 | #281 | Overview dashboard |
| ATLAS-221 | #267 | Query layer, development proxy and API-unreachable primitives |
| ATLAS-222 | #275 | Review queue view |
| ATLAS-223 | #263 | `apps/operator-ui` scaffold with template demo domains removed |
| ATLAS-224 | #265 | Theme token contract from the vendored `theme.css` |
| ATLAS-225 | #280 | Ticket-detail dependencies and readiness tab |
| ATLAS-226 | #273 | Ticket-detail definition and metadata view |
| ATLAS-227 | #279 | Ticket-detail evidence tab |

All 19 E13 records are `Done` in Linear.

### Cross-epic E12 deliveries

| Key | PR | Delivered |
| --- | --- | --- |
| ATLAS-207 | #266 | `GET /api/v1/dependencies/graph` |
| ATLAS-208 | #262 | `GET /api/v1/epics` and `epic_key` on the ticket-board item |

Both cross-epic records are `Done` in Linear. These are the only additive v1
read routes introduced by Phase 11.

### Hand-delivered meta work

| Label | PR | Delivered |
| --- | --- | --- |
| ATLAS-044M | #261 | Phase 11 design, governed ticket batch and planning renders |
| ATLAS-045M | #268 | Backlog audit and verification-gate hardening performed during the delivery run |

At phase entry the Python suite reported 2,076 passing tests after the
pre-phase corrective batch. The final accepted Phase 11 head reported 2,134:
**2,076 → 2,134 (+58)**, in addition to the new browser, acceptance and
accessibility suites.

---

## 2. Milestone evidence (the closure gate)

Roadmap test:

> An operator opens the UI in a browser against a running `atlas api serve`,
> reaches every ticket's definition, evidence and dependency readiness, the
> review queue with its acceptance gates, the critical path, and the lessons
> draft queue — with no CLI query and no database read — and the end-to-end
> suite proves each view against a seeded live API rather than against fixtures.

The final acceptance candidate was ATLAS-218 at
`03224410d81d649653a97e07f91f346abe00aed9`, tested as GitHub's merge candidate
against the current base and then merged as `108e9e8f`.

Exact-head CI run 716 passed all 14 required jobs:

- Python: **2,134 passed, 6 skipped**
- Live-API Playwright: **36 passed**
- Browser components: **38 passed**
- UI acceptance: **33 passed**
- Accessibility and responsive: **7 passed**
- OpenAPI generation drift, UI lint, TypeScript, bundle build, Python lint,
  mypy, doc lint, import contracts and PR-title validation all passed

The live-API harness seeds a real SQLite store, starts a real
`atlas api serve`, drives every delivered route through Chromium, and tears the
store and process down after the run. The accessibility job visits every
delivered view in light and dark modes, deliberately proves a seeded axe
violation is detected, traverses the keyboard surface, checks tab and table
semantics, and verifies laptop and tablet widths without horizontal overflow.

The operator also started the API and Vite UI locally against the existing
Atlas store on 2026-07-30 and read the Overview dashboard. That execution
proved the supported local run path independently of the seeded CI harness. It
also exposed the sync-freshness semantic defect recorded in §5 and §6; that
defect does not invalidate route reachability or the phase's read-only
milestone, but it is not represented as correct behaviour.

Every milestone clause therefore has concrete evidence: real HTTP/browser
execution for the delivered routes, generated-contract drift enforcement for
the API boundary, and a deliberately tripped accessibility rule for the
automated accessibility gate.

---

## 3. Placeholder disposition

The old roadmap carried 21 `ATLAS-2NN` placeholders inside a paste-ready
fenced block. The governed plan/apply run assigned the real keys
ATLAS-207 through ATLAS-227.

The assignment is not the same order as the prose placeholder list. The
authoritative disposition is:

- Cross-epic API work: ATLAS-207 and ATLAS-208
- E13 closeout: ATLAS-209 and ATLAS-218
- E13 shell, views, testing and infrastructure: ATLAS-210 through ATLAS-227,
  excluding the two cross-epic keys above

The roadmap is rewritten to the delivered titles and real keys in the same
closure change. No placeholder remains and no key is claimed for Phase 12
before `atlas apply`.

---

## 4. Closure findings and review corrections

There was no separate closure-findings ticket batch. Exact-head review and the
CI feedback loop found and corrected material defects before merge:

- **ATLAS-216 documentation drift.** Epic grouping was initially described as
  partly future work after it had become active functionality. The final head
  made the canonical wording describe only the delivered behaviour.
- **ATLAS-220 derived-state guard bypass.** The first remediation still allowed
  duplicated Overview derivations through response destructuring. A second
  exact-head cycle closed the bypass and added positive and negative boundary
  tests without banning legitimate presentation filtering.
- **ATLAS-225 integration preservation.** Its first finished head conflicted
  with the newly merged Evidence tab. The accepted rebase preserved Definition,
  Metadata and Evidence while adding Dependencies as an independent request.
- **ATLAS-209 invalid tab semantics.** The first accessibility run failed
  because Lessons status triggers referenced tab panels that did not exist.
  Symphony added the panels, the regression was tested, and the complete
  accessibility job then passed 7/7.

These findings are part of the phase evidence. Green first-pass implementation
was not treated as sufficient; the accepted result is the corrected exact
head after integration with current `main`.

---

## 5. Incident ledger

- **Sibling merges repeatedly invalidated otherwise correct PRs.** Concurrent
  work shared the ticket board, ticket detail and test harness. One merge could
  leave the next PR conflicted or behind `main`, forcing another Symphony
  cycle before its evidence could be accepted.
- **Mechanical integration consumed agent capacity.** The remediation was
  frequently a deterministic rebase and preservation of both feature sets,
  rather than new semantic implementation. The operator remained unable to
  perform that post-handoff path through one governed command.
- **Exact-head review caught defects that green neighbouring heads could not
  settle.** Evidence and approval were correctly invalidated whenever a PR head
  or its base changed. That discipline increased cycle count but prevented
  stale evidence from authorising a different commit.
- **Accessibility CI repaired its own first failure.** ATLAS-209's required
  accessibility job exposed a real ARIA defect, Symphony resumed the ticket,
  and the corrected head passed the complete job. The gate demonstrated that
  it detects and feeds back real failures rather than merely adding a nominal
  check.
- **The Overview sync timestamp is semantically wrong.** The displayed
  `last_linear_sync_at` is derived from the newest ticket definition cursor,
  not from the last successfully completed Linear sync tick. A successful
  no-op or status-only sync can therefore still render `Stale — 3d ago`. The
  operator discovered this by running the delivered UI against the real store.
- **Canonical closure lagged implementation.** All Phase 11 code and tickets
  were complete while `implementation-roadmap.md` still contained the
  paste-ready placeholder block and `ROADMAP.md` still claimed only Phases
  1–9 were closed. This closure change repairs that drift rather than allowing
  implementation status to substitute for canonical closure.

---

## 6. Carry-forwards (with owners and scope)

These remain open; none is closed by the Phase 11 status change:

- **True Linear-sync success timestamp — PM/API owner.** Persist a real
  successful-sync tick timestamp and make `/api/v1/status` source
  `last_linear_sync_at` from it. A ticket's definition-push cursor is not a
  substitute. Add coverage for a successful no-op tick and a status-only pull.
- **Post-handoff mainline integration — Phase 12 / Autonomous Delivery
  (E10).** Deliver exact-head assessment, an operator-owned lease-guarded
  rebase workspace, and a binding mainline-freshness acceptance restart.
  Conflict resolution remains human; remote mutation must fail closed on head
  or base movement.
- **Browser review policy — operator.** Phase 11 supplies a trustworthy browser
  surface. It does not decide whether an agent-authored PR review must include
  an operator browser step.
- **Writeable API/UI — future writeable phase.** Authentication, actor context
  and a threat model remain a single entry condition. No write control should
  enter incrementally.
- **Production serving and remote deployment — future deployment design.**
  Vite's loopback development proxy is the supported path. Binding, origins,
  authentication and deployment must be designed together.
- **Pagination-aware aggregates — future API/UI contract change.** Overview
  aggregates and client-side filters assume complete collection responses.
  Pagination must revisit those consumers in the same change.
- **Roadmap/store key identity — roadmap governance.** The earlier collision
  class remains contained but not conceptually resolved. Phase 11 introduced
  no new collision and Phase 12 must obtain keys only from `atlas apply`.

---

## 7. Critical success criteria — self-assessment

1. **Atlas generates its own backlog through plan/apply with stable identity —
   HELD WITH THE EXISTING ROADMAP-NAMESPACE EXCEPTION.** The 21 Phase 11
   records were minted through the governed flow, and their Linear identities,
   Atlas keys and PRs reconcile. The older roadmap collision class remains a
   named carry-forward.
2. **Atlas refuses unverifiable completion — HELD.** Every accepted ticket was
   reviewed and evidenced at an exact head. Rebases and corrective commits
   invalidated the previous evidence instead of inheriting it.
3. **Every operational record is traceable to intent — HELD.** The E12 and E13
   tickets resolve to canonical Phase 11 design headings, Linear records and
   merged PRs. Meta work is labelled separately because it intentionally has no
   store ticket.
4. **The doc linter keeps canon internally consistent — HELD, WITH CLOSURE
   DRIFT REPAIRED HERE.** Runtime/design changes remained mechanically checked,
   but the phase-status prose lagged delivery. This report, the delivered
   roadmap section, root pointer and manifest registrations land together.
5. **Product work begins only after criteria 1–4 hold — HELD FOR THIS
   PHASE.** Phase 11 is an operational control-plane surface, not an investment
   product feature. It makes Atlas inspectable before the system is asked to
   deliver external product value.

---

## 8. The honest close

Phase 11 turned Atlas from a capable CLI and data store into an observable
delivery system. The operator can now inspect the board, grouped epics,
complete ticket definitions, evidence pin state, dependency readiness, review
gates, critical path, dependency graph, lessons and system summary through one
read-only browser application. The OpenAPI boundary is generated and checked,
the application is exercised against a real API and seeded store, and
accessibility is a required gate rather than a retrospective aspiration.

The phase did not make Atlas remotely deployable or writeable, and it did not
remove the operator from acceptance. It made the operator's job legible.

The delivery run also showed where the next bottleneck moved. Symphony can
implement several scoped tickets concurrently, but sibling merges make later
heads stale and send mechanical integration back through expensive full agent
cycles. Exact-head discipline is correct and must not be weakened. The next
phase therefore adds a governed operator-owned rebase lane around that
discipline rather than relaxing it.

The remaining status-timestamp defect is recorded plainly: one Overview signal
can be stale even after a successful sync because it measures the wrong event.
That is a follow-up, not a reason to pretend the UI milestone failed or that
the signal is correct.

Phase 11 is closed because its bounded read-surface contract is delivered,
integrated, tested and usable. It closes with a better control plane and with a
precise account of the operational seams the next phase must address.

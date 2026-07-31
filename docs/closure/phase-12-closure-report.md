# Phase 12 Closure Report — Mainline Integration Control

**Status: CLOSED.** ATLAS-228 through ATLAS-230 are `Done` in Linear and their
PRs #286 through #288 are merged in dependency order. Repository at
`48081304c006cf82f55e69e295f2b77f09474939` (ATLAS-230, #288, merged).
Ticket states, PR mappings and exact accepted heads were checked on 2026-07-31.
The controlled live closure gate then completed on disposable PRs #292 and
#291, including two defects found and corrected in PRs #293 and #294 before the
successful remote rewrite.

The implementation milestone is complete and operationally evidenced. A real
sibling merge left a trailing `Review Required` PR stale; the operator lane
rebased and republished it with an exact old-head lease, fresh CI ran at the new
head, old-head evidence and confirmation were rejected, and exact-head
acceptance restarted to a passed verdict.

---

## 1. What Phase 12 delivered

Phase 12 closes the post-handoff integration seam exposed by the Phase 11
three-agent run. Atlas can now determine whether one exact PR head contains one
exact current `main`, prepare and publish an operator-owned rebase without
changing the primary checkout, and refuse to enter or complete acceptance if
the head, base, branch, repository, eligibility or mergeability moves.

### E10 — Autonomous Delivery

| Key | PR | Accepted head | Delivered |
| --- | --- | --- | --- |
| ATLAS-228 | #286 | `8a610a0b` | Read-only exact-SHA PR integration assessment and `atlas pr status` |
| ATLAS-229 | #287 | `c446b543` | Durable detached-worktree rebase lifecycle with an explicit expected-value lease |
| ATLAS-230 | #288 | `01f4cd98` | Initial and final exact-head gates around the canonical close driver |

All three store records are `Done` in Linear. They were delivered serially:
ATLAS-229 consumed ATLAS-228's classifier, and ATLAS-230 bound both earlier
capabilities into acceptance.

### Hand-delivered meta work

| Label | PR | Delivered |
| --- | --- | --- |
| ATLAS-046M | #284 | Closed Phase 11 and opened Phase 12 in the canonical roadmap |
| ATLAS-047M | #285 | Applied the governed three-ticket Phase 12 batch and planning renders |

The final Phase 11 Python suite reported 2,134 passing tests. The accepted
Phase 12 merge candidate reported 2,209: **2,134 → 2,209 (+75)**, while the
existing browser, accessibility, OpenAPI, build and architecture gates
remained required.

---

## 2. Milestone evidence (the closure gate)

Roadmap test:

> Merge one of two sibling PRs, then take the trailing PR from a stale
> `Review Required` head through the operator-owned rebase lane. The published
> head must contain exact current `main`, the update must use a lease pinned to
> the original head, and old-head evidence and confirmations must not authorise
> the new head. Head/base races, conflicts and lease rejection must fail
> without unintended remote mutation.

The deterministic layers and the controlled live drill provide evidence for
every clause:

- **Assessment.** ATLAS-228 reads PR identity and exact head from one snapshot,
  independently resolves the current base branch head, compares those exact
  SHAs, separates eligibility, ancestry and mergeability, and fails closed on
  incomplete, contradictory or indeterminate GitHub data.
- **Rebase and publication.** ATLAS-229 exercises clean, conflicted,
  interrupted, resumed, aborted and published lifecycles over real temporary
  Git repositories. Tests inspect the exact push argv, prove primary-checkout
  invariance, simulate head and base movement, reject foreign or multiple push
  destinations, and prove that neither a lease race nor an invalid path moves
  a remote ref or deletes an unmanaged worktree.
- **Acceptance restart.** ATLAS-230 proves the first assessment precedes
  evidence or prompts, the second fresh assessment follows exact-head
  verification, and any head/base/identity/API movement blocks both the merge
  prompt and the completion tail. Old-head evidence remains history and cannot
  satisfy a new head.

### Controlled live sibling-PR drill

The live closure gate used disposable same-repository PRs and an isolated copy
of the Atlas store, with ATLAS-230 represented as `review_required`. It did not
change Linear or the canonical Atlas database.

1. Trailing PR #291 began at head
   `8e5bc892446b5e70462f756c79a22847f9f4dae3` on base
   `48081304c006cf82f55e69e295f2b77f09474939`. Initial CI run
   `30629388514` was green, operator confirmation
   `938850f3-cd75-4af0-b76a-ed85bf92a4c7` was pinned to that head, and the
   aggregate exact-head verdict passed.
2. Sibling PR #292 merged head
   `548c0ea6882d41b4df944499e44fc0501949ac7` as
   `feb6fbc8d8be8d5afe25b2a8cce1234e3d4737b4`, making #291 genuinely stale.
   A preliminary content-neutral sibling, #290, had merged as `c9e1fb77`
   without advancing GitHub's PR base snapshot; it changed no repository file.
3. The drill exposed that GitHub's PR payload retained its creation-time
   `base.sha`. Atlas therefore initially misclassified the stale head as
   current. PR #293 corrected assessment to resolve current `main`
   independently and merged as `7974375a359bb6620732a91ee4ac6fd617cf1903`.
4. The first clean rebase prepared against that head, but publication refused
   without a push because its separate pre-write snapshot repeated the same
   historical-base assumption. PR #294 corrected that gate and merged as
   `495fffafd8319b9784345b827fb8ba4f5f48cc37`. The unpublished workspace was
   aborted through the supported lane and prepared again against exact new
   `main`.
5. The successful receipt
   `derekrivers_atlas-pr-291-8e5bc892446b-8af6c33fb166.json` records original
   head `8e5bc892446b5e70462f756c79a22847f9f4dae3`, pinned base
   `495fffafd8319b9784345b827fb8ba4f5f48cc37`, republished head
   `8af6c33fb1669132b77fa7d7ca3d918a4f1a340e`, and the explicit expected
   old-head lease. Publication completed at `2026-07-31T12:33:12Z`.
6. Before new evidence was pulled, verification at the republished head
   reported tests, lint and acceptance all `PENDING`; the old-head records
   authorised nothing. Fresh CI run `30631036744` then passed all 14 required
   jobs, and CodeQL run `30631034240` passed all three analyses. Tests and lint
   became `PASSED` while acceptance remained `PENDING`.
7. New operator confirmation `ba0cd146-276c-4c63-bf5d-d002d97f7461` was
   pinned to `8af6c33f`; the final exact-head aggregate verdict passed. PR #291
   was then closed unmerged and its branch deleted. PR #289 removes #292's
   temporary mainline marker.

The final accepted candidate was `01f4cd982f6ab8de198eda4a5c3e5265556ab7aa`,
tested by CI as merge candidate `881f9d9063419f3b9f371d619557cfd406965b8e`
against current `main`, then merged as `48081304`. CI run 730 passed all 14
required jobs:

- Python: **2,209 passed, 6 skipped, 1 dependency deprecation warning**
- Python lint and formatting, mypy, import contracts and documentation lint
- Operator UI lint, types, OpenAPI drift, bundle build, components, live-API
  end-to-end, acceptance, accessibility and responsive checks
- PR-title validation

The live drill therefore satisfies the roadmap test rather than deferring it.
It also proved the fail-closed boundary under real GitHub semantics: both
historical-base defects stopped progress before remote mutation, and the final
publication occurred only after both read paths used exact current `main`.

---

## 3. Authority and safety boundaries that now hold

- `atlas pr status` is read-only. It does not fetch Git, update a ref, write
  Atlas or Linear state, or infer freshness from branch names.
- `atlas pr rebase` is operator-owned and valid only for eligible stale
  same-repository PRs already handed off in `Review Required`.
- Work occurs in a managed detached linked worktree. The primary checkout,
  branch, index, tracked files and local refs remain untouched.
- Conflicts remain for deliberate human resolution. Rerere and autoupdate are
  disabled for every rebase invocation.
- Publication resolves all configured push destinations, requires exactly one
  destination matching the pinned repository identity, and pushes to that
  captured URL with an explicit old-head lease.
- The manifest becomes durable before the remote-write boundary and can
  reconcile an interruption after a successful push without repeating it.
- A changed head invalidates machine evidence, acceptance confirmations,
  manual approval and verification for authority purposes. New-head acceptance
  starts again.
- Merge remains a manual operator action. The final assessment narrows but
  cannot eliminate the residual interval before the GitHub click; the one-PR
  freeze remains binding.

---

## 4. Review findings corrected before closure

Exact-head review changed material safety behaviour before the final PRs were
accepted:

- **ATLAS-228 exact-PR input.** The first head accepted malformed or mismatched
  PR-number values through the assessment service boundary. The accepted head
  requires one positive integer equal to the requested PR and returns a clean
  precondition error otherwise.
- **ATLAS-229 post-push durability.** The first head could complete the remote
  rewrite before persisting a recoverable write-boundary state. The accepted
  lifecycle records `lease_push_pending` before mutation and reconciles old,
  rebased or unexpected remote heads on retry.
- **ATLAS-229 remote identity.** The first head trusted the local `origin`
  alias without proving it represented `--repo`. The second iteration proved
  one URL but missed Git's multi-`pushurl` behaviour. The accepted head resolves
  every push URL, rejects multiplicity or mismatch, and pushes through the one
  captured validated destination.
- **ATLAS-229 manual conflict boundary.** User Git configuration could enable
  rerere and automatically stage a prior resolution. The accepted head disables
  rerere and autoupdate for both initial and continued rebases.
- **ATLAS-230 acceptance gate.** No review finding remained in the submitted
  close-driver change; its seven acceptance criteria matched the implementation
  and deterministic tests.
- **Live current-main resolution.** The operational drill proved that a PR
  payload's `base.sha` can remain pinned to its creation-time base after sibling
  merges. PR #293 made assessment resolve the live base branch head
  independently; PR #294 applied the same correction to the pre-publication
  safety gate. Both defects failed before an unintended remote mutation.

The review loop therefore strengthened the remote-write boundary rather than
merely restating green CI.

---

## 5. Incident and planning-integrity ledger

- **Ticket documentation requirements were malformed.** The Phase 12 stubs
  placed prose in `documentation_requirements`, but verification compares each
  entry by exact repository-relative path. ATLAS-228's documentation check
  correctly remained `PENDING` even though the PR changed both intended docs.
  The stored definitions for ATLAS-228 through ATLAS-230 were surgically
  repaired to exact paths without changing ticket identity or workflow state.
- **The planning path admitted an impossible contract.** `plan --stubs-only`
  and apply did not reject the prose value, allowing a ticket that no touched
  filename could satisfy. Future phase planning must validate path-only fields,
  dependency identity, ordering, cycles and manifest coverage before apply.
- **Closure diagnostics concealed the actionable reason.** The close driver
  reported only that verification was pending while its captured detailed
  report identified documentation as the sole blocker. The verifier was
  correct; the wrapper output was insufficiently diagnostic.
- **Zero confirmation output was ambiguous.** `atlas confirm` printed
  `Recorded 0 operator confirmation(s)` when no decisions remained, prompting
  an unnecessary retry. It should state that no outstanding confirmations
  exist.
- **A stale explanatory note contradicted live evidence.** The OP-A note could
  claim no operator confirmations existed immediately below passed human-tier
  evidence. That prose must be derived from current implementation state or
  removed.
- **The Phase 13–14 planning handoff was invalidated by main movement.** The
  repaired package was prepared before ATLAS-229 and ATLAS-230 merged. It must
  be regenerated from the Phase 12 closure head rather than applied as an
  approximate overlay.
- **The live drill found two historical-base assumptions.** Deterministic
  fakes had returned current `main` in the PR payload, while GitHub retained the
  PR's creation-time `base.sha`. The assessment and publish gates now resolve
  the protected branch head independently, with regression coverage in PRs
  #293 and #294.

None of these incidents weakened the final exact-head or lease protections.
The live failures occurred before the write boundary; the corrections were
merged and retested before the one successful lease-protected publication.
The remaining incidents expose the next control-plane work: validate
mechanically interpreted planning fields at creation and make fail-closed
diagnostics immediately actionable.

---

## 6. Carry-forwards (with owners and scope)

- **Pending/zero-action diagnostics — verification/CLI owner.** Print the
  detailed failed-or-pending check from `close_ticket.py`, replace ambiguous
  `Recorded 0` wording, and retire the stale OP-A explanatory note.
- **Path-field validation — Planning Engine owner.** Reject prose, globs,
  basenames and missing paths in `documentation_requirements` and equivalent
  exact-path fields before a stub can be planned or applied.
- **Phase 13–14 package rebase — planning owner.** Regenerate the design,
  roadmap, manifest and 13 stubs from the merged Phase 12 closure head. Do not
  use the bundle based on `91b26031`.
- **Residual merge interval — operator/platform owner.** Keep the one-PR
  freeze between final assessment and manual merge. Merge queue or automatic
  branch update remains a future platform decision; Atlas gains no merge
  authority here.
- **True Linear-sync timestamp — PM/API owner.** The Phase 11 Overview
  carry-forward remains open: successful no-op and status-only syncs need a
  true successful-tick timestamp rather than a definition-push cursor.
- **Writeable operator surface — Phase 13.** Authentication, actor context,
  idempotent receipts and an append-only action ledger must enter together.
  Phase 12 authorises no UI/API write.
- **Production serving and remote deployment — future design.** Binding,
  origins, authentication and deployment remain one threat-modelled decision.

---

## 7. Critical success criteria — self-assessment

1. **Atlas generates its own backlog through plan/apply with stable identity —
   HELD WITH A VALIDATION DEFECT REPAIRED.** The three tickets were minted by
   the governed flow and reconcile to Linear and merged PRs. Their path-only
   documentation field required a local data repair; the validation gap is
   recorded rather than normalised.
2. **Atlas refuses unverifiable completion — HELD.** ATLAS-228 remained pending
   until its exact documentation contract was repaired. Every accepted head
   required current CI, human confirmation and exact-head verification; no
   stale record was promoted to authority.
3. **Every operational record is traceable to intent — HELD.** ATLAS-228
   through ATLAS-230 resolve to the Phase 12 headings, Linear records, accepted
   heads and merged PRs above. Meta work remains explicitly labelled outside
   the store namespace.
4. **The doc linter keeps canon internally consistent — HELD, WITH STATUS
   CLOSURE LANDED HERE.** Behavioural documentation shipped with each ticket.
   This report, root pointer, delivered roadmap, design status and manifest
   registration repair the final phase-status lag together.
5. **Product work begins only after criteria 1–4 hold — HELD FOR THIS
   PHASE.** Phase 12 strengthens the autonomous-delivery control plane and adds
   no investment-product feature or operator merge authority.

---

## 8. The honest close

Phase 12 does not make stale branches disappear. It makes their treatment
explicit, recoverable and attributable without weakening exact-head evidence.
The operator can distinguish current, behind, diverged, conflicted,
indeterminate and ineligible PRs; preserve a conflicted rebase in an isolated
workspace; publish only through an identity-checked expected-value lease; and
restart acceptance whenever a commit changes.

The phase also demonstrated that safety is broader than the push command. A
remote rewrite needs durable pre-write state, an immutable destination rather
than a mutable alias, protection against user Git automation, and recovery that
distinguishes an old, successfully rewritten or unexpectedly moved branch.
Those gaps were found in review and closed before merge.

The live drill showed why operational proof was load-bearing: deterministic
tests did not model GitHub retaining a PR's historical `base.sha`. Both affected
read paths failed closed, were corrected in independently green PRs, and were
then exercised through one successful expected-value lease, fresh CI and
new-head acceptance.

The remaining weaknesses sit one layer outward. Planning accepted prose where
verification requires paths, and closure output hid the exact check that was
doing its job. Those are control-plane usability and validation defects, now
named with owners. The older Phase 13–14 package is also deliberately rejected
as stale rather than approximated onto a different repository head.

Phase 12 is closed because its bounded implementation is delivered,
integrated, tested, fail-closed and now operationally proven. The next phase can
build governed write actions on top of a stronger mainline and acceptance
boundary.

# Debt Register

A running register of known technical debt.

## Atlas → Linear priority mapping (deferred, ATLAS-42)

`priority` is owned Atlas → Linear (ADR-0006 field ownership) but is **not
synced** in v1. Atlas `priority` is an unconstrained signed integer (the
data-model example uses `10`); Linear `priority` is an inverted 4-value
category enum (`0` = No priority, `1` = Urgent, `2` = High, `3` = Medium,
`4` = Low). There is no honest mapping yet: a clamp to `[0, 4]` would both
lose information and invert meaning. ATLAS-42 therefore drops `priority` from
`OWNED_DEFINITION_FIELDS` and syncs title + description only, exactly as
`labels` is deferred until a `Ticket.labels` field exists.

**To close:** pin Atlas's `priority` convention (range and which direction is
"more urgent"), then add a real translation that respects Linear's inverted
0–4 enum, restore `priority` to `OWNED_DEFINITION_FIELDS`, and assert the
mapping (not a raw value) crosses. Owner: a follow-up on the Phase-4
field-ownership work.

## Non-idempotent issue-create window in the sync push (ATLAS-42 → ATLAS-50)

`sync_tick`'s push creates a Linear issue for a pushable ticket that has no
`external_linear_id`, then immediately commits the returned id back to the
ticket (push-then-stamp, D5). `update_issue` is idempotent, so a crash between
push and stamp is harmless on the update path. `create_issue` is **not**: a
crash after the create but before the id-commit means the next tick re-creates
a duplicate Linear issue. The commit-id-immediately step shrinks the window to
a single statement but does not eliminate it.

**Why it can bite:** unattended frequent ticks (ATLAS-50, the PM scheduler)
multiply the exposure. **To close:** give the create a dedup key — e.g. an
idempotency token derived from the ticket key/id, or a pre-commit of the
intent — so a replayed tick reconciles to the existing issue instead of
minting a second one. Owner: ATLAS-50 (scheduler), where the create path runs
unattended.

## relevant_docs path format: planner emits bare filenames, retriever matches full paths (ATLAS-52)

The documentation retriever (ATLAS-52) matches a ticket's `relevant_docs`
against the corpus by **exact repo-relative path**, and an unmatched entry is
skipped, never a wrong-doc match (D3). The planner is shown documents as
`<document path="docs/.../x.md">` (full repo-relative paths), and
`verification-engine.md` already treats `relevant_docs` as paths — but the
planner's `relevant_docs` field carries no explicit format instruction, and the
data-model ContextPack example still lists bare filenames
(`"technical-architecture.md"`). A bare filename exact-misses the full corpus
path, so such references are silently dropped: until reconciled, `relevant_docs`
contributes nothing in live runs unless the planner happens to emit full paths.
The doc-linter does not catch the example, which is structurally valid JSON.

**To close:** add a sentence to the planner ticket prompt instructing
repo-relative `relevant_docs` paths (matching the `<document path=...>` tag),
and fix the data-model ContextPack example to use full repo-relative paths.
Mechanical trust over inference — the exact-match retriever stays as is; the
planner is tightened at source. Owner: a follow-up on the Phase-5 planner-prompt
and doc-example work.

## Lesson match facets: spec rule 4 vs roadmap wording (ATLAS-53)

`context-renderer.md` rule 4 says lessons match by tag intersection with the
ticket's "tags and **ticket_type**"; the roadmap ATLAS-53 line says
"tag/**component**"; and the `adr_retrieval` precedent matches on "tags +
component". ATLAS-53 implements the **union** (tags + component + ticket_type)
so no signal is lost, but the two document wordings still disagree with each
other and with the implementation.

**To close:** align `context-renderer.md` rule 4 and the roadmap ATLAS-53 line
to a single wording — "tags, component, and ticket_type" — matching the
implemented union. Documentation hygiene; no code change. Owner: a
doc-reconciliation follow-up.

## ContextPack constraints/risks/context have no clean Ticket source (ATLAS-56)

`context-renderer.md` rule 5 lists `constraints`, `risks`, and `context` as
"verbatim from the ticket", but neither `Ticket` nor `ProposalTicket` carries a
`constraints` or `risks` field, and `context` has no ContextPack field and no
"Context" entry in the rendered structure. ATLAS-56's v1 compromise:
`constraints = []` (its section omitted when empty); `risks = ["Risk level:
<risk_level>"]` (a line derived from the enum — echoing, not computing, so
ADR-0005-safe); and `ticket.context` folded into the rendered Objective. So the
structured `constraints`/`risks` fields are effectively unpopulated, and
`context` survives only in the Objective prose.

**To close:** pick one direction. Either add real `constraints`/`risks` fields
to `Ticket` + `ProposalTicket` + the planner (the way ATLAS-127/128 added
tags/component, honouring the `ProposalTicket` "every field required"
invariant), or amend rule 5 and the rendered structure to match the model (drop
`constraints`/`risks`, give `context` a defined home). Settle this before any
pack consumer relies on those structured fields. Owner: a spec-vs-model
reconciliation follow-up.

## Related-ticket one-line objective not rendered (ATLAS-56)

`context-renderer.md` rule 3 renders related tickets as "key, title, status,
one-line objective". The assembler reads title/status off the `project_graph`
ticket node, which carries `key`/`title`/`status` but **not** `objective`, so
ATLAS-56 renders key/title/status only and drops the objective rather than
thread a separate tickets-listing through the otherwise pure builder.

**To close:** either extend `project_graph` ticket nodes to carry a one-line
objective (a small `atlas.dependencies` change, reused everywhere the graph
renders a ticket), or pass an objective-by-key lookup into `build_context_pack`.
Prefer the graph-node enrichment — single source, no extra builder parameter.
Owner: a follow-up on the Phase-5 renderer / dependency graph.

## Phase-4 Leg-1 evidence is promotion write-back, not an external Linear flip (ATLAS-50)

The Phase-4 closure report (§1) records the "status change in Linear reflected in
Atlas within one sync cycle" milestone as PASS on operator-run live evidence of
2026-06-25. The evidence exercised was the **promotion round-trip**: Atlas wrote
`Ready for Agent` to Linear via `promote_ready`, and the next `_pull` read that
state back within the tick, flipping ATLAS-1 to `ready_for_agent` locally. This
drives the real `_pull` path end-to-end, but the state it read back was one
**Atlas itself wrote** — not a status changed by an external actor in Linear. The
milestone's literal wording ("flip a ticket's Linear status, sync, confirm")
describes a human-driven external change; the recorded evidence is the
Atlas→Linear→Atlas direction, morally equivalent for the `_pull` mechanism but
not the literal scenario.

**Why it's minor:** `_pull` is direction-agnostic — it maps whatever Linear state
it reads back to a `TicketStatus`, regardless of who wrote that state. The
promotion write-back and an external flip traverse the identical read-and-map
code, so the risk the external case behaves differently is low.

**To close:** run the literal leg once — in the live sandbox, move one synced
issue to a different mapped Linear status by hand (e.g. In Progress), run
`atlas pm sync --once`, and confirm that ticket's Atlas status reflects the
externally-set state. Then update the §1 Leg-1 row to cite the external-flip
evidence and retire this entry. Owner: a follow-up on the Phase-4 live
verification (`phase-4-live-verification` runbook), ~60 seconds when next on a
Linear-reachable network.

## `atlas evidence` tracebacked on a cold database (ATLAS-130 — RESOLVED #116)

**Resolved** in ATLAS-130 (#116, merged). Kept here for the lesson, not as an open item.

**What happened:** running `atlas evidence pull` from a clean checkout against a
never-migrated database, `ProductRepo.get_by_key(PRODUCT_KEY)` executed
`SELECT ... FROM products` and raised `sqlalchemy.exc.OperationalError: no such
table: products`. That error was not in the command's `except` set (which caught
`MissingGitHubTokenError`, `GitHubAPIError`, and a `None` product), so the raw
traceback escaped — a D7 violation. `list`/`show` shared the exposure via
`EvidenceRepo.list()`/`.get()` → `no such table: evidence`.

**Why the green suite missed it:** every test fixture calls `db.create_all()`
before exercising a command, so the empty/unmigrated-database path was never run.
The bug lived in the gap between the harness (always has a schema) and an
operator's first run (does not) — the canonical "green gates, broken first use"
defect, the kind a live run surfaces and a closed-loop suite cannot. It was in
fact found by the first real run, not by CI.

**Fix (ATLAS-130 / #116):** a shared `except OperationalError` at the
`_evidence_command` dispatch boundary maps a missing-schema error to a clean
`EXIT_PRECONDITION` ("database is not initialised … run the database migrations")
across pull/list/show. The catch is narrow (`IntegrityError` is a different class,
not masked); the broad wrap was chosen consciously to also cover write-time
`no such table: evidence` at `EvidenceRepo.add`.

**Carried forward (still open):** the *bootstrap gap* itself — `pull` needs a
pre-migrated DB and a hand-seeded `ATLAS` product, and no `atlas db init` /
seed command exists yet. ATLAS-130 made the error clean; it did not add a
bootstrap path. Tracked as a Phase-6 carry-forward (closure report §4); owner: a
small follow-up ticket before operator handoff.

Linear identifier / Atlas key homonym: two counters, one namespace (post-rename) — RESOLVED ATLAS-143

**Resolved** in ATLAS-143. Kept here for the lesson, not as an open item.
The pushed Linear title now embeds the Atlas store key (`ATLAS-<n>: <title>`,
`render_definition_title` in `atlas/linear/ownership.py`), and the WORKFLOW.md
PR-title instruction references that embedded prefix instead of
`{{ issue.identifier }}` — so the key the agent copies into its PR title is the
Atlas key by construction, and `parse_close_set` attributes the PR to the right
object. The `_KEY` matcher and the rejected `external_linear_id` join were left
untouched (the fix is upstream, in what the title contains). The seam below is
the historical description of the defect.

The WORKFLOW.md prompt instructs the agent to title its PR with
{{ issue.identifier }} — Linear's per-team identifier, minted by
Linear's own counter. The Atlas key never crosses into Linear: the pushed
issue title is ticket.title bare (OWNED_DEFINITION_FIELDS) and
render_definition_description deliberately excludes external ids. Since the
team-key rename, both counters emit ATLAS-<digits> into one visual
namespace. Pre-rename this failed loudly (ATL-<n> bounced off the mapper's
ATLAS-(\d+)); post-rename it fails silently: a PR titled with Linear's
ATLAS-<n> passes the lint-pr-title gate, resolves in parse_close_set,
and verification then attributes it to whatever Atlas ticket happens to hold
that number in the store — a different ticket, or none. The gate green-lights
provenance pointing at the wrong object.

Why it is deferred safely for now: the current smoke scope uses
hand-created Linear tickets with no Atlas-store counterpart, so there is
nothing to misattribute; the seam only bites when Atlas manages real work
end-to-end.

How it was closed (ATLAS-143): the Atlas key is embedded in the pushed
Linear title at sync (ATLAS-<n>: <title>, composed by
render_definition_title and carried by definition_payload's owned title
field), the WORKFLOW.md PR-title instruction now references that embedded
key instead of {{ issue.identifier }}, and the workflow contract tests were
updated on both sides. Rejected alternative (NOT taken): resolving Linear
identifiers through the external_linear_id join at verification time — it
introduces a second key grammar and breaks the gate-is-the-mapper invariant.

PR #137–#139 / #141 title backfill: keyless until real referents exist

PRs #137–#139 merged with no key or the (ATLAS-NN) placeholder; #141
carries a pre-rename ATL- identifier. The backfill (editing PR titles on
GitHub — metadata only, no history rewrite) was prepared but is deferred: the
Linear tickets those PRs pointed at were test-only and have been deleted, so
any key written into the titles today has no referent — inventing referents
would be worse than the hole. The merged lint-pr-title gate already
prevents any NEW keyless PR from landing, so the debt is bounded to these
four.

To close: once real tickets exist through the pipeline (proposal → apply
mints keys → pm sync), create retroactive tickets for the four PRs' work,
then run the prepared backfill prompt
(agent-prompt-pr-title-backfill.md) with the minted keys in its OP slots,
and verify each title through scripts/check_pr_title.py (exit 0). Owner:
alongside the identifier-seam ticket, before the Atlas-loop smoke's
provenance assertions are trusted.

## Key-namespace burn: ATLAS-111..146 reserved-and-discarded (OP-3a)

Delivered out-of-band work (roadmap entries, PR titles #129–#148, code
docstrings, migration names 0011/0012/0014, tech-debt files) used the
hand-maintained identifier sequence ATLAS-111..146 before any store mint —
the same OP-5(b) smear the Smoke B closure documented at 108–110, which
would otherwise recur at every future `atlas apply` until the counter
cleared 146.

**Correction (2026-07-07, operator: derekrivers):** the ticket counter was
advanced from 110 to 146 via the sanctioned `KeyCounterRepo.reserve`
(monotonic, no-reuse — a discarded reservation is structurally an archived
key, AT-6). No ticket rows were created; no schema changed. The next real
mint is **ATLAS-147**.

Consequences, accepted:

- ATLAS-111..146 are permanently non-store identifiers. Their work's
  provenance remains doc-anchored (roadmap + PR titles + closure reports),
  not store-queryable — the same standing 101–110 already had.
- Linear-native identifiers use the TEAM key prefix (`ATL-<n>` for team
  Atlas-Dev-Test, observed live 2026-07-07: ATL-227..336), a different
  namespace from store keys — numeric collision with `ATLAS-<n>` cannot
  occur. The original entry claimed a shared prefix; that claim was
  reviewer-authored inference, disproven by probe. The embedded-title key
  remains the sole authority at the tracker seam; native numbers never
  enter the store. F-8 rule restated: the counter is the only key
  authority; every other identifier is a different namespace.
- The committed render header (`ticket_key_high_water: 110`) reflects the
  last apply and self-corrects at the next one; renders are never
  hand-edited (ADR-0007).

**To close the pattern (not just the instance):** hand-dispatched work must
stop claiming forward keys. Either mint first (inbox stub → `atlas apply`
→ use the assigned key), or label the PR with an explicitly non-key tag
(the ATLAS-00xM meta-commit convention already models this). The runbook
rule lives in `docs/runbooks/agent-ticket-prompt.md` (landed in the same
change as this entry).
## Terminal-dependency fix shipped unminted (2026-07-08, ATLAS-004M)

terminal-dependency fix shipped unminted — the defect blocked the apply
path that assigns keys; bootstrap exception, not precedent.

## Duplicate mints: ATLAS-149/150 rejected same day (F-4, second instance)

PlanRun 0f8c9eba (2026-07-08) minted ATLAS-149/150 as exact-title
duplicates of ATLAS-147/148: the planner re-emitted the committed inbox
stubs alongside their deterministic promotion. The duplicates carried no
dependency edges, so the diff's dependency section could not surface
them — only the ticket ADD lines could, and the confirm gate was taken
on the reviewer's "safe by construction" advice instead of the eyeball
check the gate exists for. Contributing cause owned by the reviewer;
the operator's gate discipline was talked past, not skipped.

Disposition: board issues marked Canceled 2026-07-08; pulled to
`rejected`, terminal. Two burned keys, on the record.

To close the pattern: promotion-vs-model dedup keyed on source_anchor at
reconciliation (a promotion-injected ticket and a model-emitted ticket
citing the same stub anchor are the same work). Two live reproductions
in hand (this and the declined 2026-07-08 double-emission). Note for the
fix's acceptance criteria: edgeless duplicates are invisible in the
dependency section — the dedup must act at ticket identity, not edges.
Owner: queued inbox stub (next planner batch).

**Pattern closed (2026-07-12, ATLAS-151):** a third reproduction landed
before the fix (ATLAS-155/158 — the fix ticket's own mint: byte-identical
content, identical stub anchors, edges on the promotion side only).
Across the three, duplicates matched or diverged on every candidate
feature, so the operator-ratified design uses positional identity: the
pipeline threads the promote_inbox_stubs-injected index set into
reconcile, which collapses any keyless model ticket sharing a promotion
ticket's anchor, re-points its edges to the survivor, and prints one
COLLAPSE line per absorbed ticket at both gates (spec §4). Closed class:
shared-anchor re-emission (the 155/158 and double-emission shapes).
Boundary, held open deliberately: foreign-anchor re-emission (this
entry's own 149/150 shape) never collides with a promotion anchor and
remains gate-read territory; a near-title collapse heuristic becomes a
stub if a fourth reproduction appears. Owner: operator gate reads until
that stub is minted.

## Meta-label ATLAS-004M doubly assigned

ATLAS-004M was used by PR #155 (key-namespace burn record) and reused by
PR #158 (terminal-dependency fix), which by sequence position should
have been ATLAS-007M and is hereby so designated. The meta counter
resumes at the next unused number (verify against merged PR titles
before each use — this entry exists because that verification was
skipped once). To close the pattern: extend the lint-pr-title check to
reject a reused ATLAS-00xM label against merged history — small code
change, queued for a future inbox stub; eyes are demonstrably not
enough.

## Finding: the parked backlog blocks autonomous promotion (2026-07-08)

Two facts compose: (1) definition-push creates Linear issues in the
Needs Human state, so every pushed ticket is born parked — the
107-ticket park is a birth default, not a decision; (2) the readiness
predicate requires depends_on targets to be strictly `done`
(readiness.py, DEPENDENCY_NOT_DONE). Therefore no ticket depending on
the existing backlog can autonomously promote to Ready for Agent while
the park stands — observed live when ATLAS-147/148 pulled back as
needs_human_decision and could never promote (deps ATLAS-45/46, both
parked). Hand-dispatch is unaffected; Symphony-driven autonomous
dispatch is gated on the OP-3b ruling, which acquires a deadline the
moment autonomous dispatch is wanted. Surgical option now on the table:
operator-attested `done` for the handful of self-evidently delivered
infrastructure tickets that future work depends on (e.g. ATLAS-45/46 —
the deliverable is the running system), leaving the bulk ruling intact.

## updatedAt-since incremental sync deferred (OP-9 follow-up, ATLAS-148)

ATLAS-148 made the tick's pull one batched, project-scoped query
(`ceil(n / 250)` requests) and scoped the comment scan to the active-state
set, which fits the 2,500/hour budget at the default cadence with two
orders of magnitude of headroom. The next refinement — filtering the pull
by `updatedAt` since the last tick (Linear's `issues(filter: {updatedAt:
{gt: ...}})`) so an idle board costs near-zero — was named in the OP-9
stub as a non-goal and is deliberately not started: it needs a persisted
per-tick cursor (new state, new failure modes around missed windows and
clock skew) for a saving the budget no longer requires.

**To close:** adopt only if the request budget tightens again (bigger
board, faster cadence, or more engines sharing the key): add a persisted
high-water `updatedAt` cursor with a full-pull fallback on any gap, and
prove the miss-window behaviour with a fake-clock test. Owner: a future
inbox stub against the PM Engine epic (queued behind need; none while the
post-148 arithmetic holds — that evidence is the ATLAS-148 bound test).

## One-time stub-anchor repair outside planning (2026-07-12, ATLAS-159)

Structural defect found at ATLAS-153 delivery: `promote_inbox_stubs`
anchored each minted ticket to its stub's ACTIVE-inbox path, and apply
retires that stub to `inbox/processed/` — so every stub-minted ticket's
anchor dangled the moment its own apply completed, and gate 4 refused
every later stubs-only echo as `GATE4_UNRESOLVED_ANCHOR`. Sixteen live
tickets carried such anchors: ATLAS-109/110/147-158 (named in the
rendered ticket) plus ATLAS-159/160, whose stubs were retired by the
very apply that minted them (PR #171) — the premise delta ratified at
the plan gate. Eight of the sixteen were frozen (done/rejected, spec
§4), so planning could not repair them and no other sanctioned anchor
writer existed.

Operator ruling (ATLAS-159 plan gate, PR #172): branch (a) — a scoped
ONE-TIME repair outside planning, per the ATLAS-007M bootstrap-exception
precedent. Bootstrap exception, not precedent. The repair is
`scripts/repair_stub_anchors.py`: it verifies every named ticket
fail-closed BEFORE any write (anchor is an active-inbox anchor, its stub
is genuinely retired, the rewritten anchor resolves against committed
`processed/` state), rewrites exactly the sixteen named anchors to their
durable `processed/` spelling in one transaction (`source_anchor` +
`updated_at` only), is idempotent, and never writes `docs/planning/`
(ADR-0007 — the renders self-correct at the next apply). Run once by the
operator against the live store; the script refuses any drift from the
named set.

Forward fix in the same change (unconditional): promotion anchors to the
durable `processed/` path from birth, the anchor index and
`input_doc_shas` cover `processed/`, and apply's AT-5 fresh set collects
it symmetrically — the defect class cannot recur, proven by the
promotion-then-retirement round-trip test. Note: bumping `updated_at` on
the sixteen rows means the next Linear sync tick re-pushes their
definitions once (`updated_at > linear_synced_at`); expected, harmless,
and on the record here.

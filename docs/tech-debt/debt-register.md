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

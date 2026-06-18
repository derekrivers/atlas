# Phase 3.5 Closure Report — Layer Consolidation

Status: CLOSED on the milestone, 2026-06-18. Three tickets merged
(ATLAS-113 #57, ATLAS-114 #58, ATLAS-115 #59), each under both evidence
tiers (agent completion reports corroborated by system-tier CI pinned to
head commits, per ADR-0008). The `planning ⇄ dependencies` import cycle is
**consolidated and broken**, its return is **mechanically prevented**, and
the roadmap now **reflects the delivered build** with a sensor that keeps
it that way.

This was a discovered cleanup phase, not in the original roadmap — added
the way Phase 2.5's 100-series was. Its whole output is one redundant
edge deleted, one look-alike primitive collapsed, two architecture-
fitness sensors installed, and one roadmap omission corrected. It adds no
capability; the single behaviour change is a latent epic-ordering fix,
called out in §1. The reasoning for closing here is in §6.

---

## 1. Milestone evidence

Phase 3.5 has no standalone design doc (§5); its milestone is the union
of the three tickets' falsifiable guards. The phase is met when the cycle
is gone and cannot return, the key-sort has one implementation, and the
roadmap covers what was built — each CI-evidenced:

| Claim | Asserted by | Status |
| --- | --- | --- |
| `dependencies → planning` edge removed | `grep` clean; `dependencies/mermaid` imports `atlas.core.*`, not `planning` (ATLAS-113) | **PASS** (deterministic, CI) |
| Cycle's return mechanically prevented | import-linter `layers` contract KEPT on the tree, BROKEN naming the edge on a seeded `dependencies → planning` import (ATLAS-114) | **PASS** (deterministic, CI) |
| One key-sort primitive | `core/keys.natural_key`; the yaml_io and mermaid duplicates deleted; reconciler's identity-sort correctly retained (§3) | **PASS** (deterministic, CI) |
| Epic keys order numerically | `ATLAS-E1, E2, …, E10` pinned with an E10-spanning render test; seeded wrong-order guard (ATLAS-113) | **PASS** (deterministic, CI) |
| Roadmap reflects delivered reality | ATLAS-108–111 enumerated + Phase 3.5 section added; `enumerate_roadmap_tickets` → 99, pin matches (ATLAS-115) | **PASS** (deterministic, CI) |
| Roadmap drift mechanically prevented | coverage sensor over all `docs/closure/*.md`; BROKEN naming a dropped delivered ticket, empty on the real roadmap (ATLAS-115) | **PASS** (deterministic, CI) |

The whole phase is deterministic — zero model/API calls anywhere — which
is why it was the right work under the live API-billing constraint, as
Phase 3 was.

**Behaviour change (intended):** epic keys (`ATLAS-E<n>`) previously sorted
lexically in YAML renders (E1, E10, E2) because yaml_io's strict grammar
could not parse them. The consolidated `natural_key` uses the loose
grammar and orders them numerically. Unsprung until now only because the
corpus has fewer than ten epics — see §3.

---

## 2. Delivered

| Ticket | Delivered |
| --- | --- |
| ATLAS-113 (#57) | One key-sort primitive in a neutral layer: `core/keys.natural_key` (loose grammar `^([A-Za-z-]+?)-?(\d+)$`, int number, total fallback) and `core/mermaid.escape_label`. Deleted `yaml_io._natural_key`, `planning.mermaid._natural`/`_escape`, and both `_KEY_RE`; repointed every consumer; `dependencies/mermaid` now imports from `core`, removing the cycle edge. Fixed yaml_io's latent epic-ordering bug as a consequence; `reconciler._natural` left untouched (§3). |
| ATLAS-114 (#58) | The first steal-list architecture-fitness sensor: an import-linter `layers` contract (`cli > planning > dependencies > storage > core`) making the spine executable, wired into pre-commit and CI, with the canonical order landed in ARCHITECTURE.md alongside it. `tools` and `__main__` are consumers outside the spine, intentionally unconstrained. No runtime code. |
| ATLAS-115 (#59) | The roadmap made to reflect the build: ATLAS-108–111 enumerated in the Phase 2 block, a new Phase 3.5 section enumerating 113/114/115, the pin bumped 92→99. The second fitness sensor — roadmap-coverage — asserting every ticket referenced in any closure report appears in the roadmap, with a seeded guard. No runtime code. |

---

## 3. The harness ledger — what the phase taught and where it was encoded

- **A look-alike count is not a duplicate count.** The Phase 3 closure
  named "three divergent natural-key helpers." Grounding that claim in
  the code showed it was two true duplicates (yaml_io, mermaid) plus
  `reconciler._natural`, which sorts `DiffEntry.identity` — "key,
  new:&lt;n&gt;, new_epic:&lt;n&gt;, or 'src -> tgt'" — a general identity
  sort over composite strings, a *different primitive by domain*.
  Collapsing all three would have broken dependency and placeholder
  ordering. The save was reading `DiffEntry.identity`, not trusting the
  prose. This extends Phase 3's "verify scope against the code" to
  **verify the shape of a redundancy before deduplicating it.**
- **The grammar was load-bearing.** Epic keys are `ATLAS-E<n>`
  (`EPIC_PREFIX = "ATLAS-E"`). yaml_io's strict `^([A-Za-z]+)-(\d+)$`
  drops them to a lexical fallback (E1, E10, E2); planning/mermaid's
  loose grammar parses them. The survivor *had* to be the loose grammar,
  which means consolidation **fixed** a latent yaml_io ordering bug —
  unsprung only because the corpus has under ten epics. This is the same
  "unsprung by corpus" mechanism as ATLAS-110's copied epic-key example.
  Encoded as an E10-spanning seeded test, because E1–E9 alone cannot
  distinguish lexical from numeric order.
- **A fitness guard must be shown to break.** Both sensors land with a
  seeded-defect test that drives the failure, not just the pass:
  import-linter is reproduced BROKEN on a seeded `dependencies → planning`
  edge naming the exact import; the coverage sensor is reproduced
  reporting `['ATLAS-108']` when that line is dropped. A guard verified
  only on the clean tree is a guard you have not tested.
- **The harness loop ran in the doc-error direction.** The ATLAS-115
  runbook said "106/107 correctly absent"; in the roadmap they were
  *present* as planned-but-unbuilt lines, part of the baseline 92. The
  agent followed the operative instruction (do not add, leave unchanged),
  kept them byte-untouched, and surfaced the wording mismatch rather than
  silently reconciling it. When the doc and the code disagree, the agent
  **flags; it does not reconcile** — and the durable fix is the corrected
  sentence, not a corrected agent.

---

## 4. The phase's defining lesson

**A redundancy is only safe to delete once you have verified what each
copy actually does.** The phase's one near-miss was folding reconciler's
identity-sort into the key-sort on the strength of a "three divergent
helpers" line; the cheapest place that error was caught was reading
`DiffEntry.identity` at the plan gate, before a single edit existed. The
same mechanical core as Phase 3 — ground the plan in the repository, not
the prose — held here against a claim in our own closure report.

---

## 5. The executable design — why this phase has no design doc

Every prior phase was governed by a canonical design doc first. Phase 3.5
deliberately is not, and that is the more faithful application of single-
source-by-deletion. The layer spine's design *is* the import-linter
contract: falsifiable, enforced in CI, and named once in ARCHITECTURE.md.
A prose design doc restating the layer order would be a second source
that drifts from the contract the moment either changes. The roadmap-
coverage invariant is likewise its own test, not a paragraph. Naming the
order once and enforcing it once is the whole design; anything more would
be the redundancy this phase exists to remove.

---

## 6. Why close here

The scope was to break the cycle, prevent its return, collapse the
duplicated key-sort, and make the roadmap reflect the build with a guard.
All four are done, every mutation CI-evidenced, with no discovered scope-
extension — the reconciler identity-sort was correctly identified as a
different primitive and left out, not deferred. The phase is internally
self-consistent under its own new sensor: because ATLAS-115 enumerated
113/114/115 into the roadmap, this closure report may reference them
without tripping the coverage sensor it installed. Closing here is clean.

---

## 7. Carry-forwards (owners and homes)

This phase **resolves** two Phase 3 §7 carry-forwards and **promotes** one
steal-list item; the remaining Phase 3 §7 carry-forwards are unchanged and
stay homed there (not re-copied — that would be the drift this phase
removes).

| Item | Owner / home | Status |
| --- | --- | --- |
| `dependencies → planning` edge + divergent natural-key helpers | Phase 3 §7 → ATLAS-113 | **Resolved** — edge gone, key-sort consolidated to one |
| import-linter / architecture-fitness sensor (steal-list) | Phase 3 §7 steal-list → ATLAS-114 | **Resolved** — promoted to a CI-enforced contract |
| `reconciler._natural` as a third natural-sort impl | This report | Open, benign — a general identity sort, a different domain. Consolidate to a shared neutral primitive only if a *second* general-identity-sort consumer ever appears; no action now |
| AT-7 bar threshold | Open **operator decision** (planning track) | Unchanged by 3.5; gates nothing here |
| Phase 4 charter gate: does the 77-line `pm-engine-and-linear-sync.md` clear the "design doc first" bar | Open **operator decision** before Phase 4 build | Unchanged by 3.5 |
| Remaining steal-list (mutation testing, agent-authored-suppression linters, KB-freshness sensor, dependency-cruiser) and the other Phase 3 §7 items (MODIFY-apply `estimated_effort` invariant, runtime-observability loop) | Phase 3 §7 / steal-list register | Unchanged; promote as prioritised |

---

## 8. Phase 4 readiness

Phase 3.5 cleaned the layering before Phase 4 multiplies it. The PM Engine
will import the readiness predicate and likely the renderer; the import-
linter contract now guards those additions mechanically — a
`planning/pm → dependencies/storage/core` edge is allowed, an inverted one
fails the build. And the roadmap-coverage sensor means Phase 4's
discovered-scope tickets cannot silently go unenumerated the way
ATLAS-108–111 did out of Phase 2.5.

The gating carry-forward for Phase 4 is unchanged: write (or ratify) its
design doc first, and settle whether the existing 77-line spec clears the
charter bar. With the spine clean and guarded, Phase 4 builds delivery
coordination on a layering it can trust not to invert under it.

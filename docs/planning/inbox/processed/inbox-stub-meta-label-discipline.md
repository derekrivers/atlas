---
title: "Meta-label discipline in lint-pr-title: reject reused ATLAS-00xM labels and suggest the next free one"
objective: "A PR titled with an already-merged meta label fails the title gate with a message naming the collision and the next free label, ending both the double-assignment and the guess-the-next-number dance."
context: "ATLAS-004M was assigned twice (#155 and #158 — debt register: 'Meta-label ATLAS-004M doubly assigned'), caught only at branch review; the register routed a lint-pr-title extension as the pattern closure. Separately, three stub-landing PRs (#164, #165, #169) failed the title gate for missing labels and each cost a manual enumerate-merged-titles round to pick the next number. One check closes both: when a PR title carries an ATLAS-00xM-form label, validate it is unused among merged PR titles and, on any title-gate failure where a meta label is plausible (keyless doc/planning-only change), print the next free meta label in the failure message. The check runs where lint-pr-title already runs and uses whatever merged-title source that gate already has access to; if the current check is regex-only with no history access, the plan gate decides the cheapest honest source (e.g. git log of the default branch's merge/squash subjects) rather than adding a network dependency."
ticket_type: "tech_debt"
epic_ref: "ATLAS-E1"
acceptance_criteria:
  - "A PR title bearing a meta label already present in merged history fails the gate with a message naming the colliding PR reference and the next free label; proven against a fixture history containing the real 004M double assignment."
  - "A PR title bearing the next free meta label passes; a real-key title (ATLAS-<digits> without the M) is wholly unaffected — the existing key-form behaviour is byte-identical, proven by the existing tests passing unmodified."
  - "A title-gate failure on a label-less title includes the next-free-label suggestion in its message."
  - "Seeded regression: pre-fix, the 004M-reuse fixture passes the gate; post-fix it fails."
  - "The debt-register 'Meta-label ATLAS-004M doubly assigned' entry's pattern-closure line is updated to point at this ticket (deletion over annotation for its 'queued' clause)."
non_goals:
  - "No change to real-key (ATLAS-<digits>) validation, the (KEY) title convention, or any CI wiring beyond the existing title-gate invocation."
  - "No retroactive relabeling of merged PRs — the 004M/007M designation in the register stands as the historical record."
test_requirements:
  - "Unit tests over fixture title histories (no live GitHub calls, ATLAS_LIVE_TESTS=0); seeds `assert 1 == 2` (B011); the 004M shape and the #169-style missing-label shape are named fixtures."
definition_of_done:
  - "All acceptance criteria evidenced by named tests; full gate sweep green; debt-register pointer updated in the same change."
---

# Meta-label discipline

The gate that knows the history should also share it.

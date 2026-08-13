---
title: "Protected integration-lane classification and admission holds"
objective: >-
  Prevent available worker slots from dispatching tickets that compete for the
  same high-contention repository surfaces by applying deterministic protected
  integration lanes before admission.
context: >-
  Phase 15 component and risk lanes bound broad delivery classes, but repeated
  conflicts cluster around migrations, generated contracts, workflow files,
  planning sources, shared manifests and other single-writer surfaces. Phase
  15.5 adds an explicit repository-owned lane registry and ticket
  classification. Lane saturation produces a typed hold even when working and
  Symphony capacity remain available.
ticket_type: feature
epic_ref: ATLAS-E6
risk_level: critical
component: delivery-control
tags:
  - phase-15-5
  - admission
  - integration
  - conflict-control
relevant_docs:
  - "docs/atlas/dependency-engine.md"
  - "docs/atlas/multi-agent-delivery-control.md"
  - "docs/atlas/parallel-delivery-efficiency-and-integration-control.md"
  - "docs/atlas/pm-engine-and-linear-sync.md"
  - "docs/runbooks/pr-acceptance.md"
depends_on:
  - "inbox-stub-02-ci-pending-capacity.md"
  - "ATLAS-248"
  - "ATLAS-249"
acceptance_criteria:
  - >-
    A versioned deterministic registry defines bounded protected lanes for at
    least migrations, generated API/client contracts, workflow configuration,
    planning sources/manifests and explicitly operator-declared hotspots.
  - >-
    Candidate classification uses trusted ticket component/tags and canonical
    declared paths only, records every matched lane and fails closed on
    ambiguous or contradictory declarations without inspecting model prose.
  - >-
    Working and CI-pending tickets consume all matching protected lanes; a
    saturated lane yields a complete typed hold reason before a candidate can
    be selected for the single external admission write.
  - >-
    Stable ranking remains unchanged among feasible candidates, and a lower
    candidate cannot bypass the selected higher-ranked candidate merely because
    it occupies a different lane in the same evaluation.
  - >-
    Lane-registry or active-surface movement between selection and external
    write invalidates revalidation and admits nobody.
  - >-
    The registry and holds perform no GitHub diff mutation, Git rebase, ticket
    demotion, worker cancellation, policy optimisation or automatic widening
    of lane capacity.
non_goals:
  - >-
    No semantic merge-conflict prediction, LLM path inference, dynamic code
    ownership, automatic rebasing, merge queue or proof that different lanes
    are semantically independent.
test_requirements:
  - >-
    Table/property tests cover every protected lane, multi-lane tickets,
    saturation, ambiguity, order-independent fingerprints, ranking interaction
    and stale pre-write revalidation.
  - >-
    Seeded admission tests prove zero unintended promotion and no change to the
    existing one-write lease/fence boundary.
implementation_notes:
  - >-
    Prefer explicit ticket metadata over file-path guesses before dispatch.
    Protected lanes are conservative coordination controls; they complement
    dependencies and never claim semantic independence.
documentation_requirements:
  - "docs/atlas/dependency-engine.md"
  - "docs/atlas/multi-agent-delivery-control.md"
  - "docs/atlas/parallel-delivery-efficiency-and-integration-control.md"
  - "docs/atlas/pm-engine-and-linear-sync.md"
definition_of_done:
  - >-
    All six criteria have named deterministic/property evidence, protected
    surfaces are operator-visible and fail closed, focused gates pass and the
    PR title carries the minted ticket key.
---

# Protected integration-lane classification and admission holds

Free workers do not make a shared file free.

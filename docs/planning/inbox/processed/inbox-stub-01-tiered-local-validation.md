---
title: "Tiered local-validation contract and deterministic validation-plan CLI"
objective: >-
  Replace mandatory per-agent repository-wide sweeps with a deterministic,
  reviewable validation plan that selects the smallest safe local checks for
  the changed surfaces while keeping complete CI authoritative.
context: >-
  Atlas currently instructs every agent to reproduce the complete Python and
  Operator UI CI matrix before handoff. Under concurrent delivery this repeats
  expensive work, occupies Symphony turns and obscures whether implementation
  or validation is the bottleneck. The new contract follows local confidence,
  CI authority: agents run ticket-required and affected checks locally; CI
  still runs every required repository gate. Unknown, cross-cutting or
  protected changes fall back conservatively to a complete local sweep.
ticket_type: infrastructure
epic_ref: ATLAS-E9
risk_level: high
component: verification
tags:
  - phase-15-5
  - validation
  - ci
  - developer-workflow
relevant_docs:
  - "AGENTS.md"
  - "WORKFLOW.md"
  - "docs/atlas/evidence-pipeline.md"
  - "docs/atlas/parallel-delivery-efficiency-and-integration-control.md"
  - "docs/runbooks/agent-ticket-prompt.md"
  - "docs/runbooks/local-development.md"
depends_on:
  - "ATLAS-252"
acceptance_criteria:
  - >-
    A versioned deterministic registry maps repository paths and ticket-declared
    requirements to Python, static, documentation, schema, generated-client,
    UI, browser and full-sweep validation profiles with explicit reasons.
  - >-
    A CLI accepts an exact base and head/diff, emits the ordered local commands,
    protected-surface reasons and full-sweep fallback as bounded JSON and
    human-readable output, and performs no repository or external mutation.
  - >-
    Changed test files, tests added for changed behaviour and every explicit
    ticket test requirement are always included; a caller cannot suppress a
    mandatory profile through an untrusted path or free-form exclusion.
  - >-
    Unknown paths, validation-registry drift, ambiguous base identity and
    protected cross-cutting surfaces fail conservatively to the documented
    complete local sweep rather than returning an incomplete plan.
  - >-
    Identical repository identities and changed paths produce byte-stable plans
    independent of input ordering, local clock, UUIDs or model interpretation.
  - >-
    Canonical docs state that scoped local evidence is agent-tier confidence,
    while the complete CI matrix at the accepted identity remains the
    system-tier completion authority.
non_goals:
  - >-
    No learned test-impact prediction, CI job removal, test skipping inside CI,
    automatic completion, model judgement or claim that scoped checks prove the
    repository-wide result.
test_requirements:
  - >-
    Table-driven tests cover each profile, combined surfaces, test-file
    inclusion, explicit ticket requirements, unknown/protected fallback and
    order-independent output.
  - >-
    Mutation and architecture tests prove plan calculation performs no Git,
    GitHub, Linear, Symphony, database or filesystem write.
implementation_notes:
  - >-
    Prefer a small declarative registry and pure classifier. Full local
    validation remains available as an explicit or conservative profile; the
    optimisation changes the default development loop, never CI authority.
documentation_requirements:
  - "AGENTS.md"
  - "docs/atlas/parallel-delivery-efficiency-and-integration-control.md"
  - "docs/runbooks/agent-ticket-prompt.md"
  - "docs/runbooks/local-development.md"
definition_of_done:
  - >-
    All six criteria have named deterministic tests, the CLI produces stable
    plans, documentation clearly separates local confidence from CI authority,
    focused gates pass and the PR title carries the minted ticket key.
---

# Tiered local-validation contract and deterministic validation-plan CLI

Agents validate the change they made; CI validates the repository that will merge.

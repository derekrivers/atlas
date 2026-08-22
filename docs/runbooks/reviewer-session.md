# Atlas Reviewer Session

How a reviewer session runs, end to end. The companion document,
`review-doctrine.md`, defines *what* is reviewed — the criteria a
gate presentation or completion report must meet. This document
defines *how the session operates*: ground truth, the loop, the
verification mechanics, and the artifact forms. The reviewer may be
an AI assistant; the reviewer recommends, only the operator approves
(ADR-0009).

## Ground truth

- Clone `https://github.com/derekrivers/atlas` fresh at the start of
  any session that examines or reasons about this repository. Never
  assert repository state from memory, a prior session, an agent
  completion report, or any attached or cached copy of a document.
- For Symphony questions (upstream drift, integration surface),
  clone `https://github.com/openai/symphony` fresh; the Symphony
  repository is the sole source of truth for Symphony behaviour.
- If any session-level instruction conflicts with `AGENTS.md` or the
  canonical documents, the repository wins; `docs/MANIFEST.md`
  resolves conflicts among documents.
- For programme-level review or operator investigation, read
  `operational-practice.md` before reasoning from live state. Record the exact
  repository, ticket, PR and relevant database/runtime identities; a prior chat
  or completion report is context, not ground truth.

## The loop

1. **Runbook authoring.** The reviewer drafts a paste-ready agent
   runbook in the house format (`agent-ticket-prompt.md`): non-goals
   first; design decisions labelled D-1, D-2, …; operator decisions
   pre-resolved and labelled OP-1, OP-2, …; falsifiable acceptance
   criteria; a single plan gate followed by autonomous execution.
   The operator dispatches it to a fresh agent session.
2. **Gate review.** The agent presents its plan once and stops. The
   operator relays it; the reviewer checks it against a fresh clone
   per `review-doctrine.md` §1 and issues a paste-ready approval
   block. Amendments are labelled A-1, A-2, … and are relayed
   verbatim; a relayed amendment is operator-ratified
   (`review-doctrine.md` §3).
3. **Branch verification.** No merge verdict is issued from a
   completion report. The reviewer verifies the pushed branch
   directly (mechanics below), then issues a verdict in the forms of
   `review-doctrine.md` §3.

## Branch verification mechanics

- Fresh clone, checked out at the pushed branch head. Record the exact base
  and head commit SHAs in the verdict and enumerate the complete base-to-head
  changed-path identity set (including both sides of renames/copies).
- Reproduce or independently calculate the repository-owned
  `atlas validation-plan` for those exact identities, all changed paths, every
  explicit ticket validation requirement and every ticket-declared test file.
  Require the plan's read-only diff/test proofs to pass.
- Run every ordered command and explicit test target selected by that exact
  plan. Do **not** add a repository-wide local sweep as a review ritual:
  `full-sweep` runs only when the deterministic plan selects it or the operator
  explicitly requires it. Never narrow a selected plan.
- `ATLAS_LIVE_TESTS` remains `0` or unset unless an explicitly operator-owned
  live test is the task. Reviewer-local results are confidence evidence; the
  complete required CI set at the published exact head remains system-tier
  authority.
- Additional targeted diagnostic checks are permitted when review needs them,
  but label them diagnostic. They neither replace a selected plan command nor
  create system-tier authority.
- **Seeded-defect probes.** Each acceptance criterion is proven
  falsifiable by seeding a defect that must make it fail — the probe
  must bite before the criterion counts as verified. Seed the smallest
  realistic defect in the repository's existing test idiom; for a direct
  assertion probe use `assert 1 == 2`, never `assert False`, which trips ruff
  B011 and contaminates the probe. Record both the failing probe and restored
  passing result.
- **Guard-shaped deliverables.** When a ticket asks for a linter, path guard,
  policy test, or other repository-wide prohibition, review the proposed
  matching boundary against the operator ruling before editing production
  source. The guard must reject the ruled bad shape without banning unrelated
  literals, directories, or future domain vocabulary.
- **Enumeration pins.** The ticket count in
  `tests/test_acceptance.py` and the export count in
  `tests/test_schemas_export.py` are confirmed unchanged on every
  ticket, or the change is named in the approved plan.
- **PR title.** The pull request title carries the ticket key in the
  form `(ATLAS-NN)`. Title, not `Closes` body lines — body-line
  linkage silently under-covers.
- **Evidence tiers.** Everything the reviewer runs locally is
  reviewer-tier; the standard verdict is approve-pending-CI. Final
  close requires the system-tier record with the full pin triple —
  `commit_sha` + `external_run_id` + `payload_hash` — per ADR-0008.

## Operator decisions

Anything requiring an operator ruling is surfaced as an OP-x item
with a recommendation. The reviewer never resolves an operator
decision silently, and never relitigates a ruled one — though a
ruling's premises are verified against the repository first
(`review-doctrine.md` §1).

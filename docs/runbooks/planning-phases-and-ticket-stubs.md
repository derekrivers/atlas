# Planning phases and ticket stubs

Operator and contributor runbook for turning an approved Atlas phase into
committed planning inputs. It defines the repository artifacts, Atlas-owned
integrity gate and operator boundary before `atlas plan --stubs-only`.

## Outcome and authority

One phase-planning change leaves Atlas with:

- one canonical design document per phase;
- aligned root and implementation roadmaps;
- aligned `docs/MANIFEST.md`;
- one ordered inbox stub per planned ticket;
- one committed planning-batch manifest;
- a dependency DAG with genuine parallel lanes; and
- no minted ticket keys or hand-edited planning renders.

The planning commit defines intent. Atlas validates the committed batch, then
the operator separately reviews `atlas plan --stubs-only` and confirms
`atlas apply`. Only apply assigns keys, mutates the Atlas store, writes the four
planning renders and retires the considered stubs and batch manifest.

## Before the batch: ratify decisions and boundaries

Do not use stub authoring to hide unresolved programme decisions. Before
constructing the batch, review the owning phase/design document and make every
material operator decision explicit. A major architectural choice still open
inside an implementation stub means the phase is not ready to mint unless that
ticket's bounded purpose is specifically to present the named decision for
operator ratification.

Review the proposed phase as a system before reducing it to stubs:

- each intended capability appears once;
- dependencies express technical prerequisites, not preferred human order;
- genuinely independent work has a parallel lane rather than an accidental
  serial chain;
- milestone/release tickets are held behind the evidence that authorises them;
- no two tickets independently claim ownership of the same schema, policy,
  runtime or state transition; and
- each ticket is small enough to reach one independently reviewable candidate.

Repeated agent runs around an hour or more are a planning signal, especially
when time is dominated by broad validation, conflict recovery or repeated
context reconstruction. Investigate whether the ticket contains separable
contracts before increasing turns or weakening validation. This is not a hard
wall-clock limit: split only at a real independent review, authority or
behaviour boundary.

## External handoff package

Every external planning handoff is one ZIP containing:

```text
README.md
VSCODE_AGENT_INSTRUCTIONS.md
PHASE_PLANNING_RUNBOOK.md
bundle-manifest.yaml
repository-files/
  ROADMAP.md
  docs/MANIFEST.md
  docs/atlas/implementation-roadmap.md
  docs/atlas/<phase-design>.md
  docs/runbooks/planning-phases-and-ticket-stubs.md
  docs/planning/inbox/planning-batch-<slug>.yaml
  docs/planning/inbox/inbox-stub-NN-<slug>.md
validation/
  validate_phase_bundle.py
  VALIDATION_REPORT.md
```

`repository-files/` is the complete overlay. The root bundle manifest records
the exact Atlas base commit, every overlay file, every stub and every
intentionally future-created documentation path. An identical manifest is
committed under `docs/planning/inbox/planning-batch-<slug>.yaml`; that copy is
the runtime contract Atlas validates and later retires.

## Planning-batch manifest

The committed manifest uses schema version 1 and contains at least:

```yaml
schema_version: 1
repository: "derekrivers/atlas"
base_commit: "<40-character exact base SHA>"
repository_files:
  - "ROADMAP.md"
  - "docs/planning/inbox/planning-batch-phase-13-15.yaml"
  - "docs/planning/inbox/inbox-stub-01-example.md"
future_document_paths:
  - "docs/closure/phase-13-closure-report.md"
stubs:
  - path: "docs/planning/inbox/inbox-stub-01-example.md"
    phase: 13
```

Before a PlanRun is persisted, Atlas proves:

- `base_commit` is an ancestor of HEAD;
- `git diff --name-only <base_commit>..HEAD` exactly equals
  `repository_files`;
- every listed repository file exists at HEAD;
- the manifest lists itself;
- the ordered `stubs` list exactly equals the active inbox; and
- every future document path is an exact repository-relative Markdown path.

A moved base, additional commit, omitted overlay file or unlisted stub fails
closed. Regenerate or rebase the planning batch; do not edit the manifest to
hide unrelated movement.

### Interstitial phases and in-flight prerequisites

An interstitial identifier such as `15.5` is a semantic phase label, not a
ticket-key namespace. It may appear in the roadmap, design, batch manifest and
stub `phase` field when the work is a bounded prerequisite correction to an
in-flight phase. It does not renumber the surrounding programme or authorise a
second implementation path.

Dependencies on already minted in-flight tickets use their existing
`ATLAS-N` keys. New stubs may depend on those tickets, but planning inputs may
not rewrite an existing ticket to depend on a future stub. When an existing
milestone must wait for the interstitial phase, canonical designs record an
explicit operator release gate and the ticket remains in a human-held state
until the interstitial closure is merged. The operator verifies that state
before applying the planning batch and again before releasing the milestone.

## Stub schema

Use contiguous ordered filenames:

```text
inbox-stub-01-<slug>.md
inbox-stub-02-<slug>.md
```

Every stub carries top-of-file YAML front matter with:

- `title`, `objective`, `context`;
- `ticket_type`, `epic_ref`, `risk_level`, `component`, `tags`;
- `relevant_docs`, `depends_on`;
- one to seven `acceptance_criteria`;
- `non_goals`, `test_requirements`, `implementation_notes`;
- `documentation_requirements`; and
- `definition_of_done`.

Do not add a ticket key, Linear ID, status, actor or timestamp. `epic_ref` must
name an existing epic. Each dependency is either an existing `ATLAS-N` key or
the exact basename of a sibling stub. Sibling dependencies point only to a
lower-numbered stub.

## Exact-path fields

`documentation_requirements` and `relevant_docs` contain exact
repository-relative Markdown paths only:

```yaml
documentation_requirements:
  - "docs/atlas/operator-api.md"
  - "docs/runbooks/pr-acceptance.md"
```

Do not use prose, surrounding whitespace, globs, directory prefixes,
backslashes, absolute paths or traversal. Explanations belong in acceptance
criteria or implementation notes.

Every `relevant_docs` path must exist at HEAD. A
`documentation_requirements` path must exist at HEAD or be declared in the
batch manifest's `future_document_paths`. Atlas verification compares these
values with exact CI-reported filenames; it does not expand or interpret them.

## Atlas-owned integrity gate

Both generative planning and `--stubs-only` run the same integrity guard before
proposal generation/promotion can persist a PlanRun. Atlas validates:

- exact-path syntax and existence;
- existing ticket and sibling dependency identity;
- contiguous ordered filenames for phase batches;
- backward sibling dependency order;
- dependency acyclicity; and
- exact batch-manifest coverage.

`atlas apply` reads the same committed inputs, checks PlanRun staleness and
runs the integrity guard again before presenting the confirmation gate. This
prevents a pre-repair or otherwise impossible proposal from reaching the
store merely because it already exists.

Ordinary PM follow-up stubs with unnumbered names remain manifest-free. They
still receive the exact-path, dependency identity, deterministic backward-order
and cycle checks. Ordered `inbox-stub-NN-*.md` phase batches cannot omit their
manifest or mix with unnumbered follow-up stubs.

## Ticket quality

Each ticket is independently reviewable and small enough for one delivery
lane. Its criteria describe observable success, failure and race behaviour,
prohibited mutations, focused deterministic tests and exact documentation
scope. Seven criteria are a ceiling, not a target. Dependencies express real
technical prerequisites rather than a preferred human sequence.

Before accepting the batch, ask of every stub:

- Is there one coherent objective or several independently shippable ones?
- Can its acceptance criteria be falsified without depending on a sibling's
  implementation?
- Are non-goals strong enough to stop an agent expanding into adjacent work?
- Does the ticket own a bounded production surface and its matching docs/tests?
- Would a semantic conflict require an operator decision that should have been
  made before minting?
- Does the dependency graph allow safe parallelism where the design does?

If several answers are unclear, refine the stub before it receives an Atlas key;
key assignment is not the time to discover that the ticket boundary is wrong.

## Validation and commit boundary

Before delivery and before the local planning commit, run the packaged
validator and:

```bash
git diff --check
```

Review the complete diff. Only manifest-listed paths may change. Commit the
planning inputs, then run the repository documentation linter against the
committed corpus:

```bash
uv run python -m atlas.tools.doc_linter
```

The local agent stops after the planning-input commit. It must not run
`atlas plan`, `atlas apply`, edit generated planning renders, implement a
ticket, push a branch or open a PR.

## Operator continuation

After the planning-input PR is reviewed and merged, the operator runs:

```bash
uv run atlas plan --stubs-only
uv run atlas apply
```

Inspect the diff before confirmation. Expected ADD count must match the
approved stub count, and an unexpected MODIFY, PROPOSE_ARCHIVE or CONFLICT is a
stop, not something to accept because the stubs themselves looked correct.

Immediately after a successful apply, before unrelated work:

```bash
git status --short
head -n 6 docs/planning/tickets.yaml
```

Verify the monotonic key range/high-water, the resolved dependency edges, all
four apply-owned renders, and every consumed stub plus the batch manifest under
`docs/planning/inbox/processed/`. Then stage the complete planning tree,
including deletions and newly created processed files:

```bash
git add -A docs/planning/
```

Commit and publish those apply artifacts before resetting, switching away or
running another mint. The store has already advanced; discarding the working
tree can leave committed renders/stub retirement behind operational state and
later cause duplicate promotion or broken context anchors.

Minting into Atlas and publishing to Linear are separate boundaries. After the
apply-artifact change is merged, verify the next PM sync:

```bash
uv run atlas pm sync --once -v
```

First sync creates the Linear issue for a pushable minted ticket, records its
`external_linear_id`, pushes the Atlas-owned definition/context and asserts the
mapped create-time state. A failure there is a sync incident; do **not** re-run
`atlas apply` to repair it.

If Atlas reports an integrity failure, stop. Correct the planning inputs,
regenerate the complete package against current main and repeat validation;
never bypass the guard or surgically alter the operational store.

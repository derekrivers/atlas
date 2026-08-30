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

## Governed ticket-minting lifecycle

Ticket minting is a sequence of authority handoffs. Completing one boundary
never grants the next owner permission to act, and a procedural skill may
assist an owner without acquiring that owner's authority.

| Boundary | Owner | Permitted transition | Required stop |
| --- | --- | --- | --- |
| Ratified intent | Operator, through the owning canonical design or decision | Approved intent becomes the semantic source for one bounded planning package. | Stop while a material decision, ticket boundary or state-edge owner is unresolved. |
| Planning-input preparation | Planning contributor | Create or correct the exact canonical files, ordered inbox stubs and batch manifest; validate and commit them without assigning keys. | Stop on any schema, exact-path, dependency, manifest or base-to-HEAD mismatch. A local planning agent stops after the input commit and cannot plan, apply or publish it. |
| Planning-input repository admission | Repository reviewer recommends; operator admits | The reviewer issues a verdict on the exact committed planning package; only the operator may approve and merge it, making it eligible for the operator planning path. | Stop on an unratified semantic choice, incomplete manifest, validation failure, unexpected diff or stale base. A reviewer never acquires merge authority. |
| Optional read-only preflight | Planning Engine | Inspect the committed package through the shared deterministic planning primitives described in `planning-engine-specification.md`; return diagnostics only. | Stop on any diagnostic. Preflight cannot produce a reconciliation diff, persist a `PlanRun`, assign a key, retire an input or mutate the store or working tree. |
| Proposal construction | Planning Engine, invoked by the operator | `atlas plan --stubs-only` builds the deterministic backlog echo plus promoted stubs, runs the applicable gates and persists one `proposed` `PlanRun`. | Stop unless ticket ADD count equals approved stub count, dependency ADDs exactly equal the approved graph, and every other entity type and diff result is expected. Aggregate ADD is informational composition, not ticket-count authority. |
| Exact-proposal approval | Operator | Review the complete typed proposal at its pinned inputs and either approve that exact proposal for apply or reject it. | Any unexpected ticket, dependency, epic, other entity type, MODIFY, PROPOSE_ARCHIVE, CONFLICT, collapse, identity, provenance or integrity result stops approval. |
| Key assignment and apply | `atlas apply`, after explicit operator confirmation | Assign monotonic keys, atomically mutate the Atlas store, write the four planning renders and retire the considered stubs and manifest. | A stale, changed or rejected proposal stops. An already-applied `PlanRun` is never applied again, including to repair a later publication incident. If an active PM cadence observes the selected store and no governed quiescence spans apply through repository admission, stop before confirmation. |
| Apply-artifact candidate publication | Repository contributor | Commit, validate and publish the complete apply-owned render and retirement change as one exact repository candidate. | Stop on an incomplete planning tree, unexpected repository diff or failed validation. Never discard the advanced-store artifacts or hand-edit their projection. |
| Apply-artifact repository admission | Repository reviewer recommends; operator admits | The reviewer issues a verdict on the exact apply-artifact candidate; only the operator may approve and merge it under the normal repository controls. | Stop on failed review, changed identity or unpublished/unmerged apply artifacts; PM publication cannot start, and a reviewer never acquires merge authority. |
| Linear publication and reconciliation | Atlas PM Engine, within the PM field and state-edge ownership table | On its governed cadence, create or update the Linear issue, persist/reuse `external_linear_id`, push Atlas-owned definition/context and assert the mapped current Atlas state on first creation. | Any missing or ambiguous join, failed create/update/assertion or reconciliation anomaly is a PM incident. Do not re-run apply and do not substitute raw Linear issue creation or workflow mutation. |
| Delivery admission | Atlas PM Engine | Under the active operator-owned delivery policy, independently select at most one eligible ticket and promote its existing Linear issue to `ready_for_agent` through the lease, revalidation and write-fence protocol. | Publication, reconciliation or create-time assertion alone grants no admission. Mint-only or publish-only intent has no admission side effect: use a separately governed publication-only seam, or fail closed when the available runtime path could continue into admission. |

The ordinary `atlas pm sync` tick currently contains both the publication pass
and the later admission pass. It is therefore not permission-safe for a
mint-only or publish-only instruction merely because publication occurs first.
The operator either invokes a separately implemented publication-only seam or
stops; procedure wording cannot narrow a runtime path that still has admission
authority.

Repository admission is an authority precondition, not an executable fence on
the current managed cadence. `atlas apply` commits the store before its render
and retirement artifacts can be reviewed and merged, so an active PM process
watching that store could observe the minted tickets too early. No
planning-specific quiescence-and-resume lane is active. The operator therefore
MUST stop before confirming apply against a store observed by an active PM
cadence; ad hoc service control and `atlas pm sync --once` are not substitutes.
This ticket records the boundary and stop condition but activates no runtime
fence, service operation or publication-only path.

## Unexpected-stop disposition

Before retry or closure, every unexpected stop in planning-input preparation,
packaged validation, plan, apply, apply-artifact repository publication or PM
publication receives one explicit disposition. `Retry` alone is not a
disposition. Classify the stop as exactly the applicable kind below and record
the evidence that supports that classification:

- **input, schema or preflight defect** — correct the governed planning package
  and name the stub, manifest or canonical input that absorbs the correction;
- **skill or procedure defect** — correct the owning repository skill or
  runbook through a separately governed change;
- **runtime or code defect** — name the implementation and test surface that
  owns the repair;
- **governance or authority defect** — name the canonical design, runbook or
  ADR that must be ratified before work resumes;
- **operational recovery or troubleshooting finding** — name the owning
  operational runbook or `docs/runbooks/troubleshooting.md` section;
- **reusable Atlas lesson** — record it through the governed Lesson lifecycle,
  remaining DRAFT until operator promotion;
- **delivery or debt follow-up** — record the owning `DebtItem` or governed
  follow-up planning input rather than creating a tracker ticket directly; or
- **proven transient external or system event requiring no product change** —
  retain the bounded provider/system evidence and explain why no durable Atlas
  surface needs correction.

Every durable finding names the repository or Atlas surface that will absorb
it. If a pre-key failure exposes an understood defect in the still-unminted
batch, correct the owning planned ticket before minting; do not knowingly mint
the defect and defer it to a follow-up. If that correction genuinely needs an
additional canonical repository path in the same package, the manifest may
expand only under the original-base and complete base-to-HEAD equality rules
below. Unrelated paths cannot be absorbed merely to make validation pass.

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

A durable pre-key finding may legitimately expand the governed planning
package with a newly required canonical or repository path. When it does,
retain the original base if it remains valid, add the path to
`repository_files`, and re-prove equality with the complete new
`base_commit..HEAD` overlay. Do not preserve an obsolete path inventory
mechanically or re-pin the base merely to hide a same-batch correction;
unrelated paths still fail closed.

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

Inspect the diff by entity type before confirmation. For an ordered stubs-only
batch, the ticket ADD count must equal the approved stub count and the
dependency ADD set must exactly equal the approved declared dependency graph.
The aggregate `ADD` summary counts every added diff entry, including dependency
edges, so it may exceed the number of stubs and must not be compared directly
with the stub count. Any unexpected ticket, dependency, epic or other added
entity type is a stop, as is any unexpected MODIFY, PROPOSE_ARCHIVE, CONFLICT,
collapse, identity, provenance or integrity result.

Expected typed counts come from the ratified batch, never from a fixed aggregate
allowance. For example, this programme's thirteen approved stubs and twenty-four
declared edges produce thirteen ticket ADDs plus twenty-four dependency ADDs —
thirty-seven aggregate ADD entries — only when every typed entry matches the
approved batch.

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
apply-artifact change is merged, the next PM action remains owned by the
governed managed cadence and its runtime procedure. Do not run
`atlas pm sync --once`: on the canonical store it normally contends with the
managed writer, and when it can run it executes the same admission-capable tick
rather than a publication-only seam. If the planning lifecycle stopped because
no governed quiescence-and-resume lane exists, repository merge alone does not
authorise ad hoc service control; retain the explicit governance/operational
stop until that lane is separately delivered.

When the governed cadence is lawfully active, its first sync creates the Linear
issue for a pushable minted ticket, records its `external_linear_id`, pushes the
Atlas-owned definition/context and asserts the mapped create-time state. A
failure there is a sync incident; do **not** re-run `atlas apply` to repair it.

If Atlas reports an integrity failure, stop. Correct the planning inputs,
regenerate the complete package against current main and repeat validation;
never bypass the guard or surgically alter the operational store.

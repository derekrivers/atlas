# Atlas Operational Practice

Operational handbook for running Atlas as a real delivery system. This document
captures the operator craft that sits between the formal contracts: how a fresh
session establishes truth, how a phase becomes safely minted work, how reviews
and CI are interpreted, how incidents are diagnosed, and where newly learned
operational knowledge belongs.

This is an index and practice guide, not a competing source of truth. When a
specialist document owns a behaviour, that document wins. In particular:

- `docs/MANIFEST.md` resolves canonical-document authority;
- `docs/atlas/planning-engine-specification.md` and
  `docs/runbooks/planning-phases-and-ticket-stubs.md` own planning and minting
  contracts;
- `WORKFLOW.md` and `docs/atlas/symphony-integration.md` own dispatched-agent
  behaviour and state-edge ownership;
- `docs/runbooks/pr-acceptance.md` owns the acceptance sequence;
- `docs/runbooks/operator-environment.md` owns environment-specific facts and
  credential hazards; and
- `docs/runbooks/troubleshooting.md` owns symptom-specific recovery guidance.

The operating principle is simple: **do not carry critical Atlas knowledge only
in a human or AI conversation.** A fresh operator or agent should be able to
reconstruct the intended operating model from the repository, then read live
state from the system that owns it.

## 1. Start every serious session by re-establishing truth

Do not begin a programme investigation, review, milestone observation, or
production diagnosis from remembered state. Establish the identities that make
the claim meaningful.

From the checkout being used:

```bash
git rev-parse --show-toplevel
git remote get-url origin
git symbolic-ref --quiet --short HEAD || true
git rev-parse HEAD
git status --short
```

For work that depends on current `main`, also fetch it and record its exact
identity before reasoning about freshness:

```bash
git fetch origin main
git rev-parse origin/main
```

Then establish the task context:

1. Read `AGENTS.md` and the relevant entries in `docs/MANIFEST.md`.
2. Read this handbook for programme/operator work.
3. Read the current ticket definition from the tracker; the `ATLAS-N` key in
   the ticket title is the Atlas identity, not the `ATL-N` Linear identifier.
4. Read the ticket's embedded or rendered Context Pack when one exists. Use
   `uv run atlas context show <ATLAS-N>` for a local canonical view when
   appropriate.
5. Read the specialist design/runbook that owns the behaviour under question.
6. For live claims, interrogate the current database, Linear/GitHub/CI state,
   and Symphony runtime rather than substituting a cached report or transcript.

Before dispatching Symphony work, run the relevant preflight, including the
pinned-model reachability check:

```bash
uv run atlas preflight --check-model
```

Never infer the database identity from a remembered path. Establish the current
checkout, any `ATLAS_DATABASE_URL`/`--db` override, and the actual file or server
being addressed before comparing operational counts. A stale report from one
store and a fresh query against another are not contradictory evidence.

## 2. Know which system is authoritative for which claim

A large fraction of Atlas incidents are authority mistakes rather than code
mistakes. Use this map before diagnosing or mutating anything.

| Claim | Authority |
| --- | --- |
| Product intent, architecture, rules | Canonical repository documents |
| Ticket definition after mint | Atlas store; Atlas-owned definition pushed to Linear |
| Operational ticket state | Atlas store, reconciled through the documented state-edge owners |
| Human/operator decision | Explicit operator action/receipt, never an agent inference |
| Agent execution | Symphony workspace/session and its tracker-visible lifecycle |
| Complete CI result | System-tier GitHub/CI evidence pinned to the exact published head |
| Acceptance readiness | Atlas verification at the exact accepted identity plus required human confirmation |
| Merge | Manual operator action in GitHub |
| Live Symphony ceiling | Process-owned runtime identity, not a policy mirror or committed file alone |
| Delivery admission limits | Active Atlas delivery-policy revision and repository-owned lane registries |
| Actual occupancy | Current observed runtime/board snapshot |

Do not infer one row from another. In particular, a configured Symphony ceiling,
a policy-approved ceiling, Atlas capacity controls, and observed occupancy are
four distinct identities. Controlled milestones require them to agree where the
gate says they must agree; agreement is proved, not assumed.

## 3. Our normal operating loop

Atlas delivery is a sequence of bounded authority handoffs, not one agent doing
everything:

```text
design / decision ratification
        ↓
small governed planning inputs
        ↓
plan + operator approval + apply
        ↓
Atlas store + committed planning renders
        ↓
PM sync publishes definitions/state to Linear
        ↓
Symphony executes one ticket
        ↓
agent validates exact candidate and publishes once
        ↓
CI Pending: agent stops, CI/Atlas own the handoff
        ↓
Review Required: operator/reviewer acceptance
        ↓
exact-head evidence + confirmation + verification
        ↓
manual merge
        ↓
merged-proof verification
        ↓
read-only observation of managed PM completion
```

Acceptance does not deploy code or schemas and does not orchestrate the PM
scheduler. After merged-proof verification, the managed recurring PM cadence
owns ordinary reconciliation while the acceptance driver only observes Atlas
store status; manual `atlas pm sync --once` is not an acceptance step.

When diagnosing a broken loop, first locate **which boundary failed**. Do not
start by changing code or dragging a Linear card until the owner of the missing
edge is known.

## 4. Turning a phase into tickets

### 4.1 Ratify the design before minting

Ticket minting is intentionally downstream of design decisions. Before writing
or approving a phase batch:

- identify every decision the phase requires;
- resolve operator-owned choices explicitly rather than hiding them in an
  implementation note;
- distinguish a true prerequisite from a preferred human ordering;
- identify live milestones and any authority they must *not* grant; and
- make the canonical phase/design document capable of anchoring every ticket.

If a ticket still contains a major unresolved architectural decision, the
planning is not finished. Either resolve the decision before minting or make the
ratification gate itself the bounded work; do not make an execution ticket
silently invent programme architecture.

### 4.2 Decompose for one delivery lane

A good Atlas ticket is independently reviewable, has one coherent objective,
and can reach a stable PR without repeatedly rediscovering programme design.
Use the schema limits as ceilings, not targets: up to seven acceptance criteria
is permitted, but a ticket does not become better by filling the allowance.

The practical decomposition test is:

- one main behavioural change or contract family;
- a bounded set of production surfaces;
- deterministic tests that can falsify the behaviour;
- explicit non-goals and prohibited mutations;
- documentation owned by the same behaviour; and
- dependencies that express real technical prerequisites.

Repeated tickets taking around an hour or more of agent wall time, especially
when much of that time is broad validation, conflict recovery, or repeated
context reconstruction, are a **planning smell**. Investigate whether the ticket
combines separable contracts before increasing agent turns or weakening
validation. Do not split work merely to reduce a stopwatch number; split where
there is a genuine independent review/authority boundary.

### 4.3 Review the batch as a system

Before the planning-input PR is accepted, inspect the whole phase batch rather
than each stub in isolation:

- Does every intended capability appear exactly once?
- Are sibling dependencies backward-only and acyclic?
- Are there genuine parallel lanes, or did the batch accidentally serialize
  independent work?
- Do protected/cross-cutting surfaces have a deliberate integration position?
- Are milestone tickets held behind the prerequisites that actually prove their
  entry conditions?
- Could two stubs independently attempt to establish the same schema, policy,
  runtime, or authority?
- Is any ticket so broad that its acceptance criteria describe multiple
  independently shippable behaviours?

The detailed stub and manifest contracts remain in
`planning-phases-and-ticket-stubs.md`.

## 5. Minting tickets safely

For an approved committed phase batch, the operator continuation is:

```bash
uv run atlas plan --stubs-only
uv run atlas apply
```

Treat the `plan` output as a gate, not as ceremony. Before confirming `apply`,
check at least:

- expected ADD count equals the approved stub count;
- no unexpected MODIFY, PROPOSE_ARCHIVE, or CONFLICT appears;
- titles/objectives correspond to the intended stubs;
- dependency edges match the ratified DAG; and
- the PlanRun was derived from the committed batch you intended to mint.

`atlas apply` is a two-sided operation: it mutates the operational store **and**
writes/moves working-tree planning artifacts. Immediately after a successful
apply, before doing unrelated work:

```bash
git status --short
head -n 6 docs/planning/tickets.yaml
```

Verify all of the following:

- the ticket high-water advanced by the number of minted tickets;
- minted keys form the expected monotonic range;
- dependencies resolve to those keys correctly;
- the four apply-owned planning renders changed together;
- every consumed stub moved from `docs/planning/inbox/` to
  `docs/planning/inbox/processed/`;
- the consumed batch manifest moved with them; and
- no unrelated file changed.

Then immediately stage the whole planning tree, including deletes and new
processed files:

```bash
git add -A docs/planning/
```

Commit and publish the apply artifacts before resetting, switching away, or
running another mint. **Never discard the working tree after apply.** The store
has already advanced; losing the renders/retirements creates a silent split
between operational state and committed planning history and can later cause
re-minting or broken context anchors.

### 5.1 Minting into Atlas is not yet publishing into Linear

`atlas apply` assigns Atlas keys and persists the backlog. First-sync PM work is
a separate boundary: PM sync creates the Linear issue for a pushable minted
ticket, stores its `external_linear_id`, pushes the Atlas-owned definition (with
an embedded Context Pack when rendering succeeds), and asserts the mapped
create-time workflow state.

After a mint/apply-artifact merge, verify the next PM sync rather than assuming
the Linear board is now correct:

```bash
uv run atlas pm sync --once -v
```

Check the resulting board/store mapping before dispatching the new batch. A
successful Atlas mint plus a failed Linear create/state assertion is a sync
incident, not grounds to re-run `atlas apply`.

## 6. Executing and reviewing tickets

### 6.1 Execution agents

The ticket description and embedded Context Pack are the execution contract.
Symphony agents own the active implementation lifecycle defined in
`WORKFLOW.md`; they do not own ticket authoring, final review, merge, or Done.

Before each candidate publication the agent must establish exact repository
identity, rebase against current `origin/main` as the workflow requires,
calculate `atlas validation-plan` from the exact base/head and complete changed
path set, and run every selected command/explicit test. Local validation is
agent-tier confidence only.

After one successful publication the agent moves through `PR Open` to
`CI Pending` and stops. `CI Pending` is intentionally not a Symphony-active
state.

### 6.2 Review the branch, not the completion message

A completion report is a claim. Review the actual PR head and diff. Establish
base/head identities, changed paths, current-main freshness, selected validation
profiles, explicit test targets, CI state, and ticket definition directly.

Do not mechanically run a repository-wide local sweep on every review. The
repository-owned `atlas validation-plan` decides the required local profile for
the exact candidate; `full-sweep` is run only when the deterministic plan selects
it or the operator explicitly requires it. Complete CI remains mandatory and
system-tier regardless of local plan width.

A review verdict is pinned to a head. Any head change makes old local results,
CI evidence, review observations, confirmations, and acceptance verdicts
historical for the new candidate.

### 6.3 Mechanical staleness is not semantic remediation

A `Review Required` PR that is merely behind/diverged/conflicted with current
`main` uses the operator-owned rebase lane described in `pr-acceptance.md`.
Do not send it back through Symphony as `Changes Requested` unless
implementation or other semantic remediation is actually required.

Conversely, never use the operator rebase lane to hide a semantic conflict. An
out-of-scope or meaning-changing conflict is a decision/rework problem.

## 7. Diagnosis: identity first, owner second, timeline third, mutation last

Use this order whenever behaviour surprises you.

### 7.1 Identity

Record exact identities before forming a theory:

- Atlas repository, branch and commit;
- current `origin/main`;
- Atlas ticket key and Linear issue id;
- PR repository/number/head/base;
- database actually queried;
- active policy revision/fingerprint when relevant;
- Symphony service/process/workflow identity for runtime questions; and
- CI run/check identities for evidence questions.

If two observations do not share the same identity, they are not yet evidence
of a contradiction.

### 7.2 Owner

Ask which component is allowed to perform the missing transition or mutation.
Examples:

- an agent owns implementation and `PR Open → CI Pending`;
- the trusted system-tier reconciler owns determinate CI-pending exits;
- the operator owns manual acceptance and merge;
- `atlas apply` owns planning renders and key assignment;
- PM sync owns first publication of Atlas ticket definitions into Linear; and
- controlled Symphony ceiling changes are operator/runtime procedures, not
  admission decisions.

An observation from the wrong component is not authority merely because its
value looks plausible.

### 7.3 Timeline

Reconstruct the smallest relevant sequence with timestamps and exact heads.
Preserve anomalous transitions rather than manually correcting them first.
Atlas has repeatedly learned more from the exact bad edge than from a repaired
board.

### 7.4 Evidence

Prefer machine-readable, bounded evidence over pasted narrative output. A green
GitHub rollup is not automatically the Atlas CI-handoff authority; a local test
pass is not system-tier completion; a workflow file on disk is not proof of the
currently accepted Symphony runtime; a database row from the wrong store is not
live state.

### 7.5 Mutation

Only after identity, owner, timeline and evidence are understood should the
operator mutate the system. Prefer the named governed command/lane. Never repair
an incident by editing the SQLite store, planning renders, or Linear states
surgically unless the owning runbook explicitly makes that the recovery path.

## 8. Fast symptom routing

Use the detailed troubleshooting runbook for commands and signatures. The
first routing questions are:

| Symptom | Check first |
| --- | --- |
| Ticket will not dispatch | tracker state, dependencies/readiness, admission hold reasons, preflight, Symphony active-state contract |
| Agent active but no PR | Symphony event/error signature, Codex reachability/version, workspace identity, Git write credential |
| Push returns 403 | distinguish operator env token from agent/on-disk git credential; inspect the actual credential path |
| PR sits in CI Pending | exact publication identity, complete required checks, `atlas pm sync --once -v`, CI-handoff reconciliation/hold reason |
| CI Pending returns to active work unexpectedly | preserve transition timestamps; check for out-of-ownership automation/integration writes; do not drag it back first |
| Review Required PR becomes stale after sibling merge | exact-head status, then operator rebase lane |
| Atlas report disagrees with raw query | prove both commands used the same database and regenerate the report |
| New minted tickets appear again on a later plan | inspect committed stub retirement and planning-render high-water before minting anything else |
| Context Pack missing/definition-only in Linear | inspect committed processed stub/source anchor and pack-render failure evidence; repair through the supported pack path |
| Runtime gate claims disagree | compare configured process readback, active Atlas policy, independent capacity controls, and observed occupancy separately |

## 9. VPS and other live environments

A live environment is an execution/authority location, not a substitute for
context. A fresh remote agent should read the same repository-owned operational
context before investigating it.

Do not keep multiple writable copies of the canonical operational database and
synchronize them by copying the database file back and forth. Development/test
stores may be disposable or refreshed snapshots; live milestone evidence must
be produced against the environment and store named by that milestone's
contract.

For live proof work such as a concurrency ramp, a local simulation or copied DB
can test code but cannot replace process-owned runtime, real admission, real
occupancy, real Linear/GitHub/CI transitions, or the canonical milestone
receipts. Keep investigation read-only until the gate explicitly calls for an
operator mutation.

When remote access is available, the preferred experience is for the agent to
work where the evidence exists **after** loading repository context, rather than
for the operator to copy commands and outputs between a context-rich chat and a
context-poor shell session.

## 10. Capture what the session teaches

At the end of every substantial review, incident, milestone, or planning
session ask:

> Did this session reveal a rule or operating fact that a fresh agent would
> otherwise have to rediscover?

Put the answer in the correct durable home:

| Finding | Home |
| --- | --- |
| Architectural/governance decision | ADR or owning design document |
| Stable operator procedure | owning runbook |
| Symptom → cause → recovery pattern | `troubleshooting.md` |
| Environment/credential/runtime fact | `operator-environment.md` |
| Reusable execution lesson | Atlas Lesson, promoted by the operator |
| Delivery anomaly | DebtItem / owning operational record |
| Phase/milestone historical evidence | closure report or milestone receipt |
| Current transient state | database/runtime/tracker — do **not** freeze into prose |

Conversation history is useful working context, but it is not Atlas authority.
Once an insight is ratified and expected to outlive the conversation, either
store it in Atlas or deliberately discard it. Do not rely on an AI session
remembering it next time.

## 11. Fresh-agent readiness test

A fresh operator/reviewer agent is sufficiently bootstrapped when, without an
oral history, it can correctly answer all of these from repository + live
state:

1. What are we trying to prove or deliver now?
2. Which document/ticket is authoritative for that claim?
3. Which system owns each relevant state transition or mutation?
4. What exact repo/PR/database/runtime identities are being observed?
5. What evidence tier supports the current claim?
6. What action is permitted next, and what actions are explicitly prohibited?
7. If the observation is surprising, which diagnostic path should be followed
   before mutating anything?

If a capable fresh agent cannot answer one of those because essential context
exists only in a previous conversation, treat that as an Atlas documentation or
context-model defect.

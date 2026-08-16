# Parallel Delivery Efficiency and Integration Control (Phase 15.5)

Status: Planned interstitial design authority. Defines the bounded workflow
correction required before ATLAS-253 may begin Phase 15's live ceiling ramp.

## Problem and outcome

Ten available workers do not create ten useful delivery lanes when every agent
repeats the repository-wide test matrix, waits for CI and rewrites its branch
after every sibling merge. That operating model spends agent turns on work CI
already owns and turns independent implementation into avoidable integration
contention.

Phase 15.5 separates four kinds of capacity and authority:

- an agent owns implementation and focused local confidence;
- CI owns complete system-tier validation for the published identity;
- Atlas owns deterministic admission and bounded queue state;
- the operator owns review, rebase, conflict resolution and merge.

The outcome is higher accepted flow with less duplicated validation and fewer
unnecessary branch rewrites. The phase does not raise concurrency. Its closure
is the explicit operator release gate for ATLAS-253, which still owns Phase
15's controlled one-to-three-to-five-to-seven-to-ten exercise.

## Binding decisions

| Decision | Contract |
| --- | --- |
| Local validation | Agents run ticket-required and affected checks selected by a deterministic repository-owned registry. Unknown or protected surfaces fall back to a complete local sweep. |
| CI authority | Complete CI remains mandatory and is the only system-tier authority. Scoped local checks are confidence evidence, never completion evidence. |
| Agent lifetime | After focused checks and one successful publication, the ticket enters `CI Pending`; the Symphony slot is released and the agent does not poll CI. |
| CI reconciliation | Atlas observes pinned GitHub check evidence and performs one deterministic state transition. Infrastructure, pending and ambiguous outcomes remain held without retry churn. |
| Capacity accounting | Working, CI/integration and review occupancy are separate budgets. Freeing a worker never makes the CI-pending queue unbounded. |
| Conflict avoidance | Declared protected surfaces are exclusive integration lanes. Independent surfaces remain parallel. |
| Freshness | A clean candidate may avoid branch rewrite only after a feasibility spike proves exact repository, PR, base, head and synthetic-merge identities from authoritative GitHub evidence. |
| Rebase fallback | A conflict, stale identity, provider ambiguity or failed spike routes to the delivered operator-owned rebase lane. Atlas never resolves or publishes a rebase automatically. |
| Existing Phase 15 work | ATLAS-250 and ATLAS-251 continue normally and become prerequisites of the Phase 15.5 API/UI tickets. They are not recreated or rewritten. |
| Ramp release | ATLAS-253 remains `Needs Human` until the Phase 15.5 milestone and closure are reviewed and merged. |

## Authority and non-authority

Phase 15.5 may calculate validation plans, observe commit-pinned CI, classify
outcomes, hold or advance tickets through governed PM transitions, calculate
protected-lane occupancy and present integration pressure.

It may not skip required CI, mark agent claims as system-tier, merge a pull
request, rebase or force-push a branch, resolve a conflict, cancel a worker,
change the Symphony ceiling, approve a plan, weaken branch protection, mutate
GitHub checks or grant itself credentials. An optimisation that cannot prove
its inputs fails closed to the existing safer path.

## Local confidence and complete CI

### Validation registry

A digest-pinned declarative registry at
`atlas/verification/validation_registry_v1.json` maps narrow repository paths
and registered ticket requirements to ordered profiles. The versioned v1
profiles are Python tests, static checks, documentation, database/schema,
generated OpenAPI client drift, Operator UI unit/build, browser/end-to-end,
workflow contracts and the complete local sweep. The registry declares both
commands and selection reasons. Its content hash is checked before use; a
schema, version or digest mismatch is registry drift and selects the safe
complete-sweep baseline embedded in code.

`atlas validation-plan` accepts a full lowercase 40- or 64-character base
object id, a full head object id, and every repository-relative path in that
exact diff. Repeatable `--ticket-requirement` values name registry ids and
repeatable `--ticket-test` values add explicit test files. The optional
`--expect-registry-version` pins a caller to the policy version it reviewed.
The CLI uses only read-only Git operations to derive the exact base-to-head
name-status diff, including both sides of renames, and compares that trusted
set with the supplied paths. It also proves every explicit ticket test is a
blob at the supplied head. It does not execute validation commands or write
repository/external state. It emits human-readable output by default or
canonical compact JSON with `--json`, containing:

- the repository identities used;
- the changed-path proof status;
- every selected profile and command;
- the path or ticket requirement that selected it;
- every mandatory changed or ticket-declared test file;
- any protected-surface reason; and
- whether the complete-sweep fallback is mandatory.

Inputs and rendered fields have fixed count and length bounds. Input order,
duplicates, clocks, UUIDs and model interpretation do not affect the plan;
identical identities and changed-path sets serialize to identical bytes.
Changed tests, tests added for changed behaviour and ticket-declared tests are
additive mandatory inputs. Ticket-test paths must match a configured Python,
Vitest or Playwright runner and exist at the supplied head. There is no
exclusion option, and an invalid or unregistered free-form value cannot remove
a profile. Unknown paths, omitted or mismatched diffs, Git discovery failure,
unprovable ticket tests, ambiguous or inconsistent identities, registry drift,
oversized input and protected cross-cutting surfaces select the documented
complete local sweep rather than an incomplete result. The registry, pure
classifier and read-only CLI adapter are themselves protected validation-policy
surfaces.

The complete local sweep runs the unfiltered Python test/static/documentation/
architecture gates and both Operator UI cold-checkout wrappers. It remains an
explicit profile and the conservative fallback. CI independently runs every
required job, including event-scoped gates such as PR-title provenance; local
profile selection never changes the CI workflow.

### Evidence boundary

Agent-run scoped checks are agent-tier evidence: useful for handoff and
debugging, but insufficient for completion. CI executes the complete required
matrix against the authoritative candidate identity and supplies system-tier
evidence. No local optimisation removes, weakens or conditionally skips a
required CI job.

An unchanged head is validated locally once per selected plan. Repetition of a
complete sweep for the same identity requires an explicit fallback reason; it
is not a default handoff ritual.

Before publication the agent supplies the exact base/head, every changed path,
ticket requirement and explicit test file to the deterministic planner, then
runs every ordered command and explicit test target. A failed selected check
blocks publication. The complete local sweep runs only when the named
`full-sweep` conservative profile is selected or the operator explicitly
instructs it; the agent neither narrows the plan nor adds a model-selected
check. Any head change makes the prior plan and results historical only.

## CI-pending lifecycle

### State mapping

`CI Pending` is the Atlas team's Linear `started` state
`85cdfa65-b990-41cc-a4ea-0071868ba27f`, mapped exactly to `ci_pending`. It is
observed by Atlas and deliberately absent from Symphony's active and terminal
state lists. It means implementation has published one candidate and is waiting
for authoritative checks; it is neither working occupancy nor a review verdict.

```mermaid
stateDiagram-v2
    [*] --> InProgress
    InProgress --> PROpen: focused checks and publish
    PROpen --> CIPending: agent releases slot
    CIPending --> ReviewRequired: system CI passes
    CIPending --> ChangesRequested: definite implementation failure
    CIPending --> CIPending: running, infrastructure or ambiguous
    ReviewRequired --> ChangesRequested: human remediation request
```

After successful publication the agent first enters `PR Open`, making the
published-PR prerequisite durable, then owns only `PR Open → CI Pending` and
stops. It does not wait, poll, interpret remote failures or consume a Symphony
working slot. The handoff records the exact plan, commands and local results as
agent-tier confidence. Atlas alone owns `CI Pending → Review Required` and `CI
Pending → Changes Requested`; browser and Symphony paths own neither exit. A
changed head invalidates earlier CI authority.

The CI reconciler consumes trusted check evidence pinned to the current head:

- all required checks passed: transition to `Review Required`;
- a definite implementation-owned failure: transition to `Changes Requested`;
- checks running or queued: remain `CI Pending`;
- provider outage, rate limit, missing check, malformed payload, unknown
  conclusion or identity mismatch: remain held with a typed reason.

The actionable failure rule is deliberately narrower than generic FAILED
normalisation: every required system-tier check must be present and determinate,
and each deciding failed observation must carry the provider's explicit
`failure` conclusion. Partial sets, timeouts, cancellation, stale heads and
contradictory current observations cannot send implementation back to Symphony.

Only an owner-specific PM boundary performs Linear transitions. The generic
Linear status pull may mirror the agent-owned `PR Open → CI Pending` entry, but
it rejects arbitrary entries and every CI-pending exit as a deduplicated
ownership anomaly. ATLAS-256's trusted CI reconciler is the only seam that can
exercise the Atlas-owned exits. It re-reads the PR head, complete board, active
policy, coherent snapshot and product/ticket-scoped evidence assessment
immediately before the write under the shared product lease. Any change to the
assessment or its deciding evidence ids records a typed hold and requires a
fresh tick. Each determinate decision is appended with the exact identity and
bounded check results, and a durable fence is committed before the single
Linear mutation. A transport-ambiguous mutation remains fenced until a fresh
complete board observation proves source, target or external movement; that
reconciliation tick never retries the write. Duplicate observations are
therefore idempotent, and concurrent owners, lease loss or identity movement
produce zero mutations. A new head restarts the lifecycle with new evidence;
previous records remain history.

These states are deliberately different claims. `CI Pending` says only that a
locally validated candidate was published and CI now owns classification.
`Review Required` says the system-tier required-check set passed for that exact
head and the candidate may enter operator acceptance. Final completion still
requires the accepted exact-head evidence, required human approval, manual
merge and merged-proof verification; neither local success nor Review Required
is `Done`. A shorter valid local plan never shortens or weakens the complete CI
matrix.

## Three separate capacity budgets

Phase 15's working and review budgets remain binding. ATLAS-255 adds one
operator-owned integration budget for the CI-pending queue:

| Budget | Counts | Releases when |
| --- | --- | --- |
| Working | Ready/active Symphony work under the Phase 15 policy | Work publishes or otherwise leaves the active delivery states |
| Integration | `CI Pending` published heads awaiting a determinate CI outcome | Required checks become determinate for the same identity |
| Review | Existing Phase 15 acceptance pressure | Existing review policy releases it |

A ticket moves between budgets; releasing one budget does not erase downstream
pressure. Admission stops when any applicable budget or protected lane is
full. Paused or draining modes retain their existing Phase 15 semantics.
No budget is a utilisation target and none authorises Symphony cancellation.

## Protected integration lanes

### Classification

The digest-pinned repository registry at
`atlas/pm/protected_lane_registry_v1.json` declares stable lane keys, strict
integer capacities and additive matcher rules. Version 1 bounds six lanes at
capacity one: database migrations, generated API/client contracts, workflow
configuration, planning sources/renders, shared dependency manifests and the
explicitly operator-declared admission-control hotspot. The parser rejects an
unknown version, digest drift, duplicate/ambiguous lane or rule identity,
unbounded capacity, non-canonical selector/path or a lane without a rule.

Before admission, a ticket is classified only from its stored `component`,
`tags`, `relevant_docs` and `documentation_requirements`. Components and tags
are NFKC-normalised, trimmed and case-folded; declared paths must already be
canonical repository-relative paths. Objective, context, acceptance criteria,
implementation notes, title and other model prose are never inspected. Every
matched lane and its declaration/rule evidence are retained. Distinct
declarations can select multiple lanes, while one declaration selecting
different lanes, a non-canonical path or contradictory canonical tag
declarations is a typed fail-closed classification. A multi-lane candidate is
feasible only when all matching lanes have capacity.

### Behaviour

Protected lanes serialize only conflict-prone integration surfaces. Every
working and CI-pending ticket consumes all of its valid matches; review-only and
pre-delivery tickets do not occupy a lane. Each saturation hold names the lane,
simulated count, capacity and sorted current owning ticket keys without exposing
secrets. Occupancy and classification are part of the coherent snapshot, whose
active-surface fingerprint is independent of source order.

Candidate ranking is unchanged. The first reason-free candidate in the
existing stable order is selected, a higher held candidate may be skipped for
the highest feasible one, and every lower feasible candidate receives the
existing `single_write_limit` even when it names another lane. Immediately
before the one external admission write, Atlas reloads the digest-pinned
registry and rebuilds protected-lane state across both deterministic race
seams. Registry identity or active-surface movement yields a typed stale result,
persists no write fence and admits nobody.

The registry is operator-owned configuration reviewed like other delivery
policy. Its loader and classifier have no GitHub client, Git command, Linear
writer, Symphony adapter or policy-revision service. A hold never mutates a
diff, rebases Git, demotes a ticket, cancels a worker, optimises policy or widens
capacity. Atlas does not infer a permanent lane from model judgement or learn
one automatically from a conflict. Publication-time verification of declared
paths remains a separate downstream control; admission does not inspect a
GitHub diff.

## Exact-base acceptance without unnecessary rewrite

### Why this is a spike first

Being behind `main` is not itself a semantic defect. A clean candidate can be
evaluated as the exact synthetic merge of current base plus unchanged head,
but only if GitHub exposes identities and checks strongly enough to bind the
acceptance decision. The sixth ticket is therefore an evidence spike, not an
implementation assumption.

The spike must record, across controlled clean, conflicting and moving-base
PRs:

- repository and PR identity;
- current protected base branch and fetched base commit;
- candidate head commit;
- GitHub mergeability state and its convergence behaviour;
- synthetic merge commit/tree identity where available;
- required-check association and whether it is unambiguously tied to that
  synthetic identity; and
- behaviour under head movement, base movement, conflict, provider delay and
  malformed/partial responses.

PASS requires reproducible authoritative evidence that the accepted snapshot
is exactly the candidate head integrated with the current base, with no stale
reuse window and no reliance on an agent-provided merge claim. If GitHub cannot
provide that contract for this repository and branch protection, the spike
records FAIL and the no-rewrite path is not implemented.

### ATLAS-259 governed feasibility report

**Decision: FAIL (2026-08-16).** The exact synthetic candidate is observable,
but this repository's bounded GitHub check evidence does not pin required
results to that candidate. The existing current-head acceptance contract and
operator-owned rebase lane remain authoritative. ATLAS-260 must not activate a
no-rewrite path unless its phase design first adds a system-tier candidate
attestation that closes this gap.

The read-only live probe used merged PR 329 because it retained one complete,
clean GitHub Actions execution without touching an open production PR. GitHub
Actions jobs `test` and `lint` independently checked out candidate
`9c756d071289691dd56f769450b1d623d2d3e2ff`. The candidate has parents
`1c1573715f2f672636493896fb0452f4341e9fff` (then-current `main`) and
`499e3687ac66279ae0ea09c571dbe797db8c13f2` (PR head), and tree
`44a3ba815f75d8163a0af1ef009a33d4242c6200`. The two checkout reads reproduced
that identity. The corresponding successful Check Runs, however, have
`head_sha` equal to contributor head `499e3687...`, and the Checks endpoint for
candidate `9c756d07...` returns zero Check Runs. Active repository ruleset
`17514272` supplies a closed set of eight required `(context, GitHub App ID)`
pairs: `build-operator-ui`, the three `lint-operator-ui*` jobs,
`lint-pr-title`, and the three `test-operator-ui-*` jobs, all for App ID
`15368`. Every one of those successful results is head-pinned rather than
candidate-pinned, so the complete provider-required set fails exact candidate
attribution.

The later GitHub merge commit
`160a7e3c87f91ce601564eba22c4328b95a963c0` has the same two parents and tree as
the tested candidate but a different commit SHA. That is useful relationship
evidence, not a retroactive CI pin. Log parsing is not an acceptable repair:
logs are unbounded payloads, candidate identity is not part of the bounded
Check Run result, and relying on checkout implementation details would weaken
the provider boundary.

The executable harness is
`scripts/exact_base_candidate_spike.py`, with bounded selected-field fixtures in
`tests/fixtures/github/exact_base_candidate_cases.json` and mutation-spy tests
in `tests/test_exact_base_candidate_spike.py`. It creates only unreferenced and
local Git objects in a temporary repository and runs no `fetch`, `merge`,
`push`, `rebase` or `update-ref`. It proves:

- repeated clean reads reconstruct one candidate commit and tree;
- head movement and sibling-`main` movement each mint a different candidate,
  so the old candidate and its evidence are historical immediately;
- missing, conflicted, malformed and indeterminate observations fail closed;
- two candidates for unchanged head/base are provider ambiguity;
- a final two-parent merge commit may share the candidate tree while having a
  different commit identity;
- a squash result has the candidate tree, one base parent and a new commit SHA,
  so candidate authority and post-merge proof are distinct; and
- credentials and raw payloads are absent from retained projections, and
  oversized check collections fail closed before retention.

The sufficient identity algebra for a future amended design is:

```text
candidate = (
  repository, PR number,
  head SHA,
  base ref, live base SHA,
  candidate commit SHA, candidate tree SHA,
  candidate parents == (live base SHA, head SHA),
  canonical (check name, GitHub App ID) set fingerprint
)

required result = (
  check name, GitHub App ID, external execution ID,
  commit SHA == candidate commit SHA,
  lifecycle time, terminal conclusion
)
```

Two bounded reads of an unchanged repository/PR/head/base tuple must reproduce
the complete candidate tuple. Every member of the unchanged required-check set
must then resolve to exactly one current, successful result whose commit SHA is
the candidate SHA. Any head, live-base, candidate, tree, parent or required-set
movement; duplicate or absent result; conflict; missing field; delay;
indeterminate mergeability; or malformed response invalidates the tuple. The
current provider evidence fails the `commit SHA == candidate commit SHA` term.

Fallback is exact and unchanged: for an eligible mechanically stale Review
Required PR, run
`uv run atlas pr rebase prepare --pr <N> --repo <owner>/<repo>`, resolve only
the managed-worktree conflicts if any, use `continue` until
`ready_to_publish`, then use `publish`. All exact-head evidence, confirmations
and readiness restart at the rebased head.

### ATLAS-260 governed system-tier attestation assessment

**Decision: PASS (2026-08-16), assessment authority only.** A bounded
system-tier attestation can close the identity gap found by ATLAS-259 if, and
only if, its producer is outside contributor control, candidate execution is
isolated from signing authority, and Atlas independently verifies both the
signed manifest and the provider lifecycle. This PASS authorises refinement
and planning of a later implementation ticket. It does not implement or
activate `exact_integration_candidate`, change acceptance-session identity,
or permit a no-rewrite classification. The exact-head/current-main contract
and operator-owned rebase lane remain the Phase 15.5 production authority.

The executable assessment is
`scripts/candidate_attestation_assessment.py`, with selected-field fixtures in
`tests/fixtures/github/candidate_attestation_cases.json` and tests in
`tests/test_candidate_attestation_assessment.py`. Its governed stable case
records repository, PR, contributor head, live base, candidate commit/tree and
ordered parents, required-set fingerprint and members, immutable workflow
commit/blob, run ID, run attempt, job and Check Run IDs, candidate mapping and
terminal conclusions. Twenty-four adversarial cases fail closed. The harness
has no network, GitHub, Linear or Symphony client; its only mutations are
local Git plumbing inside a disposable temporary repository.

The cryptographic boundary is deliberately explicit. The fixture field
`cryptographically_verified` represents the bounded typed output of an
Atlas-owned Sigstore verifier, not a boolean that production code may accept
from a workflow or contributor. A future implementation must verify a capped
GitHub artifact-attestation envelope against the GitHub Actions OIDC issuer,
trusted root, subject digest and exact signer identity before constructing that
typed projection. GitHub documents that an artifact attestation binds a subject
digest to a signed statement and records workflow/repository/run provenance;
its verifier warning that a compromised workflow can falsify predicates is why
workflow isolation is part of the authority rather than an optional hardening
step ([artifact attestations](https://docs.github.com/en/actions/concepts/security/artifact-attestations),
[OIDC claims](https://docs.github.com/en/actions/reference/security/oidc),
[secure-use guidance](https://docs.github.com/en/actions/reference/security/secure-use)).

#### Exact contract proven

The future authoritative manifest is canonical JSON and its SHA-256 is the
attestation subject digest. The identity algebra is:

```text
candidate = (
  repository, PR number, contributor head SHA,
  base ref == main, live base SHA,
  synthetic candidate SHA, candidate tree SHA,
  ordered parents == (live base SHA, contributor head SHA)
)

required_set = sha256(canonical_json(
  ruleset ID,
  immutable policy repository + path + commit SHA + blob SHA,
  ordered unique (required context, GitHub App ID) members
))

execution = (
  trusted producer repository + workflow path,
  workflow ref + immutable workflow commit SHA + blob SHA,
  workflow run ID + run number + run attempt,
  Atlas-controlled trigger, GitHub-hosted runner,
  isolated-signer boundary
)

required_result[i] = (
  required context + GitHub App ID,
  provider job ID + Check Run ID,
  execution run ID + attempt,
  candidate SHA,
  status == completed, conclusion == success
)

attestation = sigstore_verify(
  subject_digest == sha256(canonical_manifest),
  issuer == GitHub Actions OIDC,
  signer workflow == trusted immutable workflow,
  invocation == execution run ID + attempt
)
```

Authority exists only when two bounded provider reads reproduce the complete
candidate, required-set, workflow and execution tuple; the signed manifest
equals the independently reconstructed tuple; and every required member maps
to exactly one successful provider job in that same run and attempt. The
provider Jobs API remains lifecycle corroboration, while the trusted signed
manifest supplies the candidate mapping absent from ATLAS-259's Check Runs.
Neither source is sufficient alone.

The producer workflow required by this contract is Atlas-controlled and
triggered from trusted configuration, with every action reference pinned to a
full commit SHA. Candidate jobs run on separate GitHub-hosted runners with
read-only content access, `persist-credentials: false`, no repository or
environment secrets, no OIDC, no attestation permission and no credentialed
cache shared with the signer. The signer runs only after every required job on
a fresh runner. It never checks out or executes candidate code and consumes no
candidate artifact, cache, output or environment. It independently reads the
PR, live base, candidate Git objects, immutable required-set policy and its own
provider jobs; then it emits and signs the bounded manifest. The design does
not use `pull_request_target` or `workflow_run` with an untrusted checkout.

Atlas independently verifies the retained capped signature envelope and
manifest, then performs bounded REST reads of the PR, live branch, candidate
commit/tree/parents, trusted workflow commit/blob, ruleset/policy, workflow
run attempt and jobs. It retains only the canonical manifest, selected
certificate/provenance claims, signature and inclusion-proof material needed
for verification, provider object IDs, lifecycle fields and hashes. Each
collection has a hard count/byte cap. Credentials, raw provider envelopes,
arbitrary payloads, workflow logs and job logs are excluded. Unavailable,
oversized or unverifiable provenance fails closed.

#### Trust-model answers

1. **Producer:** the isolated signer job of the Atlas-owned candidate-CI
   workflow, after its fixed required candidate jobs finish.
2. **Why trusted:** Atlas admits only a locally configured producer repository,
   path, full workflow commit and blob digest after cryptographic provenance
   verification. A GitHub workflow name, branch name or human claim is not a
   trust signal.
3. **Immutable identity:** the producer workflow commit SHA, workflow blob SHA,
   every action's full commit SHA and the signed attestation's build-config and
   invocation identities.
4. **Contributor modification:** no. The producer and required-set policy are
   outside the PR's writable identity. Candidate code runs on separate
   unprivileged runners and cannot reach the signer workspace or inputs.
5. **Head/base binding:** the signer and Atlas each resolve repository/PR,
   contributor head and live `main`; ordered candidate parents must be exactly
   `(live base, contributor head)`, and the second read must match the first.
6. **Candidate binding:** both boundaries resolve the full synthetic commit,
   tree and ordered parents. The signed manifest contains those exact values.
7. **Required-result binding:** fixed trusted workflow semantics execute each
   named job at the manifest candidate; the signer records provider job/Check
   Run IDs and candidate mapping, while Atlas re-reads each job and requires a
   unique same-run/same-attempt successful match.
8. **Reruns:** run ID and `run_attempt` are signed and re-read. A newer attempt
   or replacement run invalidates all evidence from the old lifecycle.
9. **Required-set stability:** ordered unique `(context, App ID)` members,
   ruleset ID and immutable policy file identities form the fingerprint. A
   changed member, ruleset, policy commit/blob or duplicate invalidates it.
10. **Independent provider proof:** Sigstore verification plus bounded GitHub
    PR, branch, Git commit, workflow-run-attempt and job reads corroborate every
    retained field. The attestation API is addressed by subject digest; the
    run-attempt and Jobs APIs distinguish the execution lifecycle.
11. **Invalidating movement:** repository/PR, head, live base, candidate
    commit/tree/parents, workflow commit/blob/ref, policy/ruleset/fingerprint,
    run, attempt, job identity/status/conclusion or attestation subject/signer
    movement invalidates the old evidence immediately. Missing, conflicting,
    malformed, duplicate, skipped, cancelled and superseded observations also
    invalidate it.
12. **No logs or contributor payloads:** yes. Verification consumes only the
    capped signed manifest, cryptographic provenance and selected provider API
    fields. Workflow logs and arbitrary candidate-generated artifacts or
    outputs have no role.

The trust assumptions are GitHub's OIDC/Sigstore and REST identities, full Git
object immutability, GitHub-hosted job isolation, Atlas protection of its
producer allowlist and correct implementation of the pinned workflow. If a
future implementation cannot independently verify any assumption, cannot keep
the signer isolated, or must accept a candidate-controlled assertion, it must
return indeterminate/FAIL and retain the rebase lane.

The adversarial matrix proves stable reproduction and fail-closed head, sibling
base, candidate, workflow, required-set, run and attempt movement; missing,
conflicted, indeterminate and malformed candidates; missing, failed, duplicate,
cancelled, skipped, superseded and candidate-mismatched results; unverified or
contributor-modifiable producers; stale prior-base evidence; repository/PR
ambiguity; tampered fingerprints; and oversized collections. Disposable Git
evidence separately proves that a later two-parent merge and a squash may share
the candidate tree while having different commit identities. Exact commit
inequality prevents authority transfer in both cases.

### Conditional no-rewrite lane

ATLAS-260's assessment PASS permits a later implementation ticket to refine
this lane against the exact attestation contract above; it does not authorise
Atlas to classify any current candidate as `exact-base clean`. ATLAS-259's
provider-native route remains FAIL, and no production system-tier candidate
attestation exists yet.
Acceptance pins repository, PR, base branch, base commit, candidate head,
synthetic merge identity, required-check set and acceptance-criteria
fingerprint. Any movement or ambiguity invalidates the classification and all
derived authority.

An exact-base-clean classification permits review of the unchanged candidate
without force-pushing a rebased head. It does not merge the PR or bypass
required checks. A true conflict, failed mergeability, absent synthetic
identity, branch-protection mismatch or stale observation routes to
`rebase required` or `indeterminate`. The delivered Phase 12 operator lane
remains the only rebase/publish mechanism.

## API and Operator UI

After ATLAS-250 delivers the Phase 15 API, the Phase 15.5 API extension exposes
bounded projections for CI-pending integration occupancy, protected-lane
holds, current candidate identities, validation-plan summaries and typed
freshness outcomes. It adds no generic mutation route and returns no raw
provider payload, credential, command output or workspace path.

After ATLAS-251 delivers the Phase 15 UI, the console shows working,
CI-pending integration and review pressure separately. It distinguishes
waiting from failure, explains protected-lane holds, marks identity movement
stale and never presents a no-rewrite classification as a merge action. Policy
changes retain the existing authenticated confirmation and receipt boundary.

## Symphony workflow contract

The binding workflow prompt instructs an implementation agent to:

1. inspect the exact ticket requirements and changed surfaces;
2. rebase once onto current `origin/main` for the candidate publication;
3. calculate the deterministic plan from exact identities, every changed path,
   ticket requirement and explicit test file;
4. run every selected command and explicit test, publishing nothing on failure;
5. publish the unchanged validated candidate once and record the exact bounded
   local results;
6. enter `CI Pending`; and
7. stop the session in the same turn.

`WORKFLOW.md` mechanically proves that `CI Pending` is absent from
`active_states`, so the tracker transition releases the Symphony slot instead
of relying on an instruction to wait quietly. A later system-tier failure can
return the preserved workspace only through `Changes Requested`; a pass moves
to `Review Required` without redispatching the agent.

The workflow forbids agent-side CI polling, repeated full sweeps for an
unchanged head without a fallback reason, automatic rebase/conflict resolution
and mutation of the ceiling. System reconciliation, operator review and the
existing integration lane continue after the agent releases its slot.

## Existing-ticket and release sequencing

ATLAS-250 and ATLAS-251 are already minted Phase 15 delivery contracts. They
should continue after PR #315 rather than wait for Phase 15.5 implementation.
The new API ticket depends on ATLAS-250; the new UI ticket depends on ATLAS-251
and that API extension. This avoids reopening their accepted definitions while
giving the new projections stable extension points.

An existing ticket cannot depend on a future unminted stub. Therefore
ATLAS-253 is governed by an operator release gate: keep it in `Needs Human`,
merge and apply the Phase 15.5 planning inputs, deliver the Phase 15.5 graph,
merge its closure, then deliberately release ATLAS-253. No automation performs
that release.

## Delivery graph

```mermaid
flowchart TD
    V["Scoped validation"] --> W["Publish and slot release"]
    C["CI pending"] --> R["CI reconciliation"]
    R --> W
    L["Protected lanes"] --> A["API and UI"]
    S["Merge-evidence spike"] --> N["Conditional no-rewrite lane"]
    N --> A
    W --> M["Efficiency milestone"]
    A --> M
```

The validation, CI-lifecycle and merge-evidence lanes can begin independently
once their listed existing prerequisites are satisfied. The milestone joins
them and remains the only Phase 15.5 closure authority.

## Milestone and closure

Before running the milestone, the operator records fixed observation windows,
an independent workload and numerical PASS/FAIL thresholds for:

- agent active time and local-validation time;
- duplicate complete sweeps per unchanged identity;
- CI queue/run time and indeterminate outcomes;
- working, CI-pending integration and review queue bounds;
- review dwell, conflicts and rebases; and
- accepted completed flow, not merely PR creation or worker utilisation.

The controlled run includes protected-lane collisions, definite code failure,
infrastructure failure, provider ambiguity, head/base movement and a genuine
merge conflict. Repository and external-call spies prove the absence of
automatic merge, rebase, push, worker cancellation, CI mutation, plan
approval, permission expansion, deployment and secret-bearing retained
evidence.

Phase 15.5 closes only when every predeclared threshold and authority invariant
passes and the Phase 15.5 closure report lands at
docs/closure/phase-15.5-closure-report.md. A failed or
ambiguous result records the evidence, leaves Phase 15.5 open, keeps ATLAS-253
in `Needs Human` and leaves `WORKFLOW.md`'s committed ceiling unchanged.

## Explicit non-goals

- Raising or dynamically selecting `max_concurrent_agents`.
- Executing any ATLAS-253 ramp gate.
- Removing complete CI jobs or treating focused local tests as completion.
- Automatic GitHub merge, merge queue, rebase, force-push or conflict repair.
- Predictive or learned test-impact selection.
- Agent/model scoring or automatic routing; that belongs to Phase 16.
- Cancelling sessions, deleting workspaces or demoting active tickets.
- Remote hosting, multi-product allocation or deployment authority.

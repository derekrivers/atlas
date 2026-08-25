# Runbook: Symphony Runtime Operation

This is the canonical operator procedure for the supported
`atlas-symphony.service` runtime. It owns immutable `WORKFLOW.md`
materialisation, exact release and process identity, service reload/restart,
process-owned runtime readback, controlled ceiling activation and rollback.

It does not own `atlas-pm-sync.service`; managed PM deployment remains
exclusively in `docs/runbooks/pm-runtime-deployment.md`. It also does not
replace the Phase 15 milestone/design contract in
`docs/atlas/multi-agent-delivery-control.md`: that document owns why and what
each ATLAS-253 gate must prove, while this runbook owns how the operator loads
and proves the required Symphony runtime identity.

Reading or editing this documentation authorises no live gate, service,
delivery-policy or workload action. Gate 1 remains operator-paused until its
separate prerequisites and exact live workload are ratified.

## 1. Supported runtime facts and authority

The supported procedure identifier is exactly
`vps-systemd-immutable-workflow-readback-v1`. It is limited to the
operator-owned `atlas-symphony.service` boundary and frozen Symphony release
`e5c5e48917e9e91ffb6709ab5a2a02c5af16bf02`. A later release is unsupported
until the operator ratifies it and the repository records the replacement.

The sole configured worker ceiling is
`WORKFLOW.md.agent.max_concurrent_agents`; the operator alone may edit it.
The committed-main ceiling remains `1` while Phase 15 is open,
`max_turns` remains `10`, and the only controlled ramp is
`1 -> 3 -> 5 -> 7 -> 10`. Values 3, 5 and 7 are milestone-branch-only and
never independently mergeable to `main`. Ten is a proven maximum, not a
utilisation target.

The dedicated branch is exactly
`phase-15-atlas-253-ceiling-ramp`. The active Atlas delivery-policy ceiling is
an admission-side mirror, not a second Symphony ceiling. Admission remains
paused while a new runtime identity is being established. No endpoint, CLI,
agent, CI job or automation gains authority to edit the workflow, runtime,
policy, acceptance evidence or milestone receipts through this procedure.

## 2. Immutable workflow activation and exact runtime readback

Editing the branch does not change the running VPS. Before every gate the
operator must identify the exact branch commit and `WORKFLOW.md` blob, use a
ratified reload/restart procedure, and capture bounded runtime evidence that
the active process loaded that exact commit, ceiling and unchanged
`max_turns: 10`.

**Current disposition: the managed VPS runtime procedure is supported.** Its
identifier is exactly `vps-systemd-immutable-workflow-readback-v1`. It is
limited to the operator-owned `atlas-symphony.service` boundary and the frozen
Symphony release
`e5c5e48917e9e91ffb6709ab5a2a02c5af16bf02`. A later release is unsupported
until the operator ratifies it and this repository registers that replacement.
The release contains the process-owned `GET /api/v1/runtime` readback. That
endpoint returns the cached accepted `WorkflowStore` identity; it does not
reread `WORKFLOW.md` for each request, and a failed reload preserves the last
known good identity.

For every Gate 1/3/5/7/10, the operator performs this exact sequence while
admission remains paused:

1. Fetch current `origin/main`, update the dedicated milestone branch through
   the operator-owned exact-current-main rebase lane and identify one immutable
   `<gate-commit>`. Record the exact commit and require its merge base to equal
   the fetched main identity.
2. Verify that `<gate-commit>:WORKFLOW.md` declares the expected gate ceiling
   and unchanged `max_turns: 10` with the milestone doc-linter and workflow
   contract commands below. Record the Git object identity with
   `git rev-parse <gate-commit>:WORKFLOW.md`; no moving branch name may
   substitute for `<gate-commit>` after this point.
3. Choose a new gate-specific immutable runtime file in the operator-owned VPS
   runtime area. Require that it does not already exist, materialise the exact
   bytes with `git show <gate-commit>:WORKFLOW.md > <immutable-workflow-file>`,
   make it read-only and calculate `sha256sum <immutable-workflow-file>`. Record
   the Git workflow blob and the lowercase 64-character content SHA-256. The
   raw path and workflow contents remain outside the retained Atlas receipt.
4. Through the existing operator-owned systemd configuration boundary, point
   `atlas-symphony.service` at that exact immutable workflow file. Do not copy
   the service environment or environment-file contents into milestone
   evidence. Run `systemctl daemon-reload`, then operator-restart exactly
   `atlas-symphony.service` and require it to return active.
5. Read `MainPID` with
   `systemctl show --property MainPID --value atlas-symphony.service`, require a
   live non-zero process and inspect that PID's command line. It must identify
   the intended immutable workflow file. Independently require the deployed
   Symphony release provenance to equal the ratified commit. Retain only the
   bounded instance alias, service unit and ratified release commit—not the
   command line or raw path.
6. Call the process-owned `/api/v1/runtime` endpoint. Require its cached
   `workflow_content_sha256`, configured ceiling and `max_turns` to equal the
   materialised SHA-256, the gate level and ten. A missing response, failed
   reload, last-known-good identity from a different gate or any mismatch stops
   the gate before workload admission.
7. Record load and proof timestamps and construct the bounded runtime identity
   described below. Only after the operator confirms that process proof and its
   canonical identity may the operator deliberately activate a new Atlas
   delivery-policy revision coherent with this gate, change the policy from
   paused to running and admit the predeclared workload.

The live runtime receipt contains exactly the bounded `instance_id`,
`supported_procedure_id`, `service_unit`, `symphony_commit_sha`,
`workflow_content_sha256`, `loaded_commit_sha`, `workflow_blob_sha`,
`configured_ceiling`, `max_turns`, `loaded_at`, `proof_observed_at` and
`proof_identity`. `service_unit` must be `atlas-symphony.service`; the Symphony
commit must be the ratified release above; the loaded commit/blob must equal
the gate receipt; the content digest is lowercase SHA-256; the ceiling must
equal the gate; `max_turns` must be ten; and timestamps must be ordered, fresh
and no later than workload admission. `proof_identity` is the SHA-256 of the
UTF-8 canonical JSON object containing the other selected fields, with keys
sorted, ASCII escaping and compact separators. Timestamps are normalised to
UTC `Z` before hashing. Raw provider payloads, environment values, credentials,
secret-bearing paths, workflow or prompt contents, process command lines and
arbitrary process environment never enter the receipt.

`fixture-only-no-live-runtime-v1` remains accepted for
fixture/schema regression only. It is never production runtime evidence and
cannot establish that the VPS loaded a gate configuration. The harness remains
unconditionally
offline and read-only: either procedure can only validate a supplied receipt;
neither creates transition, closure, deployment, policy or runtime-mutation
authority.

For rollback, keep admission paused and point `atlas-symphony.service` back to
the previous proven gate's exact immutable workflow file. Run `systemctl
daemon-reload` and operator-restart the named service, then recapture
MainPID/process identity and require `/api/v1/runtime` to report the previous
content SHA-256, ceiling and `max_turns: 10`. Only after that process-owned
readback succeeds may the operator activate a policy revision coherent with
the restored ceiling and resume. Never infer rollback from a branch edit, a
checkout read or a service restart result alone.

## 3. Controlled edit and common preflight

### Exact edit and common preflight

Gate 1 observes the unchanged declaration. After a gate passes, the operator
changes only the scalar line in the branch front matter and commits it on the
same milestone branch:

```yaml
agent:
  max_concurrent_agents: <next-level>
```

The only permitted sequence is `1 -> 3`, `3 -> 5`, `5 -> 7`, then `7 -> 10`.
The prompt body below the front matter, `max_turns: 10` and every other workflow
field remain byte-for-byte unchanged. Before loading a level, the operator
validates the checkout with:

```bash
uv run python -m atlas.tools.doc_linter --repo . \
  --symphony-milestone-level <1|3|5|7|10>
ATLAS_SYMPHONY_MILESTONE_LEVEL=<1|3|5|7|10> \
  uv run pytest tests/test_workflow_contract.py \
  tests/test_symphony_ceiling_doc_linter.py
```

The explicit validation context derives the checked-out branch and accepts only
the exact dedicated branch at the declared level. Ordinary CI omits this
context and therefore continues to reject an open-Phase-15 checkout at 3, 5, 7
or 10; milestone validation is preflight evidence, never merge authority.
Before loading a level, the operator verifies all of the following:

1. The checked-out branch name is exactly
   `phase-15-atlas-253-ceiling-ramp`; its head and `WORKFLOW.md` blob are
   recorded, the milestone PR is still unmerged, and a fresh fetch records the
   exact `origin/main` and branch/origin-main merge-base SHAs. The merge base
   must equal that fetched main identity at this gate's setup.
2. Current `origin/main` declares exactly one and keeps `max_turns: 10`. The
   branch declaration is the requested level, is at most ten and differs from
   the last proven declaration only by the one permitted scalar transition;
   Gate 1 starts from the unchanged value one.
3. Every prerequisite PASS receipt named below exists on the milestone PR and
   pins the immediately preceding level. No FAIL receipt remains unresolved.
4. The current active policy is the reconciled one-agent revision before Gate
   1. For later levels, the operator has paused new admission while changing
   the declaration and its policy mirror. The new immutable policy revision
   matches the declaration; its independent working/integration/review
   budgets, Changes Requested reserve, risk/component limits and protected-
   lane registry validate.
5. A complete fresh board observation and successful PM-sync receipt exist;
   there is no unresolved admission or CI-handoff write fence, critical
   delivery-control fault, unexplained CI Pending reactivation, partial pull,
   stale policy or indeterminate Linear result.
6. Symphony is explicitly loaded through the documented VPS procedure from
   the recorded branch head and blob. Bounded process evidence proves the
   active instance loaded that exact identity and gate ceiling before any new
   admission. The operator also records actual active session identities;
   neither this check nor a lower ceiling claims to cancel them.

Any failed preflight is a Gate FAIL without starting the observation window.

## 4. Gate-specific runtime sequence

The Phase 15 design and accepted receipts decide whether a gate may begin.
Once authorised, runtime operation is strictly ordered:

- **Gate 1 — serialized baseline.** Observe and prove the unchanged ceiling-one
  declaration and `max_turns: 10`. Gate 1 performs no ceiling increase.
- **Gate 3 — first controlled increase.** Gate 3 cannot begin without the Gate
  1 PASS receipt. Change only `max_concurrent_agents: 1` to `3`, establish
  the new immutable runtime identity, then activate a coherent policy mirror.
- **Gate 5 — stable review pressure.** Gate 5 cannot begin without the Gate 3
  PASS receipt. Change only `3` to `5` and repeat the same load/readback
  proof before admission resumes.
- **Gate 7 — lanes, recovery and acceptance capacity.** Gate 7 cannot begin
  without the Gate 5 PASS receipt. Change only `5` to `7` and prove the
  runtime identity before the design-owned exercises begin.
- **Gate 10 — maximum, not target.** Gate 10 cannot begin without the Gate 7
  PASS receipt, Phase 14 closure and the design-owned acceptance-throughput
  prerequisites. Change only `7` to `10`.

Every level has one fixed 60-minute window under the design-owned observation
and decision rules. Runtime identity, gate receipt and active policy must agree
before the first admission. Occupancy below the declared ceiling is acceptable.

## 5. Stop, rollback and non-closure

At any failed preflight, identity mismatch, failed readback, service failure,
policy/runtime disagreement or design-owned gate failure, keep admission paused.
Restore the last proven immutable workflow and follow the rollback readback in
section 2. Then activate a coherent policy revision only after the previous
runtime identity is process-proven.

Do not terminate sessions, cancel workers or delete workspaces merely to make a
lower ceiling appear true. Post the bounded FAIL receipt required by the
milestone design with the rollback commit and retained/restored level. The
milestone PR stays unmerged and Phase 15 remains open. A failed or partial gate
never authorises the next value, closure below ten is prohibited, and ordinary
`origin/main` movement does not erase an already accepted PASS receipt; use
the operator-owned rebase lane where the design requires freshness.

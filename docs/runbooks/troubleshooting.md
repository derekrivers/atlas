# Troubleshooting

Symptom-driven diagnosis for Atlas setup, planning, Symphony execution,
CI/acceptance and live-runtime problems.

The governing method is **identity first, owner second, timeline third,
mutation last**. Establish the exact repository/ticket/PR/database/runtime
identities involved before forming a theory. Then identify which component is
allowed to perform the missing state edge. Preserve the anomalous timeline and
collect bounded evidence before repairing anything.

See `operational-practice.md` for the full operator method and
`operator-environment.md` for credential/runtime/environment facts.

## Ticket will not dispatch

**Check first:**

1. Is the Linear/Atlas ticket actually in `Ready for Agent`?
2. Are structural dependencies Done, and did Atlas readiness say the ticket is
   eligible?
3. Did delivery admission hold it for working/integration/review capacity,
   Changes Requested reserve, risk/component limits, protected lanes, paused or
   draining mode?
4. Does `WORKFLOW.md` list the current tracker state as Symphony-active?
5. Does operator preflight pass for tracker/project/model configuration?

Run the current preflight before changing the board:

```bash
uv run atlas preflight --check-model
```

A dependency/admission hold is not a Symphony failure. A ticket parked in
`Needs Human` is deliberately outside dispatch. Fix the owning condition; do not
drag the ticket through states to force a poll.

## Silent empty turns — an agent run no-ops to the turn cap

**Symptom.** A dispatched ticket remains active, no PR appears, and Symphony
continues apparently clean turns until the turn cap.

**Signature.** In debug-level Symphony events, a turn ends with
`codex/event/error` immediately before `task_complete`, while the rollout has
`last_agent_message: null`.

Typical event sequence:

`task_started → turn/started → item_started → item_completed → user_message →`
**`codex/event/error`** `→ thread/status/changed → error → task_complete`.

**Historical reference — ATL-224.** The first observed case was an old
Codex/model compatibility problem. Treat that as the incident that taught the
signature, **not** as the current model diagnosis: `WORKFLOW.md` is the
authority for today's `codex.command` and model pin.

**First response:**

1. Run `uv run atlas preflight --check-model`.
2. Read the live `codex.command` in `WORKFLOW.md`; do not use a remembered model
   name or CLI version.
3. If preflight is green, probe the same pinned model directly outside the
   Symphony loop and read the raw failure.
4. Confirm PATH selects the intended current Codex installation.
5. If the model is reachable, continue from the event/error payload rather than
   assuming every empty turn has the historical cause.

Atlas is not the owner of Symphony's per-turn continuation. A new runtime cause
with this signature belongs in this runbook and, where appropriate, upstream
Symphony rather than in an invented Atlas retry heuristic.

## Git push or publish fails with 403

Atlas has two GitHub credential channels: the Atlas CLI's environment token and
the agent/operator git credential used by `git push`. They can disagree.

**Diagnosis:**

- read `operator-environment.md#github-credentials--two-independent-channels`;
- establish whether the failing action is a GitHub API read or a git push;
- inspect `gh auth status`, configured credential helpers and the repository's
  push remote without pasting secret-bearing output into tickets/logs;
- remember that an exported `GITHUB_TOKEN`/`GH_TOKEN` can shadow an otherwise
  write-capable stored `gh` credential in an interactive shell; and
- for Symphony, use the workflow's non-mutating write-access probe/preflight
  before starting expensive work.

A publish failure does not mean the implementation disappeared. Preserved
Symphony workspaces may contain the validated commit; follow the recovery path
in `operator-environment.md` rather than restarting from scratch.

## The `atlas` command shows unfamiliar flags

If `atlas` reports flags such as `--git`, `--info` or `--init` that this
repository does not define, another package is shadowing Atlas on PATH.

Use:

```bash
which atlas
uv run atlas --help
```

Run repository commands through `uv run atlas ...` from the Atlas root.

## `atlas plan --stubs-only` or `atlas apply` refuses the phase batch

Common causes are:

- planning inputs are dirty/untracked instead of committed;
- the planning-batch base no longer matches the intended overlay;
- active inbox files do not exactly match the manifest;
- a stub points forward to a sibling, to an unknown key, or forms a cycle;
- an exact-path field is invalid/unresolvable;
- the latest proposed PlanRun is stale; or
- the diff contains a shape `apply` is not authorised to accept.

Do not edit the store or generated planning renders to get around the refusal.
Repair/regenerate the complete planning input against current `main`, commit it,
and repeat the integrity gate.

## A mint succeeded, then planning files look wrong or tickets reappear

`atlas apply` changes both the operational store and the working tree. Losing
the working-tree half after a successful apply is dangerous because the key
counter/store have already advanced.

**Signals:**

- committed `docs/planning/tickets.yaml` high-water is lower than the store's
  highest minted key;
- consumed stubs are still committed in the active inbox;
- processed stubs/manifest are absent from the apply-artifact commit;
- a later `--stubs-only` proposal tries to mint already delivered intent; or
- a Context Pack cannot resolve a source anchor that should live in
  `inbox/processed/`.

**Response:**

1. Stop before another plan/apply.
2. Establish the exact store and apply PlanRun involved.
3. Inspect `git status --short` and the planning-render headers.
4. Recover/commit the apply-owned renders and stub retirement using the owning
   runbook. Do not re-run apply against an unretired inbox merely to recreate
   files: that can mint again.

Prevention: immediately `git add -A docs/planning/`, commit and publish after
every successful apply.

## Minted Atlas tickets are missing or wrong in Linear

Minting and Linear publication are separate.

`atlas apply` assigns keys/persists Atlas state. On first pushable sync, PM sync
creates the Linear issue, records `external_linear_id`, pushes the Atlas-owned
definition/context and asserts the mapped initial state.

Run:

```bash
uv run atlas pm sync --once -v
```

Treat create/state-assertion/context-render failures as PM-sync incidents. Do
not repair them by re-running `atlas apply`.

## Context Pack is absent or Linear received definition-only

Start with the source identity rather than the rendered text:

1. confirm the ticket's `source_anchor` and `relevant_docs`;
2. if the ticket came from a stub, confirm the consumed stub is committed under
   `docs/planning/inbox/processed/`;
3. run the relevant supported context commands:

   ```bash
   uv run atlas context show <ATLAS-N>
   uv run atlas context validate <ATLAS-N>
   ```

4. inspect PM-sync pack-render failure counters/evidence; and
5. use the supported context-pack repair path after the source problem is fixed.

Definition-only push is an intentional degradation mode for enumerated render
failures; the unstamped cursor allows a later retry. Do not manually paste a
pack into Linear.

## PR is in CI Pending and does not advance

`CI Pending` is not a Symphony-active state. The agent has handed off and
stopped.

Run the supported PM cadence in verbose one-shot mode:

```bash
uv run atlas pm sync --once -v
```

Then establish:

- the Linear issue has exactly one coherent issue-bound GitHub publication;
- repository, PR number, target branch and current contributor head agree;
- the required check set is complete and determinate for that head;
- the production evidence pull succeeded; and
- the CI-handoff adapter's safe hold/mutation reason.

Typical fail-closed holds include unavailable/ambiguous publication identity,
evidence-ingestion failure, missing/pending/indeterminate checks, stale head or
identity movement.

Do **not** manually drag `CI Pending` into `Review Required` or
`Changes Requested`. The trusted reconciler owns determinate exits.

## CI Pending unexpectedly reactivates into Symphony work

Preserve the exact transition timestamps, issue history and actor/integration
evidence **before** repairing anything.

The known ATLAS-261/262 incident was caused by Linear's
`PR opened → In Progress` GitHub workflow automation; it was disabled on
17 August 2026. Keep that automation disabled. A recurrence is not something to
paper over by dragging the issue back: it is an out-of-ownership transition and,
during a controlled delivery milestone, may be an immediate gate failure.

An authorised `Changes Requested → In Progress` semantic-remediation edge is
different; prove the owner and preceding state before classifying the event.

## Review Required PR is behind/diverged/conflicted after a sibling merge

Mechanical staleness is operator-owned and does not by itself mean the
implementation is wrong.

Start with:

```bash
uv run atlas pr status --pr <N> --repo <owner>/<repo>
```

For a mechanically stale Review Required PR, use the operator rebase lane from
`pr-acceptance.md` (`prepare` → resolve only authorised conflicts → `continue`
→ `publish`). The ticket remains Review Required.

Use `Changes Requested` only for semantic remediation that must return to
Symphony. An out-of-scope or meaning-changing rebase conflict is not
"mechanical"; stop for operator judgement.

## Atlas report and a raw database query disagree

Before interpreting the values, prove both observations addressed the same
store and time.

Check the repository/current directory, any `ATLAS_DATABASE_URL` or `--db`
override, and the actual database target. Regenerate the Atlas report after
establishing that identity; do not compare a remembered/pasted report with a
fresh raw query.

Never resolve the discrepancy by copying an operational SQLite file between
machines and then treating both copies as authoritative.

## Runtime, policy and occupancy disagree during a live gate

Do not collapse these into one "concurrency" number. Establish independently:

1. the configured Symphony ceiling from the **process-owned runtime readback**;
2. the active Atlas delivery policy's approved Symphony ceiling;
3. Atlas working/integration/review/reserve/risk/component/protected-lane
   controls; and
4. observed runtime/board occupancy.

A committed `WORKFLOW.md`, historical policy revision, configured value or
observed worker count cannot substitute for the others. Follow the milestone's
exact runtime-identity procedure and stop admission on any mismatch.

## General rule when the cause is still unclear

Do not widen mutation authority to make diagnosis easier. Collect exact
identities and bounded read-only evidence, reduce the problem to the owner of
one missing edge, and add the final symptom → cause → recovery pattern here once
it is understood.

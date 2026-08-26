---
# ─────────────────────────────────────────────────────────────────────────
# Atlas-product Symphony workflow (canonical contract).
#
# This is the Symphony WORKFLOW.md for the Atlas product. Its front matter and
# prompt spine are the executable, product-invariant dispatch contract. The
# detailed lifecycle they require is owned by
# docs/runbooks/symphony-agent-execution.md and must not be forked per product.
# The ONLY per-product knobs are:
#   - tracker.provider.project_slug   (the operator's Linear project)
#   - hooks.after_create     (the repository the workspace clones)
# A future multi-product render injects exactly those two values per product
# around this same body. Do not copy or diverge the contract body to adapt it
# to another product — change the two knobs, never the contract.
#
# (A YAML comment: ignored by Symphony's parser and by yaml.safe_load alike,
# so it carries no config and touches no acceptance assertion.)
# ─────────────────────────────────────────────────────────────────────────
tracker:
  kind: linear
  provider:
    project_slug: "26cc58f4bc91"
  required_labels: []
  active_states:
    - Ready for Agent
    - In Progress
    - PR Open
    - Changes Requested
  # CI Pending is intentionally absent: it is a non-active Atlas handoff state
  # that releases Symphony working occupancy while consuming integration budget.
  # Linear/GitHub integrations may link evidence but must not mutate Atlas-owned
  # workflow state. A complete PM pull may catch the local mirror up from a
  # Symphony-active predecessor when the poll missed short-lived intermediate
  # states; it records the observed edge without inventing those states. The
  # cadence evaluates at most one issue-bound GitHub publication and its exact
  # canonical evidence pull per tick; any CI Pending reactivation is a milestone
  # failure.
  terminal_states:
    - Done
    - Canceled
    - Duplicate
polling:
  interval_ms: 5000
workspace:
  root: ~/code/atlas-workspaces
hooks:
  after_create: |
    git clone https://github.com/derekrivers/atlas .
  before_run: |
    git fetch origin main

    remote_url="$(git remote get-url origin)"
    repo_for_message="$(printf '%s\n' "${remote_url}" | sed -E 's#^(https?://)[^/@]+@#\1#; s#^git@github.com:#https://github.com/#')"
    head_short="$(git rev-parse --short=12 HEAD)"
    probe_ref="refs/heads/atlas-write-access-probe-${head_short}"
    probe_output="$(mktemp)"
    if ! GIT_TERMINAL_PROMPT=0 git push --dry-run origin "HEAD:${probe_ref}" >"${probe_output}" 2>&1; then
      cat >&2 <<EOF
    Atlas before_run failed: GitHub write-access probe failed for ${repo_for_message}.
    Git output:
    EOF
      cat "${probe_output}" >&2
      printf '\n' >&2
      rm -f "${probe_output}"
      cat >&2 <<EOF
    This Codex session runs with shell_environment_policy.inherit=core, so the operator's exported GITHUB_TOKEN is not visible here.
    The most likely cause is that the agent session's on-disk GitHub credential lacks write access for ${repo_for_message}; the Git output above is the evidence for the exact failure.
    The non-mutating GitHub write-access probe failed before agent work began; fix the agent session's credential path or repository access and dispatch again.
    EOF
      exit 1
    fi
    rm -f "${probe_output}"
  before_remove: |
    true
# `agent.max_concurrent_agents` is the single controlling Symphony worker
# ceiling. The operator is the sole owner of this value. Atlas working,
# review and lane budgets are admission limits, and observed occupied slots
# are runtime facts; neither changes this declaration. Ordinary committed
# `main` stays at 1 while Phase 15 is open; only exact Gate 10 closure may land
# 10. Intermediate values 3, 5 and 7 belong only to the milestone branch.
# Concurrency is bounded by REVIEW throughput, not agent capacity: every PR
# needs a reviewer pass plus the operator's acceptance chain, and concurrent
# branches in Review Required stale each other as siblings merge. Dependency
# chains already serialise most work, so headroom is raised deliberately,
# never as a utilisation target.
agent:
  max_concurrent_agents: 1
  # max_turns is not part of the Symphony concurrency ramp.
  max_turns: 10
# ─────────────────────────────────────────────────────────────────────────
# Current Codex model authority is only the executable `codex.command` below.
# Narrative documents must direct operators here rather than duplicating its
# value. Verify the executable pin before dispatch with:
#   uv run atlas preflight --check-model
# ─────────────────────────────────────────────────────────────────────────
codex:
  command: codex --config shell_environment_policy.inherit=core --config 'model="gpt-5.6-sol"' --config model_reasoning_effort=xhigh app-server
  approval_policy: never
  thread_sandbox: workspace-write
  turn_sandbox_policy:
    type: workspaceWrite
    networkAccess: true
    # Do NOT add writableRoots here for the workspace .git: Symphony
    # injects <workspace>/.git per issue at dispatch time (the Codex
    # app-server rejects relative paths, and absolute paths can't be
    # known statically). See symphony config/schema.ex.
---

You are an autonomous Atlas execution agent working a single Linear ticket,
`{{ issue.identifier }}` — "{{ issue.title }}". Its current tracker state is
`{{ issue.state }}`.

{% if attempt %}
Continuation context:

- This is retry attempt #{{ attempt }}; the ticket is still in an active state.
- Resume from the existing workspace — do not restart from scratch or repeat
  completed investigation, implementation, or validation unless new code
  changes require it.
{% endif %}

## The ticket description is your contract

The ticket description below is your binding contract. If it contains an
`ATLAS CONTEXT PACK v1` block, treat that pack as authoritative. Otherwise,
treat the description's definition fields — objective, acceptance criteria,
non-goals, and definition of done — as binding. Either way, work strictly
inside that scope, and never edit the ticket description — Atlas owns it.

Ticket description:
{% if issue.description %}
{{ issue.description }}
{% else %}
No description provided — treat this as a blocker (see Hard limits).
{% endif %}

## Canonical execution doctrine

Before changing code, publishing, or moving the ticket, read the complete
canonical lifecycle at:

`docs/runbooks/symphony-agent-execution.md`

That runbook owns named-gap handling, repository freshness and rebase details,
deterministic validation, publication readback, Changes Requested input
resolution, same-PR remediation, state-transition mechanics and blocker
reporting. If it is missing, unreadable, ambiguous, or materially inconsistent
with this executable spine, fail closed: do not change code, publish, or mutate
tracker state.

## Procedural skill routing

After reading the canonical runbook above, load the procedural adapter selected
by the current rendered state:

| Current state | Required procedural skill |
| --- | --- |
| `Ready for Agent` | `atlas-ticket-execution` |
| `In Progress` | `atlas-ticket-execution` |
| `PR Open` | `atlas-ticket-execution` |
| `Changes Requested` | `atlas-ticket-remediation` |

These skills package the procedure; they cannot override `WORKFLOW.md` or the
execution runbook. The ordinary execution skill must not handle `Changes
Requested`. `CI Pending` is not an active route and has no procedural skill.

## Executable lifecycle spine

The rendered issue identifier, title, state and description above establish the
current execution identity and scope. The supported agent routes are exactly
`Ready for Agent`, `In Progress`, `PR Open` and `Changes Requested`.
`CI Pending` is deliberately not an active route. `Review Required`,
`Needs Human` and terminal states are handoffs, not agent work states.

- `Ready for Agent` enters `In Progress` only under the canonical runbook.
- `In Progress` implements only the ticket contract. Before publication it
  rebases once onto current `origin/main`, freezes the candidate, calculates
  the exact deterministic validation plan and runs every selected command.
- Every ticket uses one ticket branch and one same-repository PR targeting
  `main`. Every normal PR must carry exactly one issue-bound closing
  relationship, as this standalone line:

  ```text
  Closes {{ issue.identifier }}
  ```

  The PR title independently carries the `ATLAS-<n>` prefix from the issue
  title. Detailed parsing and authenticated publication readback belong to the
  canonical runbook.
- `PR Open` verifies the published frozen head, moves to `CI Pending`, and
  stops in the same turn without polling CI or waiting for review.
- `Changes Requested` must resolve and freeze the exact current-head
  remediation input while the ticket remains in that state. Only then may it
  enter `In Progress`; remediation updates the same branch and PR and returns
  through `PR Open` to `CI Pending`.

Linear/GitHub automation may link publication evidence but must not write
Atlas-owned workflow state. An unexplained `CI Pending` reactivation into an
active Symphony state is an ATLAS-263 milestone failure.

## Hard limits

Never mark your own work `Done`, and never merge the PR. Only the system-tier
CI reconciler may move `CI Pending` to `Review Required` or
`Changes Requested`; the agent never classifies CI. Missing, incomplete,
ambiguous or identity-mismatched execution input routes to the fail-closed
outcome defined by the canonical runbook.

Never author tickets in Linear. Record an out-of-scope finding only through the
canonical `atlas:proposed-follow-up` comment contract in the execution
runbook. Work only in the provided repository checkout.

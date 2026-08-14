---
# ─────────────────────────────────────────────────────────────────────────
# Atlas-product Symphony workflow (canonical contract).
#
# This is the Symphony WORKFLOW.md for the Atlas product. The prompt body
# below the front matter is the canonical, product-invariant Atlas execution
# contract: it governs how every dispatched agent behaves and must not be
# forked per product. The ONLY per-product knobs are:
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
# Codex model requirement — read before editing `codex.command` below.
#   The pinned model="gpt-5.5" needs a current Codex CLI: verified working on
#   0.142.5 (known-good, not a bisected minimum). The snap `codex` package is
#   capped at 0.114.0 and CANNOT run gpt-5.5 (it fails asking for a newer
#   Codex), and an npm-global update may land off PATH. Install the official
#   CLI so it is first on PATH:
#     curl -fsSL https://chatgpt.com/codex/install.sh | sh
#   Entitlement also depends on auth mode: some *-codex models are unavailable
#   on a ChatGPT-account login and require API-key auth.
#   Verify the pin is reachable before dispatch:  atlas preflight --check-model
#   Full detail (PATH hazard, install, C6): docs/atlas/bootstrap-guide.md.
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

## Named design gaps

Some tickets name design gaps for you to resolve — decisions the
contract deliberately leaves open, as distinct from ambiguities
in it. Do not resolve a named gap silently. Post one Linear
comment stating, for each gap: your proposed resolution, its
failure modes, and what you would do differently if it were
rejected. Then move the ticket to `Needs Human` and stop.

The operator ratifies or amends your proposal and returns the
ticket to an active state; resume in the same workspace and
execute the ratified resolution. Resolving a named gap without
ratification is out of scope however reasonable your answer —
the gate exists because these decisions outlive the ticket.

## Ticket key identity

Store keys are issued only by the key authority (KeyCounterRepo high-water marks). No work — hand-dispatched or agent-dispatched — may claim a key ahead of the counter. Hand-dispatched work either mints first (inbox stub → atlas apply → assigned key) or carries a non-key meta label. A claimed-ahead key is a namespace incident requiring counter reconciliation; ATLAS-111..146 and ATLAS-187..192 are the recorded costs.

## Integration discipline

Immediately before opening the PR, before every push (including the initial
branch publish and every later update), and before moving to `Review Required`,
run:

```bash
git fetch origin main && git rebase origin/main
```

Resolve conflicts that touch only files inside the context pack's scope (or,
when no pack is present, the ticket description's definition fields), and note
those resolved conflicts in the PR description. If any conflict touches a file
outside that scope, stop: comment on Linear with the blocker details, move the
ticket to `Needs Human`, and do not improvise.

Under ADR-0008, this ordering is binding: rebase precedes push precedes CI, so
system-tier evidence pins to a head that is current against `origin/main` at
handoff. Agents keep ATLAS-168's pre-handoff discipline: rebase before PR,
before every push, and before moving to `Review Required`. After entering
`Review Required`, never rebase on your own. If a sibling PR merges first and
your branch falls behind `origin/main`, the operator uses the Phase 12
operator-owned rebase lane for mechanical staleness, and that lane leaves the
ticket in `Review Required`. `Changes Requested` is reserved for implementation
or other semantic remediation that must return to Symphony. Any route that
changes the head commit makes old-head evidence and confirmations historical
only; the acceptance chain restarts at the new exact head. The final freshness
check still leaves the existing one-PR freeze-to-manual-merge window: the
operator performs the GitHub merge manually before any sibling PR merges.

## How to move the ticket (you perform every transition)

Route by the current state:

- `Ready for Agent` — your entry point. Move the ticket to `In Progress`,
  branch from `origin/main`, and begin the work the pack defines.
- `In Progress` — implement against the pack. When you have changes, open a PR
  whose title carries the Atlas ticket key embedded at the start of this issue's
  title — the `ATLAS-<n>` prefix before the first `:` — then move the ticket to
  `PR Open`.
- `PR Open` — keep the PR healthy. Once CI is green on the head commit, move the
  ticket to `Review Required` and stop. That is your handoff.
- `Changes Requested` — review feedback has arrived. Resume in the same
  workspace, read the PR review comments, address them, push, and return the
  ticket to `Review Required`.

## Hard limits

- Never mark your own work `Done`, and never merge the PR. `Done` is owned by
  Atlas verification (system-tier CI evidence) plus any required human approval;
  a human merges out of band. Your terminal state is `Review Required`.
- On a blocker, or a genuine ambiguity the pack does not resolve, post a comment
  explaining it and move the ticket to `Needs Human`. Do not improvise outside
  the pack's scope.
- You never author tickets in the tracker. When you notice worthwhile work that
  falls outside this ticket's scope, record it as a follow-up comment (below);
  the Atlas PM Engine, not you, turns those into plan proposals.

## Reporting out-of-scope findings

Post one Linear comment whose first line is exactly the tag, then a one-line
title, then a short rationale:

```text
atlas:proposed-follow-up
<one-line title of the follow-up>
<2–3 sentences: what and why, with enough context for the PM Engine to triage>
```

Work only in the provided repository checkout. Do not touch any other path.

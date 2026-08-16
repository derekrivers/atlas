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

At first entry and again immediately before publication, perform exact
repository and branch checks with `git rev-parse --show-toplevel`,
`git remote get-url origin`, and `git symbolic-ref --quiet --short HEAD`.
The repository root must be the assigned workspace, `origin` must be the
repository cloned for this ticket, and the current branch must be the
ticket-specific branch created from `origin/main`, never `main` or detached.
The PR must use that exact same-repository head branch and target `main`. Treat
any mismatch as a blocker; do not switch repositories, reuse a sibling branch,
or publish from an unverified identity.

Immediately before opening the PR and before every push (the initial branch
publish and any later `Changes Requested` update), run:

```bash
git fetch origin main && git rebase origin/main
```

This is the one successful current-main rebase required for that candidate
publication. Resolve conflicts that touch only files inside the context pack's
scope (or, when no pack is present, the ticket description's definition
fields), and note those resolved conflicts in the PR description. If any
conflict touches a file outside that scope, stop: comment on Linear with the
blocker details, move the ticket to `Needs Human`, and do not improvise.

After the rebase, commit the final implementation and freeze the candidate
head. Calculate `atlas validation-plan` from the exact base and head. Enumerate
changed paths with the read-only, NUL-delimited `git diff --name-status -z
--find-renames --find-copies --no-ext-diff --no-textconv <base> <head> --`:
an ordinary `A`/`D`/`M`/`T`/`U`/`X`/`B` entry contributes its following path,
while an `R` or `C` entry contributes both following paths (old identity, then
new identity). Supply every changed path identity exactly once as
`--changed-path`, every explicit ticket validation requirement, and every
ticket-declared test file. The CLI's own read-only diff verification is final
authority; require its diff and test proofs to pass.
Run every ordered command in the plan and every ticket-declared test file named
by its test targets, in order. Run the `full-sweep` profile only when the plan
selects it as the conservative fallback or the operator explicitly instructs
it. Do not substitute a narrower command for a selected check, and do not add
an unselected complete sweep as a handoff ritual.

A failed selected command or explicit test prevents publication: remain
`In Progress`, fix only in-scope failures, and calculate a new plan for the new
head. Any head change makes the previous plan, exact commands and results, CI
evidence, review evidence and confirmations historical only. Old-head local
results are historical only and never authorise the new candidate.
The acceptance chain restarts at the new exact head.

Only after the exact plan passes, repeat the exact repository and branch checks
and allow one successful publication per candidate head: push that head once,
open or update its single PR, and verify the PR's repository, head branch, base
branch and head SHA. Record the exact base/head, changed paths, selected
profiles, exact commands and results, and explicit test results in the PR
description or one handoff comment. Before moving to `CI Pending`, confirm the
published head is still the validated head; do not rebase or reproduce CI in
the agent session.

Under ADR-0008, this ordering is binding: rebase precedes validation, push and
CI, so system-tier evidence pins to a head current against `origin/main` at
handoff. After entering `CI Pending`, never rebase on your own. If the
system-tier reconciler later moves the ticket to `Review Required` and the
branch falls behind `origin/main` after a sibling merge, the operator uses the
Phase 12 operator-owned rebase lane for mechanical staleness, which leaves the
ticket in `Review Required`. `Changes Requested` is reserved for implementation
or other semantic remediation that must return to Symphony. The final freshness
check still leaves the existing one-PR
freeze-to-manual-merge window: the operator performs the GitHub merge manually
before any sibling PR merges.

## How to move the ticket (you perform every transition)

Route by the current state:

- `Ready for Agent` — your entry point. Move the ticket to `In Progress`,
  fetch current `origin/main`, create the ticket-specific branch from that exact
  ref, perform the identity checks above, and begin the work the pack defines.
- `In Progress` — implement against the pack. If the candidate passes the
  preparation and validation contract above, publish the candidate once in a
  PR whose title carries the Atlas ticket key embedded at the start of this
  issue's title — the `ATLAS-<n>` prefix before the first `:` — then move the
  ticket to `PR Open`. A local failure stays `In Progress` and is not published.
- `PR Open` — verify the published PR still has the exact validated repository,
  branch and head, move the ticket to `CI Pending` and stop in the same turn.
  Do not poll CI, wait for review, or consume another turn. CI owns the next
  state edge.
- `Changes Requested` — a system-tier CI classification or operator review has
  requested semantic remediation. Resume the preserved workspace, read the
  bounded failure or review details, move the ticket to `In Progress`, address
  only that in-scope feedback, and repeat the candidate preparation, validation
  and one-publish handoff through `PR Open` → `CI Pending`.

`CI Pending` is deliberately not an agent route: it is absent from
`tracker.active_states`, so Symphony must neither continue the current session
nor redispatch the issue while CI owns it.

## Hard limits

- Never mark your own work `Done`, and never merge the PR. Your publication
  handoff ends at `CI Pending`. Only the system-tier CI reconciler may move
  `CI Pending` to `Review Required` or `Changes Requested`; Review Required
  records acceptance readiness, not completion. `Done` requires Atlas
  verification at the accepted identity, any required human approval and a
  human merge out of band.
- Never poll or reproduce CI, cancel CI or a worker, skip a selected check,
  choose validation with model judgement, automatically rebase, or claim that
  scoped local confidence is repository-wide authority.
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

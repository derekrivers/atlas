# Troubleshooting

Common setup and execution issues.

## Silent empty turns — an agent run that no-ops to the turn cap

**Symptom.** A dispatched ticket never leaves its active state
(`Ready for Agent` / `In Progress`), no PR appears, and the agent run continues
turn after turn until it hits the turn cap (e.g. 20/20) — with no visible error.
Every turn looks like a clean completion.

**Signature (this is the tell).** In the Symphony log, each turn ends with a
`codex/event/error` notification (emitted at **debug** level only) immediately
before `task_complete`, and the corresponding rollout event carries
`last_agent_message: null`. Symphony logs the turn as "Completed agent run …
Continuing agent run … turn N/20" and re-dispatches. The error is real but
invisible above debug, so the run silently burns its whole budget.

Per-turn event sequence to grep for:
`task_started → turn/started → item_started → item_completed → user_message →`
**`codex/event/error`** `→ thread/status/changed → error → task_complete`
(with `last_agent_message: null`).

**Reference case — ATL-224 (first live smoke dispatch).** The underlying cause
was model entitlement / CLI version: `codex.command` pins `model="gpt-5.5"`,
which the snap-distributed Codex 0.114.0 cannot run. A direct
`codex exec … --config 'model="gpt-5.5"'` returns:
`ERROR: {"detail":"The 'gpt-5.5' model requires a newer version of Codex…"}`
— but inside the agent loop this only appeared as the debug `codex/event/error`
above, so the run produced empty turns to the cap.

**First response.**
1. Run `atlas preflight --check-model` (C6). This probes the *pinned* model and
   reports it unreachable as a loud finding — it catches the model-reachability
   cause of this signature before dispatch.
2. If preflight is green but empty turns persist, probe Codex directly outside
   the loop: `codex exec --skip-git-repo-check --config 'model="<pinned>"' "ping"`
   and read the raw error.
3. Confirm the running Codex is a version that can drive the pinned model —
   snap caps at 0.114.0; install the official CLI
   (`curl -fsSL https://chatgpt.com/codex/install.sh | sh`) and check PATH order.

**Note.** `atlas preflight --check-model` prevents the *known* (model-reachability)
cause. It does not detect every possible empty-turn cause at runtime — Atlas is
not in the turn loop (Symphony owns turn continuation; see
`docs/atlas/symphony-integration.md` §"Retry and failure seam"). If you hit this
signature from a cause preflight can't foresee, the diagnosis path above is the
fallback.

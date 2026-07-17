---
title: "One-shot CLI commands print nothing on success: counters and skip reasons to stdout"
objective: "Every operator-invoked one-shot command reports what it did on stdout — counters for work performed, and named reasons for candidates it declined — so success is distinguishable from a hang, a no-op, and a silent skip without reading the source or querying the store."
context: "Live findings across Phase 8 and 9; this gap cost operator certainty at least four separate times in one session. `atlas/cli.py` never calls `logging.basicConfig`, so Python's default drops INFO and prints only WARNING+; every happy-path line in `atlas/pm/sync.py` and `atlas/learning/scheduler.py` is `logger.info`. Observed consequences: (1) `atlas pm sync` with no flags is the recurring loop and prints nothing — indistinguishable from a hang; (2) `atlas pm sync --repair-packs` completed a repair and printed nothing, so the operator could not tell whether the sweep had run, found nothing, or silently skipped; (3) the same sweep silently skipped its only target because the ticket sat in a non-pushable status, one of six unannounced `continue` branches in the selection loop — the operator lost 20 minutes to a command that declined every candidate without a word; (4) `atlas lessons schedule` (no `--once`) ran an unbounded backfill of ~18 LLM calls in silence and was killed mid-sweep because it looked hung. An operator-invoked, one-shot command that prints nothing on success is a sharper defect than a silent daemon: there is no observable to check and no second signal. Pre-ruled decisions: D-1 one-shot modes print a result summary to STDOUT (not the log): `atlas pm sync --once`, `atlas pm sync --repair-packs`, `atlas lessons schedule --once`. The summary is the command's own result object rendered — counters already exist on `SyncResult`/the scheduler result; this ticket surfaces them, it does not invent metrics. D-2 selection loops that decline candidates name the reason per declined candidate (status not pushable, no external id, cursor already stamped, header already present, ...) — a skip is a result, not silence. D-3 a `--verbose`/`-v` flag enables INFO-level logging for the invocation; the default stays quiet-except-warnings so recurring loops do not spam. D-4 recurring (non-`--once`) modes are out of scope for stdout summaries — they keep logging semantics; `-v` covers them. D-5 no change to WARNING behaviour, to any counter's meaning, or to exit codes. D-6 the runbook's silence-discipline paragraph (`pr-acceptance.md`, when it lands) is amended in the same change to point at the new output instead of prescribing observable-checking."
ticket_type: "bug"
epic_ref: "ATLAS-E10"
acceptance_criteria:
  - "`atlas pm sync --once` prints a result summary to stdout naming pushes, embeds, status pulls, anomalies logged, and unmapped observations; a no-op tick says so explicitly rather than printing nothing."
  - "`atlas pm sync --repair-packs` prints `packs_repaired` and, for every candidate it declined, one line naming the ticket and the reason (a fixture with one repairable and one non-pushable ticket produces exactly one repair line and one named skip line)."
  - "`atlas lessons schedule --once` prints attempted / extracted / declined-as-not-notable / failed counts."
  - "`-v`/`--verbose` enables INFO logging for the invocation; without it, stdout summaries still print and INFO stays suppressed (seed the probe with `assert 1 == 2`, B011)."
  - "Negative: WARNING output, exit codes, and every counter's computed value are unchanged — existing sync/scheduler tests pass unmodified."
non_goals:
  - "No structured/JSON logging framework. No stdout summaries for recurring loop modes. No new counters or metrics. No change to what any command does, only to what it says. No progress bars."
test_requirements:
  - "Fixture-driven with captured stdout, `ATLAS_LIVE_TESTS=0`; seeded defects use `assert 1 == 2` (B011); enumeration pins hold."
definition_of_done:
  - "All acceptance criteria evidenced by named tests; full gate sweep green; PR title carries the minted key."
---

# Silence is not success

Four bites in one session. The observable should be the output.

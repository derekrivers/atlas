---
title: "Schema drift surfaces as a raw OperationalError mid-tick: check alembic head parity as a named precondition"
objective: "A command that needs the store fails fast with a named, actionable error when the database is behind the code's migration head, instead of crashing mid-work with a raw SQLAlchemy OperationalError or writing a partial result."
context: "Two live failures, 2026-07-14, both caused by a merged migration not yet applied to the operator's store. (1) After ATLAS-99 (#195) merged migration 0018 (lessons.confidence nullable), the sync tick's extraction ran the full LLM call, produced a valid DRAFT lesson, and then died at the write: `sqlite3.IntegrityError: NOT NULL constraint failed: lessons.confidence` — the model spend was incurred and thrown away. (2) After ATLAS-106 (#197) merged migration 0019 (tickets.lesson_extraction_attempted_at), every sync tick crashed: `sqlite3.OperationalError: no such column: tickets.lesson_extraction_attempted_at`, recorded as a TickFailure and blocking the loop until the operator diagnosed it by reading the SQL in the traceback. The unit suite cannot catch this class: tests build fresh databases at head, so parity always holds under test and only the live store drifts. The runbook fix (`alembic upgrade head` after merging a migration-carrying PR) is a missing sentence, and a sentence is not a gate. Pre-ruled decisions: D-1 a shared precondition helper compares the store's stamped alembic revision against the code's head and raises a typed, named error identifying both revisions and naming the fix command (`uv run alembic upgrade head`). D-2 it is called by the commands that do expensive or stateful work — `atlas pm sync` (both modes), `atlas lessons schedule`, `atlas lessons extract`, `atlas plan`, `atlas apply` — BEFORE any model call, any Linear call, and any write. Placing it in `Database.__init__` is REJECTED: fixtures construct stores directly and the blast radius is the whole suite for no gain. D-3 the CLI maps the typed error to a clean one-line `EXIT_PRECONDITION`, consistent with the cold-database contract (ATLAS-130) and the ATLAS-108 lesson. D-4 an unstamped/cold database keeps its existing cold-database behaviour — this ticket adds the DRIFT case, it does not re-litigate the cold case. D-5 read-only reporters (`atlas pm report`, `atlas lessons report`, `atlas lessons review`) are out of scope: they already fail cleanly and blocking a read on drift helps nobody. D-6 no auto-upgrade. Atlas never migrates the operator's store as a side effect of another command — the error names the command, the operator runs it."
ticket_type: "bug"
epic_ref: "ATLAS-E10"
acceptance_criteria:
  - "A fixture store stamped one revision behind head causes `atlas pm sync --once` to exit EXIT_PRECONDITION with a one-line message naming the store's revision, the code's head, and `alembic upgrade head` — before any Linear call or model call is made (assert the fakes recorded zero calls)."
  - "`atlas lessons schedule --once` and `atlas lessons extract <KEY>` fail the same way on a drifted store, with no LLM call made — the ATLAS-99 incident (spend incurred then discarded) cannot recur."
  - "`atlas plan` and `atlas apply` fail the same way on a drifted store before any write."
  - "Negative: a store at head runs unchanged — every existing sync/scheduler/plan test passes unmodified; a cold/never-migrated store keeps its current cold-database error, not the new drift error (seed the probe with `assert 1 == 2`, B011)."
  - "Read-only reporters are unaffected: `atlas pm report` on a drifted store behaves exactly as today."
non_goals:
  - "No automatic migration. No change to cold-database behaviour. No check inside Database.__init__. No new migration. No change to any command's success path."
test_requirements:
  - "Fixture-driven, `ATLAS_LIVE_TESTS=0`; drift simulated by stamping a fixture store at a prior revision; seeded defects use `assert 1 == 2` (B011); enumeration pins hold."
definition_of_done:
  - "All acceptance criteria evidenced by named tests; local-development.md or the acceptance runbook names the drift error and its fix in the same change; full gate sweep green; PR title carries the minted key."
---

# Fail before the spend, not after

The suite tests fresh databases. Only the live store drifts.

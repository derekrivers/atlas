"""ATLAS-39: the `atlas deps` CLI surface.

Drives main(["deps", ...], database=db) against an in-memory SQLite database
seeded with tickets and dependencies, mirroring tests/test_cli.py: asserts on
capsys output and the documented exit codes. The CLI is a thin wrapper over
the Phase 3 functions, so these tests prove the wiring, the validate-first
refusal, and the effort write-path — not the computations themselves (those
are covered by test_readiness/test_blockers/test_critical_path/etc.).

Falsifiable, with the wrong answer named:

- `ready` lists the ready ticket and a not-ready one is ABSENT;
- `validate` on a cyclic fixture exits EXIT_PRECONDITION and names the cycle,
  and on a clean fixture exits EXIT_OK;
- `critical-path` on a cyclic graph REFUSES (emits no path);
- `unlocks KEY` returns the expected dependents; an unknown key is clean
  (EXIT_PRECONDITION, no traceback);
- `effort KEY 0` and a negative are REJECTED and not persisted; `effort KEY 5`
  then `--json critical-path` shows the effort drove the path;
- `effort KEY --clear` sets null;
- a `--json` invocation is asserted by PARSING, not string-matching.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from test_models_validation import dependency_kwargs, ticket_kwargs

from atlas.cli import EXIT_OK, EXIT_PRECONDITION, main
from atlas.core.models import Ticket, TicketDependency
from atlas.storage import Database, TicketDependencyRepo, TicketRepo


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def make_ticket(key: str, **overrides: object) -> Ticket:
    """A planned ticket with one acceptance criterion by default — eligible
    for readiness unless an override (status, missing criteria) breaks it."""
    return Ticket(
        **ticket_kwargs()
        | {
            "id": uuid4(),
            "key": key,
            "status": "planned",
            "acceptance_criteria": ["criterion"],
        }
        | overrides
    )


def depends_on(source: Ticket, target: Ticket) -> TicketDependency:
    """``source depends_on target`` — projected as the edge source -> target."""
    return TicketDependency(
        **dependency_kwargs()
        | {
            "id": uuid4(),
            "source_ticket_id": source.id,
            "target_entity_type": "ticket",
            "target_entity_id": target.id,
            "dependency_type": "depends_on",
        }
    )


def seed(db: Database, tickets: list[Ticket], deps: list[TicketDependency]) -> None:
    ticket_repo = TicketRepo(db)
    dep_repo = TicketDependencyRepo(db)
    for ticket in tickets:
        ticket_repo.add(ticket)
    for dependency in deps:
        dep_repo.add(dependency)


# --- ready -----------------------------------------------------------------


def test_ready_lists_ready_and_omits_not_ready(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    # ATLAS-1 depends on the still-open ATLAS-2 -> not ready; ATLAS-3 is a free
    # planned ticket -> ready. ATLAS-2 is in_progress -> not ready (status).
    done = make_ticket("ATLAS-2", status="in_progress")
    blocked_ticket = make_ticket("ATLAS-1")
    free = make_ticket("ATLAS-3")
    seed(db, [done, blocked_ticket, free], [depends_on(blocked_ticket, done)])

    code = main(["deps", "ready"], database=db)
    out = capsys.readouterr().out

    assert code == EXIT_OK
    assert "ATLAS-3" in out
    # The wrong answer: a not-ready ticket appears in the ready list.
    assert "ATLAS-1" not in out
    assert "ATLAS-2" not in out


def test_ready_json_parses_to_keys(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(db, [make_ticket("ATLAS-3")], [])

    main(["deps", "ready", "--json"], database=db)

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ready": ["ATLAS-3"]}


# --- validate --------------------------------------------------------------


def _cyclic(db: Database) -> None:
    """ATLAS-1 depends_on ATLAS-2 depends_on ATLAS-1 — a 2-cycle."""
    a = make_ticket("ATLAS-1")
    b = make_ticket("ATLAS-2")
    seed(db, [a, b], [depends_on(a, b), depends_on(b, a)])


def test_validate_clean_graph_exits_ok(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(db, [make_ticket("ATLAS-1")], [])

    code = main(["deps", "validate"], database=db)

    assert code == EXIT_OK
    assert "valid" in capsys.readouterr().out.lower()


def test_validate_cyclic_graph_refuses_and_names_cycle(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    _cyclic(db)

    code = main(["deps", "validate"], database=db)
    err = capsys.readouterr().err

    assert code == EXIT_PRECONDITION
    # The wrong answer: a silent pass, or a cycle report that does not name the
    # nodes in the cycle.
    assert "cycle" in err.lower()
    assert "ATLAS-1" in err and "ATLAS-2" in err


def test_validate_json_reports_not_ok(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    _cyclic(db)

    code = main(["deps", "validate", "--json"], database=db)
    payload = json.loads(capsys.readouterr().out)

    assert code == EXIT_PRECONDITION
    assert payload["ok"] is False
    assert any(v["type"] == "CycleError" for v in payload["violations"])


# --- critical-path ---------------------------------------------------------


def test_critical_path_cyclic_graph_refuses_no_path(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    _cyclic(db)

    code = main(["deps", "critical-path"], database=db)
    captured = capsys.readouterr()

    # The wrong answer: longest-path runs on a cycle (loops/errors) or emits a
    # path. It must refuse on the validate-first gate instead.
    assert code == EXIT_PRECONDITION
    assert "Critical path" not in captured.out
    assert "cycle" in captured.err.lower()


# --- unlocks ---------------------------------------------------------------


def test_unlocks_returns_expected_dependents(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    # ATLAS-1 and ATLAS-3 both depend only on ATLAS-2 (planned). Finishing
    # ATLAS-2 flips both to ready -> unlocks == [ATLAS-1, ATLAS-3].
    blocker = make_ticket("ATLAS-2")
    dep_a = make_ticket("ATLAS-1")
    dep_b = make_ticket("ATLAS-3")
    seed(
        db,
        [blocker, dep_a, dep_b],
        [depends_on(dep_a, blocker), depends_on(dep_b, blocker)],
    )

    code = main(["deps", "unlocks", "ATLAS-2", "--json"], database=db)
    payload = json.loads(capsys.readouterr().out)

    assert code == EXIT_OK
    assert payload["key"] == "ATLAS-2"
    assert payload["dependents"] == ["ATLAS-1", "ATLAS-3"]
    assert payload["count"] == 2


def test_unlocks_unknown_key_is_clean(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(db, [make_ticket("ATLAS-1")], [])

    code = main(["deps", "unlocks", "ATLAS-404"], database=db)
    captured = capsys.readouterr()

    # The wrong answer: a traceback / crash. It must be a clean precondition
    # exit with the message on stderr.
    assert code == EXIT_PRECONDITION
    assert "ATLAS-404" in captured.err
    assert captured.out == ""


# --- blocked ---------------------------------------------------------------


def test_blocked_key_lists_blocking_target(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = make_ticket("ATLAS-2")  # planned, not done
    dependent = make_ticket("ATLAS-1")
    seed(db, [blocker, dependent], [depends_on(dependent, blocker)])

    code = main(["deps", "blocked", "ATLAS-1", "--json"], database=db)
    payload = json.loads(capsys.readouterr().out)

    assert code == EXIT_OK
    assert payload["key"] == "ATLAS-1"
    assert payload["is_blocked"] is True
    assert payload["targets"] == [{"key": "ATLAS-2", "code": "dependency_not_done"}]


def test_blocked_key_and_high_risk_mutually_exclusive(db: Database) -> None:
    # argparse rejects the combination before any handler runs.
    with pytest.raises(SystemExit):
        main(["deps", "blocked", "ATLAS-1", "--high-risk"], database=db)


def test_blocked_high_risk_report(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = make_ticket("ATLAS-2", risk_level="high")
    dependent = make_ticket("ATLAS-1")
    seed(db, [blocker, dependent], [depends_on(dependent, blocker)])

    code = main(["deps", "blocked", "--high-risk", "--json"], database=db)
    payload = json.loads(capsys.readouterr().out)

    assert code == EXIT_OK
    assert payload["high_risk_blockers"] == [
        {
            "target": "ATLAS-2",
            "risk_level": "high",
            "blocks": ["ATLAS-1"],
            "blocked_count": 1,
        }
    ]


# --- effort ----------------------------------------------------------------


def test_effort_zero_rejected_and_not_persisted(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(db, [make_ticket("ATLAS-1")], [])

    code = main(["deps", "effort", "ATLAS-1", "0"], database=db)

    assert code == EXIT_PRECONDITION
    # The wrong answer: 0 silently persisted. It must remain null.
    assert TicketRepo(db).get_by_key("ATLAS-1").estimated_effort is None  # type: ignore[union-attr]


def test_effort_negative_rejected_and_not_persisted(db: Database) -> None:
    seed(db, [make_ticket("ATLAS-1")], [])

    code = main(["deps", "effort", "ATLAS-1", "-3"], database=db)

    assert code == EXIT_PRECONDITION
    assert TicketRepo(db).get_by_key("ATLAS-1").estimated_effort is None  # type: ignore[union-attr]


def test_effort_unknown_key_rejected(db: Database) -> None:
    code = main(["deps", "effort", "ATLAS-404", "5"], database=db)
    assert code == EXIT_PRECONDITION


def test_effort_clear_sets_null(db: Database) -> None:
    seed(db, [make_ticket("ATLAS-1")], [])
    TicketRepo(db).set_estimated_effort("ATLAS-1", 7)

    code = main(["deps", "effort", "ATLAS-1", "--clear"], database=db)

    assert code == EXIT_OK
    assert TicketRepo(db).get_by_key("ATLAS-1").estimated_effort is None  # type: ignore[union-attr]


def test_effort_value_and_clear_together_rejected(db: Database) -> None:
    seed(db, [make_ticket("ATLAS-1")], [])

    code = main(["deps", "effort", "ATLAS-1", "5", "--clear"], database=db)

    assert code == EXIT_PRECONDITION
    # Neither persisted: the conflicting request is refused before any write.
    assert TicketRepo(db).get_by_key("ATLAS-1").estimated_effort is None  # type: ignore[union-attr]


def test_effort_drives_critical_path(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    # Two chains: long null chain (ATLAS-1->2->3, total 3) vs short heavy chain
    # (ATLAS-4->5). Set ATLAS-4/5 to 5 each so the heavy chain (10) overtakes.
    long_chain = [make_ticket(k) for k in ("ATLAS-1", "ATLAS-2", "ATLAS-3")]
    heavy_chain = [make_ticket(k) for k in ("ATLAS-4", "ATLAS-5")]
    seed(
        db,
        long_chain + heavy_chain,
        [
            depends_on(long_chain[0], long_chain[1]),
            depends_on(long_chain[1], long_chain[2]),
            depends_on(heavy_chain[0], heavy_chain[1]),
        ],
    )

    assert main(["deps", "effort", "ATLAS-4", "5"], database=db) == EXIT_OK
    assert main(["deps", "effort", "ATLAS-5", "5"], database=db) == EXIT_OK
    capsys.readouterr()  # discard the effort confirmations

    code = main(["deps", "critical-path", "--json"], database=db)
    payload = json.loads(capsys.readouterr().out)

    assert code == EXIT_OK
    # The wrong answer: the null 3-node chain still wins because effort was
    # cosmetic. The heavy chain must drive the path.
    assert payload["keys"] == ["ATLAS-5", "ATLAS-4"]
    assert payload["total_effort"] == 10

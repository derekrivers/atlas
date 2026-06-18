"""ATLAS-32: the operator-driven write-path that populates
``estimated_effort`` on stored tickets, and proof the field is load-bearing.

Falsifiable, with the wrong answer named:

- a positive effort set through the seam reads back as that value (not null);
- a seeded 0 and a seeded negative are REJECTED, not silently persisted —
  the row stays null;
- null remains accepted (an operator clearing an estimate) and reads back null;
- an unknown key raises rather than silently no-op'ing;
- populated efforts CHANGE the critical path versus the all-null baseline:
  the heavier two-node chain overtakes the longer three-node null chain —
  proving the column drives the result, not cosmetics — while the null chain
  still weights as 1 per node downstream.
"""

from pathlib import Path
from uuid import uuid4

import pytest
from test_models_validation import dependency_kwargs, ticket_kwargs

from atlas.core.models import Ticket, TicketDependency
from atlas.dependencies import build_dependency_graph, critical_path
from atlas.storage import (
    Database,
    EffortValidationError,
    TicketDependencyRepo,
    TicketNotFoundError,
    TicketRepo,
)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def make_ticket(key: str, *, effort: int | None = None) -> Ticket:
    """A planned (non-terminal) ticket so it participates in the critical
    path; effort defaults to null, the "not yet estimated" state."""
    return Ticket(
        **ticket_kwargs()
        | {
            "id": uuid4(),
            "key": key,
            "status": "planned",
            "estimated_effort": effort,
            "acceptance_criteria": ["criterion"],
        }
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


# --- the write-path and its validation (G1, G2) ---------------------------


def test_set_positive_effort_persists_and_reads_back(db: Database) -> None:
    repo = TicketRepo(db)
    repo.add(make_ticket("ATLAS-100"))

    returned = repo.set_estimated_effort("ATLAS-100", 7)

    assert returned.estimated_effort == 7
    # Read back through a fresh query: it persisted, it is not just echoed.
    assert repo.get_by_key("ATLAS-100").estimated_effort == 7  # type: ignore[union-attr]


def test_zero_effort_rejected_and_not_persisted(db: Database) -> None:
    repo = TicketRepo(db)
    repo.add(make_ticket("ATLAS-101"))

    with pytest.raises(EffortValidationError):
        repo.set_estimated_effort("ATLAS-101", 0)

    # The wrong answer: 0 silently persisted. It must remain null.
    assert repo.get_by_key("ATLAS-101").estimated_effort is None  # type: ignore[union-attr]


def test_negative_effort_rejected_and_not_persisted(db: Database) -> None:
    repo = TicketRepo(db)
    repo.add(make_ticket("ATLAS-102"))

    with pytest.raises(EffortValidationError):
        repo.set_estimated_effort("ATLAS-102", -3)

    assert repo.get_by_key("ATLAS-102").estimated_effort is None  # type: ignore[union-attr]


def test_null_effort_accepted_clears_estimate(db: Database) -> None:
    repo = TicketRepo(db)
    repo.add(make_ticket("ATLAS-103", effort=5))

    returned = repo.set_estimated_effort("ATLAS-103", None)

    assert returned.estimated_effort is None
    assert repo.get_by_key("ATLAS-103").estimated_effort is None  # type: ignore[union-attr]


def test_unknown_key_rejected(db: Database) -> None:
    with pytest.raises(TicketNotFoundError):
        TicketRepo(db).set_estimated_effort("ATLAS-404", 3)


# --- the field is load-bearing on the critical path -----------------------


def _seed_two_chains(db: Database) -> None:
    """Two independent depends_on chains, all efforts null:

    - L (long):  ATLAS-1 depends_on ATLAS-2 depends_on ATLAS-3  (3 nodes)
    - H (heavy): ATLAS-4 depends_on ATLAS-5                      (2 nodes)
    """
    tickets = TicketRepo(db)
    deps = TicketDependencyRepo(db)
    long_chain = [make_ticket(k) for k in ("ATLAS-1", "ATLAS-2", "ATLAS-3")]
    heavy_chain = [make_ticket(k) for k in ("ATLAS-4", "ATLAS-5")]
    for ticket in long_chain + heavy_chain:
        tickets.add(ticket)
    deps.add(depends_on(long_chain[0], long_chain[1]))
    deps.add(depends_on(long_chain[1], long_chain[2]))
    deps.add(depends_on(heavy_chain[0], heavy_chain[1]))


def test_null_baseline_picks_the_longer_chain_weighting_each_null_as_one(
    db: Database,
) -> None:
    _seed_two_chains(db)

    baseline = critical_path(build_dependency_graph(db))

    # All null -> every node weighs 1, so the 3-node chain (total 3) beats the
    # 2-node chain (total 2). Execution order is reverse-topological.
    assert baseline.keys == ("ATLAS-3", "ATLAS-2", "ATLAS-1")
    assert baseline.total_effort == 3
    assert [step.effort for step in baseline.steps] == [1, 1, 1]


def test_populated_efforts_flip_the_critical_path(db: Database) -> None:
    _seed_two_chains(db)
    baseline = critical_path(build_dependency_graph(db))

    # Make the 2-node chain heavy enough to overtake the 3-node null chain.
    tickets = TicketRepo(db)
    tickets.set_estimated_effort("ATLAS-4", 10)
    tickets.set_estimated_effort("ATLAS-5", 10)

    populated = critical_path(build_dependency_graph(db))

    # The wrong answer: the path is unchanged because effort is cosmetic.
    assert populated.keys != baseline.keys
    assert populated.keys == ("ATLAS-5", "ATLAS-4")
    assert populated.total_effort == 20
    assert [step.effort for step in populated.steps] == [10, 10]

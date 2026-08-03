"""ATLAS-248 append-only admission-run persistence and orchestration."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from test_admission import FrozenClock
from test_delivery_snapshot import (
    NOW,
    PRODUCT_ID,
    issue,
    policy,
    snapshot,
    ticket,
)
from test_models_validation import product_kwargs

from atlas.core.models import AdmissionRun, Product, Ticket
from atlas.core.models.ticket import TicketStatus
from atlas.dependencies import project_graph
from atlas.orchestration import record_admission_run
from atlas.pm import evaluate_admission
from atlas.storage import (
    AdmissionRunRepo,
    Database,
    ProductRepo,
    TicketRepo,
)
from atlas.storage.tables import (
    AdmissionRunRow,
    DeliveryAdmissionPolicyRevisionRow,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def seed_run(db: Database) -> tuple[Ticket, AdmissionRun]:
    ProductRepo(db).add(
        Product(**(product_kwargs() | {"id": PRODUCT_ID, "key": "ATLAS"}))
    )
    candidate = TicketRepo(db).add(ticket("ATLAS-1", TicketStatus.PLANNED))
    selected_policy = policy()
    values = selected_policy.model_dump(mode="json")
    values["id"] = selected_policy.id
    values["product_id"] = selected_policy.product_id
    values["created_at"] = selected_policy.created_at
    with db.session() as session, session.begin():
        session.add(DeliveryAdmissionPolicyRevisionRow(**values))
    observed = snapshot([candidate], [issue(candidate)])
    run = evaluate_admission(
        graph=project_graph([candidate], [], [], []),
        tickets=[candidate],
        policy=selected_policy,
        snapshot=observed,
        continuously_eligible_since={candidate.key: NOW},
        clock=FrozenClock(NOW + timedelta(hours=1)),
    )
    return candidate, run


def test_ac5_orchestration_persists_returned_run_without_recalculation(
    db: Database,
) -> None:
    candidate, run = seed_run(db)

    persisted = record_admission_run(db, run)

    assert persisted is run
    assert AdmissionRunRepo(db).get(run.id) == run
    assert AdmissionRunRepo(db).list_for_product(PRODUCT_ID) == [run]
    assert run.selected_ticket_id == candidate.id


def test_ac5_admission_history_rejects_update_and_delete(db: Database) -> None:
    _candidate, run = seed_run(db)
    record_admission_run(db, run)

    with (
        pytest.raises(sa.exc.IntegrityError, match="append-only"),
        db.engine.begin() as connection,
    ):
        connection.execute(
            sa.update(AdmissionRunRow)
            .where(AdmissionRunRow.id == run.id)
            .values(selected_ticket_key="ATLAS-999")
        )

    with (
        pytest.raises(sa.exc.IntegrityError, match="append-only"),
        db.engine.begin() as connection,
    ):
        connection.execute(
            sa.delete(AdmissionRunRow).where(AdmissionRunRow.id == run.id)
        )

    assert AdmissionRunRepo(db).get(run.id) == run


def test_ac5_storage_has_no_raw_linear_payload_column() -> None:
    assert set(AdmissionRunRow.__table__.columns.keys()) == {
        "id",
        "schema_version",
        "product_id",
        "policy_id",
        "policy_revision",
        "policy_fingerprint",
        "snapshot_fingerprint",
        "snapshot_observed_at",
        "evaluated_at",
        "selected_ticket_id",
        "selected_ticket_key",
        "decisions",
        "created_by_type",
        "created_by_id",
    }


def test_ac5_migration_installs_and_removes_append_only_triggers(
    tmp_path: Path,
) -> None:
    url = f"sqlite:///{tmp_path}/migrated.db"
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location", str(REPO_ROOT / "atlas" / "storage" / "migrations")
    )
    config.set_main_option("sqlalchemy.url", url)

    command.upgrade(config, "head")

    engine = sa.create_engine(url)
    with engine.connect() as connection:
        assert "admission_runs" in sa.inspect(connection).get_table_names()
        triggers = {
            row[0]
            for row in connection.execute(
                sa.text(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND name LIKE 'admission_runs_%'"
                )
            )
        }
    assert triggers == {
        "admission_runs_no_update",
        "admission_runs_no_delete",
    }

    command.downgrade(config, "0025")

    with engine.connect() as connection:
        assert "admission_runs" not in sa.inspect(connection).get_table_names()

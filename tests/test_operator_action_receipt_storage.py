"""Operator action receipt storage and append-only guards."""

from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from test_debt_item_storage import public_methods
from test_operator_action_receipt_model import operator_action_receipt_kwargs

from atlas.core.models import OperatorActionReceipt
from atlas.storage import Database, NaiveDatetimeError, OperatorActionReceiptRepo
from atlas.storage.tables import OperatorActionKeyRow, OperatorActionReceiptRow


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def a_receipt(**overrides: Any) -> OperatorActionReceipt:
    return OperatorActionReceipt(
        **operator_action_receipt_kwargs() | {"id": uuid4()} | overrides
    )


def seed_reservation(db: Database, receipt: OperatorActionReceipt) -> None:
    with db.session() as session, session.begin():
        session.add(
            OperatorActionKeyRow(
                idempotency_key_identity=receipt.idempotency_key_identity,
                request_fingerprint=receipt.request_fingerprint,
                receipt_id=receipt.id,
                correlation_id=receipt.correlation_id,
                action=receipt.action,
                target_type=receipt.target_type,
                target_id=receipt.target_id,
                created_by_type=receipt.created_by_type.value,
                created_by_id=receipt.created_by_id,
                created_at=receipt.created_at,
            )
        )


def test_record_round_trips_terminal_receipt(db: Database) -> None:
    repo = OperatorActionReceiptRepo(db)
    receipt = a_receipt()
    seed_reservation(db, receipt)

    assert repo.record(receipt) == receipt
    assert repo.get(receipt.id) == receipt
    assert repo.get_by_idempotency_key_identity(receipt.idempotency_key_identity) == (
        receipt
    )


def test_repo_exposes_append_and_queries_only() -> None:
    assert public_methods(OperatorActionReceiptRepo) == {
        "add",
        "get",
        "get_by_idempotency_key_identity",
        "list",
        "record",
    }


def test_no_mutator_methods_exist() -> None:
    surface = public_methods(OperatorActionReceiptRepo)
    for forbidden in ("update", "delete", "remove", "finalize", "set_status"):
        assert forbidden not in surface, forbidden


def test_re_recording_a_persisted_receipt_raises(db: Database) -> None:
    repo = OperatorActionReceiptRepo(db)
    receipt = a_receipt()
    seed_reservation(db, receipt)
    repo.record(receipt)

    with pytest.raises(sa.exc.IntegrityError):
        repo.record(a_receipt(id=receipt.id))


@pytest.mark.parametrize("method_name", ["add", "record"])
def test_public_writers_revalidate_metadata_allowlist(
    db: Database,
    method_name: str,
) -> None:
    opaque_credential = "mF9kQ7vLc2xP8nR4wT6yB3dH5jS1aG0z"
    unsafe = OperatorActionReceipt(**operator_action_receipt_kwargs()).model_copy(
        update={"result_metadata": {"neutral": opaque_credential}}
    )
    seed_reservation(db, unsafe)

    with pytest.raises(ValidationError, match="result_metadata") as raised:
        getattr(OperatorActionReceiptRepo(db), method_name)(unsafe)

    assert opaque_credential not in str(raised.value)
    with db.session() as session:
        assert session.get(OperatorActionReceiptRow, unsafe.id) is None


@pytest.mark.parametrize("method_name", ["add", "record"])
@pytest.mark.parametrize(
    ("field_name", "prohibited"),
    [
        ("result_code", "mf9kq7vlc2xp8nr4wt6yb3dh5js1ag0z"),
        ("before_status", "Promote this private lesson narrative verbatim."),
        ("after_status", '{"private_command":"do not copy"}'),
        ("after_status", "raw-test-output-with-customer-data"),
    ],
    ids=["opaque-result-code", "lesson-before", "request-after", "evidence-after"],
)
def test_public_writers_revalidate_controlled_receipt_vocabularies(
    db: Database,
    method_name: str,
    field_name: str,
    prohibited: str,
) -> None:
    unsafe = OperatorActionReceipt(**operator_action_receipt_kwargs()).model_copy(
        update={field_name: prohibited}
    )
    seed_reservation(db, unsafe)

    with pytest.raises(ValidationError, match=field_name) as raised:
        getattr(OperatorActionReceiptRepo(db), method_name)(unsafe)

    assert prohibited not in str(raised.value)
    with db.session() as session:
        assert session.get(OperatorActionReceiptRow, unsafe.id) is None


def test_database_rejects_receipt_update_and_delete(db: Database) -> None:
    receipt = a_receipt()
    seed_reservation(db, receipt)
    OperatorActionReceiptRepo(db).record(receipt)

    with (
        pytest.raises(sa.exc.IntegrityError, match="append-only"),
        db.engine.begin() as connection,
    ):
        connection.execute(
            sa.update(OperatorActionReceiptRow)
            .where(OperatorActionReceiptRow.id == receipt.id)
            .values(result_code="rewritten")
        )

    with (
        pytest.raises(sa.exc.IntegrityError, match="append-only"),
        db.engine.begin() as connection,
    ):
        connection.execute(
            sa.delete(OperatorActionReceiptRow).where(
                OperatorActionReceiptRow.id == receipt.id
            )
        )


def test_database_rejects_reservation_update_and_delete(db: Database) -> None:
    receipt = a_receipt()
    seed_reservation(db, receipt)

    with (
        pytest.raises(sa.exc.IntegrityError, match="append-only"),
        db.engine.begin() as connection,
    ):
        connection.execute(
            sa.update(OperatorActionKeyRow)
            .where(
                OperatorActionKeyRow.idempotency_key_identity
                == receipt.idempotency_key_identity
            )
            .values(action="rewritten")
        )

    with (
        pytest.raises(sa.exc.IntegrityError, match="append-only"),
        db.engine.begin() as connection,
    ):
        connection.execute(
            sa.delete(OperatorActionKeyRow).where(
                OperatorActionKeyRow.idempotency_key_identity
                == receipt.idempotency_key_identity
            )
        )


def test_naive_timestamps_rejected_at_record(db: Database) -> None:
    receipt = a_receipt(created_at=datetime(2026, 8, 2, 12, 0, 0))
    with pytest.raises(NaiveDatetimeError, match="created_at"):
        OperatorActionReceiptRepo(db).record(receipt)

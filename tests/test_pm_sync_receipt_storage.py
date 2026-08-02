"""ATLAS-245: PmSyncReceiptRepo append-only and success timestamp semantics."""

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa
from test_pm_sync_receipt_model import pm_sync_receipt_kwargs

from atlas.core.models import PmSyncReceipt, PmSyncReceiptResult
from atlas.storage import Database, NaiveDatetimeError, PmSyncReceiptRepo


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def public_methods(cls: type) -> set[str]:
    return {
        name
        for name in dir(cls)
        if not name.startswith("_") and callable(getattr(cls, name))
    }


def a_receipt(**overrides: Any) -> PmSyncReceipt:
    return PmSyncReceipt(**pm_sync_receipt_kwargs() | {"id": uuid4()} | overrides)


def test_record_round_trips_model_to_database(db: Database) -> None:
    repo = PmSyncReceiptRepo(db)
    item = a_receipt()

    assert repo.record(item) == item
    assert repo.get(item.id) == item


def test_repo_exposes_append_and_query_only() -> None:
    assert public_methods(PmSyncReceiptRepo) == {
        "add",
        "get",
        "latest_successful_finished_at",
        "list",
        "record",
    }


def test_re_recording_a_persisted_id_raises(db: Database) -> None:
    repo = PmSyncReceiptRepo(db)
    item = a_receipt()
    repo.record(item)

    with pytest.raises(sa.exc.IntegrityError):
        repo.record(a_receipt(id=item.id, result=PmSyncReceiptResult.FAILED))


def test_latest_successful_finished_at_ignores_unsuccessful_receipts(
    db: Database,
) -> None:
    repo = PmSyncReceiptRepo(db)
    started = datetime(2026, 8, 2, 12, tzinfo=UTC)
    first_success = started + timedelta(minutes=1)
    later_partial = started + timedelta(minutes=2)
    latest_success = started + timedelta(minutes=3)
    later_failed = started + timedelta(minutes=4)

    repo.record(
        a_receipt(
            started_at=started,
            finished_at=first_success,
            result=PmSyncReceiptResult.SUCCESS_ZERO_ACTION,
        )
    )
    repo.record(
        a_receipt(
            started_at=later_partial,
            finished_at=later_partial,
            result=PmSyncReceiptResult.PARTIAL,
        )
    )
    repo.record(
        a_receipt(
            started_at=latest_success,
            finished_at=latest_success,
            result=PmSyncReceiptResult.SUCCESS_STATUS_ONLY,
        )
    )
    repo.record(
        a_receipt(
            started_at=later_failed,
            finished_at=later_failed,
            result=PmSyncReceiptResult.FAILED,
        )
    )

    assert repo.latest_successful_finished_at() == latest_success


@pytest.mark.parametrize(
    "result",
    [
        PmSyncReceiptResult.FAILED,
        PmSyncReceiptResult.CANCELLED,
        PmSyncReceiptResult.PARTIAL,
        PmSyncReceiptResult.MALFORMED_PULL,
    ],
)
def test_unsuccessful_only_preserves_null_success_timestamp(
    db: Database, result: PmSyncReceiptResult
) -> None:
    repo = PmSyncReceiptRepo(db)
    repo.record(a_receipt(result=result))

    assert repo.latest_successful_finished_at() is None


def test_list_orders_by_tick_times_then_id(db: Database) -> None:
    repo = PmSyncReceiptRepo(db)
    same_started = datetime(2026, 8, 2, 12, tzinfo=UTC)
    later = same_started + timedelta(minutes=1)
    low_id = uuid4()
    high_id = uuid4()
    if str(low_id) > str(high_id):
        low_id, high_id = high_id, low_id

    repo.record(a_receipt(id=high_id, started_at=same_started, finished_at=later))
    repo.record(a_receipt(id=low_id, started_at=same_started, finished_at=later))
    repo.record(
        a_receipt(
            started_at=same_started - timedelta(minutes=1),
            finished_at=same_started - timedelta(minutes=1),
        )
    )

    ordered = repo.list()
    assert ordered[0].started_at == same_started - timedelta(minutes=1)
    assert [item.id for item in ordered[1:]] == [low_id, high_id]


def test_naive_timestamps_rejected_at_record(db: Database) -> None:
    item = a_receipt(started_at=datetime(2026, 8, 2, 12, 0, 0))
    with pytest.raises(NaiveDatetimeError, match="started_at"):
        PmSyncReceiptRepo(db).record(item)


def test_aware_offset_normalised_to_utc(db: Database) -> None:
    plus_two = timezone(timedelta(hours=2))
    item = a_receipt(
        started_at=datetime(2026, 8, 2, 12, 0, 0, tzinfo=plus_two),
        finished_at=datetime(2026, 8, 2, 12, 5, 0, tzinfo=plus_two),
    )
    repo = PmSyncReceiptRepo(db)
    repo.record(item)

    stored = repo.get(item.id)
    assert stored is not None
    assert stored.started_at.utcoffset() == timedelta(0)
    assert stored.finished_at.utcoffset() == timedelta(0)
    assert stored.started_at == item.started_at
    assert stored.finished_at == item.finished_at

"""Immutable delivery admission policy repository contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from test_delivery_admission_policy_model import policy_spec
from test_models_validation import product_kwargs

from atlas.core.enums import ActorType
from atlas.core.models import DeliveryAdmissionPolicyRevision, Product
from atlas.storage import Database, DeliveryAdmissionPolicyRepo, ProductRepo
from atlas.storage.tables import (
    DeliveryAdmissionPolicyActiveRow,
    DeliveryAdmissionPolicyRevisionRow,
)

NOW = datetime(2026, 8, 2, 14, tzinfo=UTC)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def seed_product(db: Database) -> Product:
    return ProductRepo(db).add(
        Product(**product_kwargs() | {"id": uuid4(), "key": "ATLAS"})
    )


def seed_revision(
    db: Database,
    product_id: UUID,
    revision: int,
    *,
    make_active: bool = True,
) -> DeliveryAdmissionPolicyRevision:
    model = DeliveryAdmissionPolicyRevision(
        **policy_spec().model_dump(),
        id=uuid4(),
        product_id=product_id,
        revision=revision,
        created_by_type=ActorType.HUMAN,
        created_by_id="operator",
        created_at=NOW,
    )
    values = model.model_dump(mode="json")
    values["id"] = model.id
    values["product_id"] = model.product_id
    values["created_at"] = model.created_at
    with db.session() as session, session.begin():
        session.add(DeliveryAdmissionPolicyRevisionRow(**values))
        if make_active:
            current = session.get(DeliveryAdmissionPolicyActiveRow, product_id)
            if current is None:
                session.add(
                    DeliveryAdmissionPolicyActiveRow(
                        product_id=product_id, revision=revision
                    )
                )
            else:
                current.revision = revision
    return model


def public_methods(cls: type) -> set[str]:
    return {
        name
        for name in dir(cls)
        if not name.startswith("_") and callable(getattr(cls, name))
    }


def test_repository_exposes_read_only_policy_history() -> None:
    assert public_methods(DeliveryAdmissionPolicyRepo) == {
        "get_active",
        "get_revision",
        "list_revisions",
    }


def test_repository_reads_active_revision_and_ordered_history(db: Database) -> None:
    product = seed_product(db)
    first = seed_revision(db, product.id, 1)
    second = seed_revision(db, product.id, 2)
    repo = DeliveryAdmissionPolicyRepo(db)

    assert repo.get_revision(product.id, 1) == first
    assert repo.get_active(product.id) == second
    assert repo.list_revisions(product.id) == [first, second]


def test_historical_policy_rows_reject_update_and_delete(db: Database) -> None:
    product = seed_product(db)
    revision = seed_revision(db, product.id, 1)

    with (
        pytest.raises(sa.exc.IntegrityError, match="append-only"),
        db.engine.begin() as connection,
    ):
        connection.execute(
            sa.update(DeliveryAdmissionPolicyRevisionRow)
            .where(DeliveryAdmissionPolicyRevisionRow.id == revision.id)
            .values(working_budget=1)
        )

    with (
        pytest.raises(sa.exc.IntegrityError, match="append-only"),
        db.engine.begin() as connection,
    ):
        connection.execute(
            sa.delete(DeliveryAdmissionPolicyRevisionRow).where(
                DeliveryAdmissionPolicyRevisionRow.id == revision.id
            )
        )

    assert DeliveryAdmissionPolicyRepo(db).get_active(product.id) == revision

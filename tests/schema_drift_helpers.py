"""Test helpers for simulating Alembic stamp drift."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory

from atlas.storage import Database

REPO_ROOT = Path(__file__).resolve().parent.parent


class CapturedOutput(Protocol):
    @property
    def out(self) -> str: ...

    @property
    def err(self) -> str: ...


def alembic_head_and_parent() -> tuple[str, str]:
    config = Config()
    config.set_main_option(
        "script_location", str(REPO_ROOT / "atlas" / "storage" / "migrations")
    )
    script = ScriptDirectory.from_config(config)
    head = script.get_current_head()
    assert head is not None
    revision = script.get_revision(head)
    assert revision is not None
    parent = revision.down_revision
    assert isinstance(parent, str)
    return head, parent


def stamp_database(database: Database, revision: str) -> None:
    with database.engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE IF NOT EXISTS alembic_version "
                "(version_num VARCHAR(32) NOT NULL)"
            )
        )
        connection.execute(sa.text("DELETE FROM alembic_version"))
        connection.execute(
            sa.text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": revision},
        )


def drifted_database(database: Database) -> tuple[str, str]:
    head, parent = alembic_head_and_parent()
    stamp_database(database, parent)
    return head, parent


def assert_schema_drift_message(
    captured: CapturedOutput, *, store_revision: str, code_head: str
) -> None:
    err = captured.err
    assert captured.out == ""
    assert err.count("\n") == 1
    assert "SCHEMA_DRIFT" in err
    assert store_revision in err
    assert code_head in err
    assert "alembic upgrade head" in err
    assert "Traceback" not in err

"""ATLAS-18: DDL contracts per data-model §3.1-§3.10 SQL blocks.

Expected tables are transcribed from the documented SQL — column names,
nullability, DEFAULT clauses, FK targets, UNIQUE and CHECK constraints —
not derived from the ORM, so drift in either direction fails. Plus: the
Alembic baseline matches the ORM metadata, and the full DDL compiles
under the PostgreSQL dialect (the honesty mechanism for a SQLite-only
CI; compile-compatibility is the stated limit of the claim).
"""

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy.dialects import postgresql

from atlas.storage.db import Database
from atlas.storage.tables import Base

REPO_ROOT = Path(__file__).resolve().parent.parent

# Transcribed from the §3 SQL blocks: column -> (nullable, default literal).
NN = False  # NOT NULL
DOCUMENTED_COLUMNS: dict[str, dict[str, tuple[bool, str | None]]] = {
    # §3.1
    "products": {
        "id": (NN, None),
        "key": (NN, None),
        "name": (NN, None),
        "description": (NN, None),
        "vision": (NN, None),
        "status": (NN, None),
        "goals": (NN, "'[]'"),
        "non_goals": (NN, "'[]'"),
        "constraints": (NN, "'[]'"),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
        "created_at": (NN, None),
        "updated_at": (NN, None),
        "archived_at": (True, None),
    },
    # §3.2
    "architecture_decision_records": {
        "id": (NN, None),
        "product_id": (NN, None),
        "number": (NN, None),
        "title": (NN, None),
        "status": (NN, None),
        "context": (NN, None),
        "decision": (NN, None),
        "rationale": (NN, None),
        "consequences": (NN, "'[]'"),
        "alternatives_considered": (NN, "'[]'"),
        "supersedes_adr_id": (True, None),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
        "created_at": (NN, None),
        "updated_at": (NN, None),
    },
    # §3.3
    "epics": {
        "id": (NN, None),
        "product_id": (NN, None),
        "key": (NN, None),
        "title": (NN, None),
        "description": (NN, None),
        "objective": (NN, None),
        "status": (NN, None),
        "priority": (NN, "0"),
        "risk_level": (NN, None),
        "source_anchor": (NN, None),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
        "created_at": (NN, None),
        "updated_at": (NN, None),
        "completed_at": (True, None),
    },
    # §3.4
    "tickets": {
        "id": (NN, None),
        "product_id": (NN, None),
        "epic_id": (True, None),
        "key": (NN, None),
        "title": (NN, None),
        "objective": (NN, None),
        "context": (NN, None),
        "status": (NN, None),
        "ticket_type": (NN, None),
        "risk_level": (NN, None),
        "priority": (NN, "0"),
        "relevant_docs": (NN, "'[]'"),
        "tags": (NN, "'[]'"),
        "component": (True, None),
        "acceptance_criteria": (NN, "'[]'"),
        "non_goals": (NN, "'[]'"),
        "implementation_notes": (NN, "'[]'"),
        "test_requirements": (NN, "'[]'"),
        "documentation_requirements": (NN, "'[]'"),
        "definition_of_done": (NN, "'[]'"),
        "estimated_effort": (True, None),
        "external_linear_id": (True, None),
        "external_github_issue_id": (True, None),
        "linear_synced_at": (True, None),
        "last_observed_linear_state_id": (True, None),
        "status_entered_at": (True, None),
        "review_cycle_count": (NN, "0"),
        "source_anchor": (NN, None),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
        "created_at": (NN, None),
        "updated_at": (NN, None),
        "completed_at": (True, None),
    },
    # §3.5 (post-chore: attribution columns)
    "ticket_dependencies": {
        "id": (NN, None),
        "source_ticket_id": (NN, None),
        "target_entity_type": (NN, None),
        "target_entity_id": (NN, None),
        "dependency_type": (NN, None),
        "reason": (NN, None),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
        "created_at": (NN, None),
    },
    # §3.6 (post-ATLAS-99: confidence nullable until operator promotion)
    "lessons": {
        "id": (NN, None),
        "product_id": (NN, None),
        "status": (NN, "'draft'"),
        "category": (NN, None),
        "title": (NN, None),
        "problem": (NN, None),
        "solution": (NN, None),
        "outcome": (NN, None),
        "confidence": (True, None),
        "related_ticket_ids": (NN, "'[]'"),
        "related_adr_ids": (NN, "'[]'"),
        "tags": (NN, "'[]'"),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
        "created_at": (NN, None),
        "updated_at": (NN, None),
    },
    # §3.7
    "evidence": {
        "id": (NN, None),
        "product_id": (NN, None),
        "ticket_id": (True, None),
        "agent_run_id": (True, None),
        "evidence_type": (NN, None),
        "status": (NN, None),
        "summary": (NN, None),
        "commit_sha": (True, None),
        "external_run_id": (True, None),
        "payload_hash": (True, None),
        "source_uri": (True, None),
        "raw_payload": (NN, "'{}'"),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
        "created_at": (NN, None),
    },
    # §3.8
    "agent_runs": {
        "id": (NN, None),
        "product_id": (NN, None),
        "ticket_id": (True, None),
        "provider": (NN, None),
        "model": (True, None),
        "status": (NN, None),
        "objective": (NN, None),
        "input_context_pack_id": (True, None),
        "output_summary": (True, None),
        "error_summary": (True, None),
        "cost_estimate_usd": (True, None),
        "prompt_tokens": (True, None),
        "completion_tokens": (True, None),
        "started_at": (True, None),
        "completed_at": (True, None),
        "created_at": (NN, None),
    },
    # §3.9
    "context_packs": {
        "id": (NN, None),
        "product_id": (NN, None),
        "ticket_id": (True, None),
        "title": (NN, None),
        "objective": (NN, None),
        "constraints": (NN, "'[]'"),
        "relevant_docs": (NN, "'[]'"),
        "relevant_adrs": (NN, "'[]'"),
        "related_tickets": (NN, "'[]'"),
        "historical_lessons": (NN, "'[]'"),
        "acceptance_criteria": (NN, "'[]'"),
        "risks": (NN, "'[]'"),
        "test_commands": (NN, "'[]'"),
        "definition_of_done": (NN, "'[]'"),
        "rendered_markdown": (NN, None),
        "compression_applied": (NN, "'[]'"),
        "input_doc_shas": (NN, "'{}'"),
        "token_estimate": (True, None),
        "created_at": (NN, None),
    },
    # §3.10
    "plan_runs": {
        "id": (NN, None),
        "product_id": (NN, None),
        "status": (NN, None),
        "input_doc_shas": (NN, "'{}'"),
        "model_provider": (NN, None),
        "model_name": (NN, None),
        "prompt_version": (NN, None),
        "prompt_hash": (NN, None),
        "model_parameters": (NN, "'{}'"),
        "similarity_threshold": (NN, None),
        "raw_output_hash": (NN, None),
        "proposal": (NN, "'{}'"),
        "generation_stages": (NN, "'[]'"),
        "diff_summary": (NN, "'{}'"),
        "failure_reason": (True, None),
        "approved_by": (True, None),
        "created_at": (NN, None),
        "applied_at": (True, None),
    },
    # §3.12
    "key_counters": {
        "prefix": (NN, None),
        "high_water": (NN, "0"),
    },
    # §6.2 (delivery-anomaly record, ATLAS-116): append-only — no status,
    # no updated_at.
    "debt_items": {
        "id": (NN, None),
        "product_id": (NN, None),
        "ticket_id": (NN, None),
        "anomaly_type": (NN, None),
        "summary": (NN, None),
        "observed_at": (NN, None),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
        "created_at": (NN, None),
    },
    # §6.4 (tick-failure record, ATLAS-125): append-only — no status, no
    # updated_at. Tick-level: NO ticket_id and NO product_id, so NO FK.
    "tick_failures": {
        "id": (NN, None),
        "occurred_at": (NN, None),
        "failure_signature": (NN, None),
        "detail": (NN, None),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
    },
    # §6.6 (status-transition record, ATLAS-121): append-only — no status, no
    # created_at, no updated_at. Ticket-scoped: ticket_id is FK-backed (modelled
    # on debt_items), but there is NO product_id.
    "ticket_status_transitions": {
        "id": (NN, None),
        "ticket_id": (NN, None),
        "from_status": (NN, None),
        "to_status": (NN, None),
        "occurred_at": (NN, None),
        "created_by_type": (NN, None),
        "created_by_id": (NN, None),
    },
    # §5.2 (verification-check record, ATLAS-71): NOT evidence — status is an
    # EvidenceStatus outcome but there is no trust tier and no commit pin.
    # Append-only — no updated_at; completed_at is nullable. required defaults
    # TRUE; evidence_ids defaults '[]'. ticket_id is FK-backed and NOT NULL.
    "verification_checks": {
        "id": (NN, None),
        "ticket_id": (NN, None),
        "check_type": (NN, None),
        "status": (NN, None),
        "summary": (NN, None),
        "required": (NN, "TRUE"),
        "evidence_ids": (NN, "'[]'"),
        "created_at": (NN, None),
        "completed_at": (True, None),
    },
}

# Transcribed FK targets: table -> {column: referred table}. Absence is
# contractual too (agent_run_id, input_context_pack_id).
DOCUMENTED_FOREIGN_KEYS: dict[str, dict[str, str]] = {
    "products": {},
    "architecture_decision_records": {
        "product_id": "products",
        "supersedes_adr_id": "architecture_decision_records",
    },
    "epics": {"product_id": "products"},
    "tickets": {"product_id": "products", "epic_id": "epics"},
    "ticket_dependencies": {"source_ticket_id": "tickets"},
    "lessons": {"product_id": "products"},
    "evidence": {"product_id": "products", "ticket_id": "tickets"},
    "agent_runs": {"product_id": "products", "ticket_id": "tickets"},
    "context_packs": {"product_id": "products", "ticket_id": "tickets"},
    "plan_runs": {"product_id": "products"},
    "key_counters": {},
    "debt_items": {"product_id": "products", "ticket_id": "tickets"},
    "tick_failures": {},
    "ticket_status_transitions": {"ticket_id": "tickets"},
    "verification_checks": {"ticket_id": "tickets"},
}

DOCUMENTED_UNIQUES: dict[str, list[list[str]]] = {
    "products": [["key"]],
    "architecture_decision_records": [["product_id", "number"]],
    "epics": [["key"]],
    "tickets": [["key"]],
}


@pytest.fixture
def migrated_db(tmp_path: Path) -> Database:
    db = Database(f"sqlite:///{tmp_path}/atlas.db")
    db.create_all()
    return db


def test_documented_tables_exactly(migrated_db: Database) -> None:
    inspector = sa.inspect(migrated_db.engine)
    assert set(inspector.get_table_names()) == set(DOCUMENTED_COLUMNS)


@pytest.mark.parametrize("table", sorted(DOCUMENTED_COLUMNS), ids=str)
def test_columns_nullability_defaults(migrated_db: Database, table: str) -> None:
    inspector = sa.inspect(migrated_db.engine)
    actual = {
        column["name"]: (bool(column["nullable"]), column["default"])
        for column in inspector.get_columns(table)
    }
    assert actual == DOCUMENTED_COLUMNS[table]


@pytest.mark.parametrize("table", sorted(DOCUMENTED_FOREIGN_KEYS), ids=str)
def test_foreign_keys_exactly(migrated_db: Database, table: str) -> None:
    inspector = sa.inspect(migrated_db.engine)
    actual = {
        fk["constrained_columns"][0]: fk["referred_table"]
        for fk in inspector.get_foreign_keys(table)
    }
    assert actual == DOCUMENTED_FOREIGN_KEYS[table]


def test_fkless_columns_are_deliberate(migrated_db: Database) -> None:
    # data-model §3.7/§3.8: Phase 8 reconstructs agent runs from
    # observation; these columns carry no referential constraint.
    inspector = sa.inspect(migrated_db.engine)
    evidence_fk_columns = {
        fk["constrained_columns"][0] for fk in inspector.get_foreign_keys("evidence")
    }
    agent_run_fk_columns = {
        fk["constrained_columns"][0] for fk in inspector.get_foreign_keys("agent_runs")
    }
    assert "agent_run_id" not in evidence_fk_columns
    assert "input_context_pack_id" not in agent_run_fk_columns


@pytest.mark.parametrize("table", sorted(DOCUMENTED_UNIQUES), ids=str)
def test_unique_constraints(migrated_db: Database, table: str) -> None:
    inspector = sa.inspect(migrated_db.engine)
    actual = [
        sorted(constraint["column_names"])
        for constraint in inspector.get_unique_constraints(table)
    ]
    for expected in DOCUMENTED_UNIQUES[table]:
        assert sorted(expected) in actual


def test_lessons_confidence_check_constraint(migrated_db: Database) -> None:
    inspector = sa.inspect(migrated_db.engine)
    checks = " ".join(
        constraint["sqltext"]
        for constraint in inspector.get_check_constraints("lessons")
    )
    assert "confidence >= 0" in checks
    assert "confidence <= 1" in checks


def test_key_counters_high_water_check_constraint(migrated_db: Database) -> None:
    # §3.12: high_water is non-negative — the monotonic-from-zero floor.
    inspector = sa.inspect(migrated_db.engine)
    checks = " ".join(
        constraint["sqltext"]
        for constraint in inspector.get_check_constraints("key_counters")
    )
    assert "high_water >= 0" in checks


def test_key_counters_primary_key_is_prefix(migrated_db: Database) -> None:
    # §3.12 / gap 1: row identity is the prefix alone — one authoritative
    # counter per prefix, which is what makes no-reuse structural.
    inspector = sa.inspect(migrated_db.engine)
    pk = inspector.get_pk_constraint("key_counters")
    assert pk["constrained_columns"] == ["prefix"]


def test_alembic_upgrades_fresh_db_and_matches_metadata(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path}/migrated.db"
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location", str(REPO_ROOT / "atlas" / "storage" / "migrations")
    )
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    engine = sa.create_engine(url)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        diff = compare_metadata(context, Base.metadata)
    assert diff == [], f"migration drifts from ORM metadata: {diff}"


def test_ddl_compiles_under_postgresql_dialect() -> None:
    # PostgreSQL compatibility is kept honest at compile level on a
    # SQLite-only CI (knowledge-core: no SQLite-only features); a real
    # server round-trip is explicitly not claimed.
    dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
    for table in Base.metadata.sorted_tables:
        statement = sa.schema.CreateTable(table).compile(dialect=dialect)
        rendered = str(statement)
        assert f"CREATE TABLE {table.name}" in rendered
        if any(column.type.__class__.__name__ == "JSONB" for column in table.columns):
            assert "JSONB" in rendered

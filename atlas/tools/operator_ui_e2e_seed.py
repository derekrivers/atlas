"""Seed the Operator UI end-to-end SQLite store.

The fixture is intentionally committed data. The loader only translates it
through existing repository APIs so the live API sees normal Atlas storage rows.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar, cast
from uuid import UUID

from atlas.core.enums import ActorType, EntityStatus, EvidenceStatus, RiskLevel
from atlas.core.models import (
    ADRStatus,
    ArchitectureDecisionRecord,
    DependencyType,
    Epic,
    EpicStatus,
    Evidence,
    EvidenceType,
    Lesson,
    LessonCategory,
    Product,
    Ticket,
    TicketDependency,
    TicketStatus,
    TicketType,
    VerificationCheck,
    VerificationCheckType,
)
from atlas.storage import (
    ADRRepo,
    Database,
    EpicRepo,
    EvidenceRepo,
    LessonRepo,
    ProductRepo,
    TicketDependencyRepo,
    TicketRepo,
    VerificationCheckRepo,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEED_PATH = (
    REPO_ROOT
    / "apps"
    / "operator-ui"
    / "tests"
    / "e2e"
    / "fixtures"
    / "live-api-seed.json"
)

T = TypeVar("T")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed the Operator UI Playwright live API store."
    )
    parser.add_argument("--db", required=True, help="SQLite database URL to seed.")
    parser.add_argument(
        "--seed",
        default=str(DEFAULT_SEED_PATH),
        help="Path to the committed JSON seed fixture.",
    )
    return parser.parse_args()


def _record(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return cast(Mapping[str, Any], value)


def _records(value: object, label: str) -> Sequence[Mapping[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{label} must be a list of objects")
    return cast(Sequence[Mapping[str, Any]], value)


def _required(record: Mapping[str, Any], key: str, expected_type: type[T]) -> T:
    value = record.get(key)
    if not isinstance(value, expected_type):
        raise ValueError(f"{key} must be {expected_type.__name__}")
    return value


def _optional(record: Mapping[str, Any], key: str, expected_type: type[T]) -> T | None:
    value = record.get(key)
    if value is None:
        return None
    if not isinstance(value, expected_type):
        raise ValueError(f"{key} must be {expected_type.__name__} or null")
    return value


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _uuid(value: str) -> UUID:
    return UUID(value)


def _remove_existing_sqlite(url: str) -> None:
    if not url.startswith("sqlite:///") or ":memory:" in url:
        return
    Path(url.removeprefix("sqlite:///")).unlink(missing_ok=True)


def _load_seed(path: Path) -> Mapping[str, Any]:
    return _record(json.loads(path.read_text(encoding="utf-8")), str(path))


def _ticket_defaults(key: str, timestamp: datetime) -> dict[str, Any]:
    return {
        "title": f"Seeded {key}",
        "objective": f"Exercise live API shape for {key}.",
        "context": "Deterministic operator-ui e2e seed.",
        "ticket_type": TicketType.FEATURE,
        "relevant_docs": ["docs/atlas/operator-ui.md", "docs/atlas/operator-api.md"],
        "acceptance_criteria": [f"{key} has one seeded acceptance criterion."],
        "non_goals": ["Do not author operator view behavior in the e2e harness."],
        "implementation_notes": ["Seeded for ATL-388 live API coverage."],
        "test_requirements": ["Covered by @playwright/test over atlas api serve."],
        "documentation_requirements": ["Document the e2e command."],
        "definition_of_done": ["Live e2e seed assertions pass."],
        "tags": ["operator-ui", "e2e"],
        "component": "operator-ui",
        "external_linear_id": None,
        "external_github_issue_id": None,
        "linear_synced_at": timestamp,
        "last_observed_linear_state_id": None,
        "status_entered_at": timestamp,
        "review_cycle_count": 0,
        "lesson_extraction_attempted_at": None,
        "source_anchor": "docs/atlas/operator-ui.md#testing-contract",
        "created_by_type": ActorType.SYSTEM,
        "created_by_id": "operator-ui-e2e-seed",
        "created_at": timestamp,
        "updated_at": timestamp,
        "completed_at": None,
    }


def _seed_product(
    db: Database,
    product_record: Mapping[str, Any],
    timestamp: datetime,
) -> Product:
    product = Product(
        id=_uuid(_required(product_record, "id", str)),
        key=_required(product_record, "key", str),
        name=_required(product_record, "name", str),
        description=_required(product_record, "description", str),
        vision=_required(product_record, "vision", str),
        status=EntityStatus.ACTIVE,
        goals=["Exercise the live operator API in end-to-end tests."],
        non_goals=["Do not seed writes, authentication, or external integrations."],
        constraints=["Loopback API only."],
        created_by_type=ActorType.SYSTEM,
        created_by_id="operator-ui-e2e-seed",
        created_at=timestamp,
        updated_at=timestamp,
    )
    return ProductRepo(db).add(product)


def _seed_epics(
    db: Database,
    records: Sequence[Mapping[str, Any]],
    product: Product,
    timestamp: datetime,
) -> dict[str, Epic]:
    epics: dict[str, Epic] = {}
    repo = EpicRepo(db)
    for record in records:
        epic = Epic(
            id=_uuid(_required(record, "id", str)),
            product_id=product.id,
            key=_required(record, "key", str),
            title=_required(record, "title", str),
            description="Seeded operator-ui e2e epic.",
            objective="Host the deterministic live API seed tickets.",
            status=EpicStatus(_required(record, "status", str)),
            priority=_required(record, "priority", int),
            risk_level=RiskLevel(_required(record, "risk_level", str)),
            source_anchor="docs/atlas/operator-ui.md#testing-contract",
            created_by_type=ActorType.SYSTEM,
            created_by_id="operator-ui-e2e-seed",
            created_at=timestamp,
            updated_at=timestamp,
            completed_at=None,
        )
        epics[epic.key] = repo.add(epic)
    return epics


def _seed_adrs(
    db: Database,
    records: Sequence[Mapping[str, Any]],
    product: Product,
    timestamp: datetime,
) -> dict[str, ArchitectureDecisionRecord]:
    adrs: dict[str, ArchitectureDecisionRecord] = {}
    repo = ADRRepo(db)
    for record in records:
        number = _required(record, "number", int)
        adr = ArchitectureDecisionRecord(
            id=_uuid(_required(record, "id", str)),
            product_id=product.id,
            number=number,
            title=_required(record, "title", str),
            status=ADRStatus(_required(record, "status", str)),
            context="Seeded proposed ADR for multi-reason readiness coverage.",
            decision="Intentionally not accepted in the e2e seed.",
            rationale="A proposed ADR is a valid graph target and a readiness blocker.",
            consequences=["ATLAS-2 is not ready for more than one reason."],
            alternatives_considered=[],
            created_by_type=ActorType.SYSTEM,
            created_by_id="operator-ui-e2e-seed",
            created_at=timestamp,
            updated_at=timestamp,
        )
        adrs[f"ADR-{number:04d}"] = repo.add(adr)
    return adrs


def _seed_tickets(
    db: Database,
    records: Sequence[Mapping[str, Any]],
    product: Product,
    epics: Mapping[str, Epic],
    timestamp: datetime,
) -> dict[str, Ticket]:
    tickets: dict[str, Ticket] = {}
    repo = TicketRepo(db)
    for record in records:
        key = _required(record, "key", str)
        defaults = _ticket_defaults(key, timestamp)
        status = TicketStatus(_required(record, "status", str))
        epic_key = record.get("epic_key", "ATLAS-E1")
        if epic_key is not None and not isinstance(epic_key, str):
            raise ValueError("epic_key must be str or null")
        epic_id = epics[epic_key].id if epic_key is not None else None
        ticket = Ticket(
            **{
                **defaults,
                "id": _uuid(_required(record, "id", str)),
                "product_id": product.id,
                "epic_id": epic_id,
                "key": key,
                "status": status,
                "priority": _required(record, "priority", int),
                "risk_level": RiskLevel(_required(record, "risk_level", str)),
                "estimated_effort": _optional(record, "estimated_effort", int),
                "relevant_docs": list(
                    cast(
                        Sequence[str],
                        record.get("relevant_docs", defaults["relevant_docs"]),
                    )
                ),
                "acceptance_criteria": record.get(
                    "acceptance_criteria", defaults["acceptance_criteria"]
                ),
                "non_goals": list(
                    cast(Sequence[str], record.get("non_goals", defaults["non_goals"]))
                ),
                "implementation_notes": list(
                    cast(
                        Sequence[str],
                        record.get(
                            "implementation_notes",
                            defaults["implementation_notes"],
                        ),
                    )
                ),
                "test_requirements": list(
                    cast(
                        Sequence[str],
                        record.get("test_requirements", defaults["test_requirements"]),
                    )
                ),
                "documentation_requirements": list(
                    cast(
                        Sequence[str],
                        record.get(
                            "documentation_requirements",
                            defaults["documentation_requirements"],
                        ),
                    )
                ),
                "definition_of_done": list(
                    cast(
                        Sequence[str],
                        record.get(
                            "definition_of_done", defaults["definition_of_done"]
                        ),
                    )
                ),
                "tags": list(cast(Sequence[str], record.get("tags", defaults["tags"]))),
                "component": _optional(record, "component", str)
                if "component" in record
                else defaults["component"],
                "external_linear_id": _optional(record, "external_linear_id", str),
                "external_github_issue_id": _optional(
                    record, "external_github_issue_id", str
                ),
                "source_anchor": record.get("source_anchor", defaults["source_anchor"]),
                "completed_at": timestamp
                if status in {TicketStatus.DONE, TicketStatus.REJECTED}
                else None,
            }
        )
        tickets[ticket.key] = repo.add(ticket)
    return tickets


def _seed_dependencies(
    db: Database,
    records: Sequence[Mapping[str, Any]],
    tickets: Mapping[str, Ticket],
    adrs: Mapping[str, ArchitectureDecisionRecord],
    timestamp: datetime,
) -> None:
    repo = TicketDependencyRepo(db)
    for record in records:
        source = tickets[_required(record, "source_ticket_key", str)]
        target_type = _required(record, "target_entity_type", str)
        explicit_target_id = _optional(record, "target_entity_id", str)
        if explicit_target_id is not None:
            target_id = _uuid(explicit_target_id)
        else:
            target_ref = _required(record, "target_ref", str)
            if target_type == "adr":
                target_id = adrs[target_ref].id
            else:
                target_id = tickets[target_ref].id
        repo.add(
            TicketDependency(
                id=_uuid(_required(record, "id", str)),
                source_ticket_id=source.id,
                target_entity_type=target_type,
                target_entity_id=target_id,
                dependency_type=DependencyType(
                    _required(record, "dependency_type", str)
                ),
                reason=_required(record, "reason", str),
                created_by_type=ActorType.SYSTEM,
                created_by_id="operator-ui-e2e-seed",
                created_at=timestamp,
            )
        )


def _seed_evidence(
    db: Database,
    records: Sequence[Mapping[str, Any]],
    product: Product,
    tickets: Mapping[str, Ticket],
    timestamp: datetime,
) -> None:
    repo = EvidenceRepo(db)
    for record in records:
        created_by_type = ActorType(
            record.get("created_by_type", ActorType.SYSTEM.value)
        )
        created_by_id = record.get(
            "created_by_id",
            "github-actions"
            if created_by_type is ActorType.SYSTEM
            else "operator-ui-e2e-seed",
        )
        created_at_text = _optional(record, "created_at", str)
        created_at = _dt(created_at_text) if created_at_text is not None else timestamp
        repo.add(
            Evidence(
                id=_uuid(_required(record, "id", str)),
                product_id=product.id,
                ticket_id=tickets[_required(record, "ticket_key", str)].id,
                evidence_type=EvidenceType(_required(record, "evidence_type", str)),
                status=EvidenceStatus(_required(record, "status", str)),
                summary=_required(record, "summary", str),
                commit_sha=_optional(record, "commit_sha", str),
                external_run_id=_optional(record, "external_run_id", str),
                job_name=_optional(record, "job_name", str),
                source_event_at=created_at,
                payload_hash=_optional(record, "payload_hash", str),
                source_uri=_optional(record, "source_uri", str),
                raw_payload={"seed": "operator-ui-e2e"},
                created_by_type=created_by_type,
                created_by_id=created_by_id,
                created_at=created_at,
            )
        )


def _seed_verification_checks(
    db: Database,
    records: Sequence[Mapping[str, Any]],
    tickets: Mapping[str, Ticket],
    timestamp: datetime,
) -> None:
    repo = VerificationCheckRepo(db)
    for index, record in enumerate(records):
        status = EvidenceStatus(_required(record, "status", str))
        check_type = VerificationCheckType(_required(record, "check_type", str))
        summary = _optional(record, "summary", str)
        required = _optional(record, "required", bool)
        completed_at = (
            timestamp + timedelta(seconds=index)
            if status
            in {
                EvidenceStatus.FAILED,
                EvidenceStatus.NOT_APPLICABLE,
                EvidenceStatus.PASSED,
            }
            else None
        )
        repo.add(
            VerificationCheck(
                id=_uuid(_required(record, "id", str)),
                ticket_id=tickets[_required(record, "ticket_key", str)].id,
                check_type=check_type,
                status=status,
                summary=summary or f"{check_type.value}: seeded {status.value}",
                required=True if required is None else required,
                created_at=timestamp + timedelta(seconds=index),
                completed_at=completed_at,
            )
        )


def _seed_lessons(
    db: Database,
    records: Sequence[Mapping[str, Any]],
    product: Product,
    timestamp: datetime,
) -> None:
    repo = LessonRepo(db)
    for record in records:
        repo.add(
            Lesson(
                id=_uuid(_required(record, "id", str)),
                product_id=product.id,
                status=EntityStatus(_required(record, "status", str)),
                category=LessonCategory(_required(record, "category", str)),
                title=_required(record, "title", str),
                problem=_required(record, "problem", str),
                solution=_required(record, "solution", str),
                outcome=_required(record, "outcome", str),
                confidence=_optional(record, "confidence", float),
                source_ticket_id=_uuid(_required(record, "source_ticket_id", str)),
                related_ticket_ids=[
                    _uuid(value)
                    for value in cast(
                        Sequence[str],
                        record.get("related_ticket_ids", []),
                    )
                ],
                related_adr_ids=[
                    _uuid(value)
                    for value in cast(
                        Sequence[str],
                        record.get("related_adr_ids", []),
                    )
                ],
                tags=list(cast(Sequence[str], record.get("tags", []))),
                created_by_type=ActorType.AGENT,
                created_by_id="operator-ui-e2e-seed",
                created_at=timestamp,
                updated_at=timestamp,
            )
        )


def seed_store(db_url: str, seed_path: Path = DEFAULT_SEED_PATH) -> Database:
    """Create a fresh SQLite store and load the committed e2e fixture."""
    seed = _load_seed(seed_path)
    timestamp = _dt(_required(seed, "timestamp", str))

    _remove_existing_sqlite(db_url)
    db = Database(db_url)
    db.create_all()

    product = _seed_product(db, _record(seed.get("product"), "product"), timestamp)
    epics = _seed_epics(db, _records(seed.get("epics"), "epics"), product, timestamp)
    adrs = _seed_adrs(db, _records(seed.get("adrs"), "adrs"), product, timestamp)
    tickets = _seed_tickets(
        db,
        _records(seed.get("tickets"), "tickets"),
        product,
        epics,
        timestamp,
    )
    _seed_dependencies(
        db,
        _records(seed.get("dependencies"), "dependencies"),
        tickets,
        adrs,
        timestamp,
    )
    _seed_evidence(
        db,
        _records(seed.get("evidence"), "evidence"),
        product,
        tickets,
        timestamp,
    )
    _seed_verification_checks(
        db,
        _records(seed.get("verification_checks", []), "verification_checks"),
        tickets,
        timestamp,
    )
    _seed_lessons(
        db,
        _records(seed.get("lessons"), "lessons"),
        product,
        timestamp,
    )
    return db


def main() -> None:
    args = _parse_args()
    db = seed_store(args.db, Path(args.seed))
    db.engine.dispose()
    print(f"Seeded {args.db} from {args.seed}.")


if __name__ == "__main__":
    main()

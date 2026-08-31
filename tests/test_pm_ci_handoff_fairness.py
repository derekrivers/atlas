"""ATLAS-095M durable fair CI-handoff scheduling and reconstruction tests."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from pm_temporal_harness import ProcessGeneration, TemporalHarness
from test_models_validation import NOW
from test_pm_sync import (
    CI_PENDING_STATE,
    PROJECT_ID,
    RecordingClient,
    seed_ticket,
)

from atlas.core.models import (
    CIHandoffClassification,
    CIHandoffDecision,
    CIHandoffReason,
    TicketStatus,
)
from atlas.core.models.pm_recovery import PmBlockerCode
from atlas.linear.client import LinearIssue
from atlas.pm.ci_handoff import CIHandoffResult
from atlas.pm.ci_handoff_adapter import (
    CIHandoffAdapterReason,
    CIHandoffAdapterResult,
)
from atlas.pm.ci_handoff_fairness import (
    FairCIHandoffSelection,
    record_fair_ci_handoff_evaluation,
    select_fair_ci_handoff_candidate,
)
from atlas.storage import Database, PmRecoveryRepo, TicketRepo

PRODUCT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


@dataclass(frozen=True)
class FairnessGeneration:
    database: Database
    issues: tuple[LinearIssue, ...]


def _seed(path: Path, keys: tuple[str, ...]) -> tuple[LinearIssue, ...]:
    database = Database(f"sqlite:///{path}")
    database.create_all()
    client = RecordingClient()
    for key in keys:
        seed_ticket(
            database,
            client,
            key=key,
            product_id=PRODUCT_ID,
            status=TicketStatus.CI_PENDING,
            issue_state=CI_PENDING_STATE,
            linear_synced_at=NOW,
            status_entered_at=NOW,
        )
    issues = tuple(client.fetch_project_issues(PROJECT_ID))
    database.engine.dispose()
    return issues


def _register(
    harness: TemporalHarness, authoritative_issues: tuple[LinearIssue, ...]
) -> None:
    def factory(generation: ProcessGeneration) -> FairnessGeneration:
        return FairnessGeneration(
            Database(f"sqlite:///{generation.harness.db_path}"),
            tuple(replace(issue) for issue in authoritative_issues),
        )

    def dispose(resource: object) -> None:
        cast(FairnessGeneration, resource).database.engine.dispose()

    harness.register_generation_resource("fairness", factory, disposer=dispose)


def _database(generation: ProcessGeneration) -> Database:
    return cast(FairnessGeneration, generation.resource("fairness")).database


def _held() -> CIHandoffAdapterResult:
    return CIHandoffAdapterResult(
        reason=CIHandoffAdapterReason.PUBLICATION_UNAVAILABLE,
        candidate_count=3,
        ticket_key="unused-by-recorder",
    )


def _select(
    generation: ProcessGeneration,
    *,
    now_offset: int,
) -> FairCIHandoffSelection:
    resource = cast(FairnessGeneration, generation.resource("fairness"))
    database = resource.database
    return select_fair_ci_handoff_candidate(
        db=database,
        tickets=TicketRepo(database),
        initial_issues=list(resource.issues),
        now=NOW + timedelta(seconds=now_offset),
    )


def test_three_candidate_rotation_survives_complete_process_reconstruction(
    tmp_path: Path,
) -> None:
    path = tmp_path / "rotation.db"
    issues = _seed(path, ("ATLAS-290", "ATLAS-291", "ATLAS-292"))

    with TemporalHarness(db_path=path, initial_time=NOW) as harness:
        _register(harness, issues)
        selected: list[str] = []
        for offset in range(4):
            with harness.new_generation() as generation:
                selection = _select(generation, now_offset=offset)
                assert selection.candidate is not None
                selected.append(selection.candidate.key)
                record_fair_ci_handoff_evaluation(
                    db=_database(generation),
                    selection=selection,
                    result=_held(),
                    now=NOW + timedelta(seconds=offset),
                )

    assert selected == ["ATLAS-290", "ATLAS-291", "ATLAS-292", "ATLAS-290"]


def test_crash_before_fairness_persistence_reselects_then_eventually_advances(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pre-commit-crash.db"
    issues = _seed(path, ("ATLAS-290", "ATLAS-291"))

    with TemporalHarness(db_path=path, initial_time=NOW) as harness:
        _register(harness, issues)
        with harness.new_generation() as generation:
            first = _select(generation, now_offset=0)
            assert first.candidate is not None and first.candidate.key == "ATLAS-290"
            # Process ends before the read-only result is durably observed.
        with harness.new_generation() as generation:
            replay = _select(generation, now_offset=1)
            assert replay.candidate is not None and replay.candidate.key == "ATLAS-290"
            record_fair_ci_handoff_evaluation(
                db=_database(generation),
                selection=replay,
                result=_held(),
                now=NOW + timedelta(seconds=1),
            )
        with harness.new_generation() as generation:
            later = _select(generation, now_offset=2)
            assert later.candidate is not None and later.candidate.key == "ATLAS-291"


def test_new_arrival_joins_product_sequence_tail(tmp_path: Path) -> None:
    path = tmp_path / "new-arrival.db"
    issues = list(_seed(path, ("ATLAS-290", "ATLAS-291")))
    database = Database(f"sqlite:///{path}")
    client = RecordingClient()
    first = select_fair_ci_handoff_candidate(
        db=database,
        tickets=TicketRepo(database),
        initial_issues=issues,  # type: ignore[arg-type]
        now=NOW,
    )
    assert first.candidate is not None and first.candidate.key == "ATLAS-290"
    record_fair_ci_handoff_evaluation(
        db=database, selection=first, result=_held(), now=NOW
    )
    newcomer = seed_ticket(
        database,
        client,
        key="ATLAS-292",
        product_id=PRODUCT_ID,
        status=TicketStatus.CI_PENDING,
        issue_state=CI_PENDING_STATE,
        linear_synced_at=NOW,
        status_entered_at=NOW,
    )
    issue = client.fetch_issue(newcomer.external_linear_id or "")
    assert issue is not None
    issues.append(issue)

    next_selection = select_fair_ci_handoff_candidate(
        db=database,
        tickets=TicketRepo(database),
        initial_issues=issues,  # type: ignore[arg-type]
        now=NOW + timedelta(seconds=1),
    )
    assert next_selection.candidate is not None
    assert next_selection.candidate.key == "ATLAS-291"
    episodes = PmRecoveryRepo(database).list_active_episodes_ordered(PRODUCT_ID)
    cursors = {
        episode.candidate_ticket_key: episode.fairness_cursor for episode in episodes
    }
    assert cursors["ATLAS-291"] < cursors["ATLAS-290"] < cursors["ATLAS-292"]


def test_selected_candidate_evaluation_clears_only_its_stale_starvation_membership(
    tmp_path: Path,
) -> None:
    path = tmp_path / "starvation-relief.db"
    issues = _seed(path, ("ATLAS-290", "ATLAS-291", "ATLAS-292"))
    database = Database(f"sqlite:///{path}")
    first = select_fair_ci_handoff_candidate(
        db=database,
        tickets=TicketRepo(database),
        initial_issues=list(issues),  # type: ignore[arg-type]
        now=NOW,
    )
    record_fair_ci_handoff_evaluation(
        db=database, selection=first, result=_held(), now=NOW
    )
    [blocker] = PmRecoveryRepo(database).list_blockers(
        product_id=PRODUCT_ID, active_only=True
    )
    assert [item.ticket_key for item in blocker.starved_candidates] == [
        "ATLAS-291",
        "ATLAS-292",
    ]

    second = select_fair_ci_handoff_candidate(
        db=database,
        tickets=TicketRepo(database),
        initial_issues=list(issues),  # type: ignore[arg-type]
        now=NOW + timedelta(seconds=1),
    )
    record_fair_ci_handoff_evaluation(
        db=database,
        selection=second,
        result=_held(),
        now=NOW + timedelta(seconds=1),
    )
    first_blocker = PmRecoveryRepo(database).get_blocker(blocker.id)
    assert first_blocker is not None
    assert [item.ticket_key for item in first_blocker.starved_candidates] == [
        "ATLAS-292"
    ]


def test_publication_replacement_retires_old_episode_and_blocker(
    tmp_path: Path,
) -> None:
    path = tmp_path / "publication-replacement.db"
    database = Database(f"sqlite:///{path}")
    database.create_all()
    client = RecordingClient()
    ticket = seed_ticket(
        database,
        client,
        key="ATLAS-290",
        product_id=PRODUCT_ID,
        status=TicketStatus.CI_PENDING,
        issue_state=CI_PENDING_STATE,
        linear_synced_at=NOW,
        status_entered_at=NOW,
    )
    assert ticket.external_linear_id is not None
    client.seed_github_publication(
        ticket.external_linear_id,
        owner="derekrivers",
        repo="atlas",
        pr_number=390,
        attachment_id="publication-a",
    )
    first = select_fair_ci_handoff_candidate(
        db=database,
        tickets=TicketRepo(database),
        initial_issues=client.fetch_project_issues(PROJECT_ID),
        now=NOW,
    )
    record_fair_ci_handoff_evaluation(
        db=database, selection=first, result=_held(), now=NOW
    )
    assert first.episode is not None
    old_episode_id = first.episode.id
    [old_blocker] = PmRecoveryRepo(database).list_blockers(
        product_id=PRODUCT_ID, active_only=True
    )

    client.seed_github_publication(
        ticket.external_linear_id,
        owner="derekrivers",
        repo="atlas",
        pr_number=391,
        attachment_id="publication-b",
    )
    replacement = select_fair_ci_handoff_candidate(
        db=database,
        tickets=TicketRepo(database),
        initial_issues=client.fetch_project_issues(PROJECT_ID),
        now=NOW + timedelta(seconds=1),
    )
    assert replacement.episode is not None
    assert replacement.episode.id != old_episode_id
    assert replacement.episode.replaces_episode_id == old_episode_id
    retained = PmRecoveryRepo(database).get_blocker(old_blocker.id)
    assert retained is not None and retained.superseded_at is not None
    assert retained.supersession_kind is not None


def test_lifecycle_exit_closes_episode_and_reentry_creates_a_new_one(
    tmp_path: Path,
) -> None:
    path = tmp_path / "lifecycle-reentry.db"
    issues = _seed(path, ("ATLAS-290",))
    database = Database(f"sqlite:///{path}")
    tickets = TicketRepo(database)
    initial = select_fair_ci_handoff_candidate(
        db=database,
        tickets=tickets,
        initial_issues=list(issues),  # type: ignore[arg-type]
        now=NOW,
    )
    assert initial.candidate is not None and initial.episode is not None
    tickets.apply_linear_status(
        initial.candidate.key,
        TicketStatus.REVIEW_REQUIRED,
        now=NOW + timedelta(seconds=1),
        created_by_id="test-authority",
    )

    empty = select_fair_ci_handoff_candidate(
        db=database,
        tickets=tickets,
        initial_issues=list(issues),  # type: ignore[arg-type]
        now=NOW + timedelta(seconds=1),
    )
    assert empty.candidate is None
    closed = PmRecoveryRepo(database).get_episode(initial.episode.id)
    assert closed is not None and closed.closed_at is not None

    tickets.apply_linear_status(
        initial.candidate.key,
        TicketStatus.CI_PENDING,
        now=NOW + timedelta(seconds=2),
        created_by_id="test-authority",
    )
    reentered = select_fair_ci_handoff_candidate(
        db=database,
        tickets=tickets,
        initial_issues=list(issues),  # type: ignore[arg-type]
        now=NOW + timedelta(seconds=2),
    )
    assert reentered.episode is not None
    assert reentered.episode.id != initial.episode.id
    assert reentered.episode.replaces_episode_id is None


@pytest.mark.parametrize(
    "poison",
    [
        CIHandoffAdapterResult(
            reason=CIHandoffAdapterReason.PUBLICATION_AMBIGUOUS,
            candidate_count=2,
            ticket_key="ATLAS-290",
        ),
        CIHandoffAdapterResult(
            reason=CIHandoffAdapterReason.RECONCILED,
            candidate_count=2,
            ticket_key="ATLAS-290",
            reconciliation=CIHandoffResult(
                classification=CIHandoffClassification.PENDING,
                decision=CIHandoffDecision.HOLD,
                reason=CIHandoffReason.REQUIRED_CHECKS_PENDING,
                ticket_key="ATLAS-290",
            ),
        ),
        CIHandoffAdapterResult(
            reason=CIHandoffAdapterReason.RECONCILED,
            candidate_count=2,
            ticket_key="ATLAS-290",
            reconciliation=CIHandoffResult(
                classification=CIHandoffClassification.MALFORMED,
                decision=CIHandoffDecision.HOLD,
                reason=CIHandoffReason.MALFORMED_EVIDENCE,
                ticket_key="ATLAS-290",
            ),
        ),
    ],
    ids=["merged-publication-inactive", "checks-pending", "malformed-evidence"],
)
def test_bounded_poison_outcomes_cannot_starve_the_next_candidate(
    tmp_path: Path,
    poison: CIHandoffAdapterResult,
) -> None:
    path = tmp_path / "poison.db"
    issues = _seed(path, ("ATLAS-290", "ATLAS-291"))
    database = Database(f"sqlite:///{path}")
    first = select_fair_ci_handoff_candidate(
        db=database,
        tickets=TicketRepo(database),
        initial_issues=list(issues),  # type: ignore[arg-type]
        now=NOW,
    )
    assert first.candidate is not None and first.candidate.key == "ATLAS-290"
    record_fair_ci_handoff_evaluation(
        db=database,
        selection=first,
        result=poison,
        now=NOW,
    )

    second = select_fair_ci_handoff_candidate(
        db=database,
        tickets=TicketRepo(database),
        initial_issues=list(issues),  # type: ignore[arg-type]
        now=NOW + timedelta(seconds=1),
    )
    assert second.candidate is not None and second.candidate.key == "ATLAS-291"


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (
            CIHandoffAdapterReason.PUBLICATION_UNAVAILABLE,
            PmBlockerCode.PUBLICATION_NOT_YET_COMPLETE,
        ),
        (
            CIHandoffAdapterReason.PUBLICATION_AMBIGUOUS,
            PmBlockerCode.PUBLICATION_AMBIGUOUS,
        ),
        (
            CIHandoffAdapterReason.EVIDENCE_INGESTION_FAILED,
            PmBlockerCode.PROVIDER_UNAVAILABLE,
        ),
        (
            CIHandoffAdapterReason.IDENTITY_UNAVAILABLE,
            PmBlockerCode.CI_EVIDENCE_AMBIGUOUS,
        ),
    ],
)
def test_adapter_holds_persist_explicit_bounded_codes(
    tmp_path: Path,
    reason: CIHandoffAdapterReason,
    expected: PmBlockerCode,
) -> None:
    path = tmp_path / f"blocker-{reason.value}.db"
    issues = _seed(path, ("ATLAS-290",))
    database = Database(f"sqlite:///{path}")
    selection = select_fair_ci_handoff_candidate(
        db=database,
        tickets=TicketRepo(database),
        initial_issues=list(issues),  # type: ignore[arg-type]
        now=NOW,
    )
    record_fair_ci_handoff_evaluation(
        db=database,
        selection=selection,
        result=CIHandoffAdapterResult(
            reason=reason, candidate_count=1, ticket_key="ATLAS-290"
        ),
        now=NOW,
    )
    [blocker] = PmRecoveryRepo(database).list_blockers(
        product_id=PRODUCT_ID, active_only=True
    )
    assert blocker.code is expected

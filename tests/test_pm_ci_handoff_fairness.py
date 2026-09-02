"""ATLAS-095M durable fair CI-handoff scheduling and reconstruction tests."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from pm_temporal_harness import ProcessGeneration, TemporalHarness
from test_models_validation import NOW
from test_pm_sync import (
    CI_PENDING_STATE,
    PROJECT_ID,
    RecordingClient,
    seed_ticket,
)

from atlas.core.enums import ActorType
from atlas.core.models import (
    CIHandoffClassification,
    CIHandoffDecision,
    CIHandoffReason,
    TicketStatus,
    TicketStatusTransition,
)
from atlas.core.models.pm_recovery import MAX_PM_STARVED_CANDIDATES, PmBlockerCode
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
from atlas.storage import (
    Database,
    PmRecoveryRepo,
    TicketRepo,
    TicketStatusTransitionRepo,
)

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


def test_new_arrival_joins_product_sequence_tail_across_reconstruction(
    tmp_path: Path,
) -> None:
    path = tmp_path / "new-arrival.db"
    issues = list(_seed(path, ("ATLAS-291", "ATLAS-292")))
    database = Database(f"sqlite:///{path}")
    client = RecordingClient()
    first = select_fair_ci_handoff_candidate(
        db=database,
        tickets=TicketRepo(database),
        initial_issues=issues,
        now=NOW,
    )
    assert first.candidate is not None and first.candidate.key == "ATLAS-291"
    record_fair_ci_handoff_evaluation(
        db=database, selection=first, result=_held(), now=NOW
    )
    newcomer = seed_ticket(
        database,
        client,
        key="ATLAS-290",
        product_id=PRODUCT_ID,
        status=TicketStatus.CI_PENDING,
        issue_state=CI_PENDING_STATE,
        linear_synced_at=NOW,
        status_entered_at=NOW,
    )
    issue = client.fetch_issue(newcomer.external_linear_id or "")
    assert issue is not None
    issues.append(issue)
    rebuilt_client = RecordingClient()
    rebuilt_client._issues = {issue.id: replace(issue) for issue in issues}
    database.engine.dispose()
    database = Database(f"sqlite:///{path}")

    next_selection = select_fair_ci_handoff_candidate(
        db=database,
        tickets=TicketRepo(database),
        initial_issues=rebuilt_client.fetch_project_issues(PROJECT_ID),
        now=NOW + timedelta(seconds=1),
    )
    assert next_selection.candidate is not None
    assert next_selection.candidate.key == "ATLAS-292"
    episodes = PmRecoveryRepo(database).list_active_episodes_ordered(PRODUCT_ID)
    cursors = {
        episode.candidate_ticket_key: episode.fairness_cursor for episode in episodes
    }
    assert cursors["ATLAS-292"] < cursors["ATLAS-291"] < cursors["ATLAS-290"]


def test_stale_ci_pending_transition_does_not_replace_current_episode(
    tmp_path: Path,
) -> None:
    path = tmp_path / "stale-transition.db"
    database = Database(f"sqlite:///{path}")
    database.create_all()
    client = RecordingClient()
    ticket = seed_ticket(
        database,
        client,
        key="ATLAS-290",
        product_id=PRODUCT_ID,
        status=TicketStatus.PR_OPEN,
        issue_state=CI_PENDING_STATE,
        linear_synced_at=NOW,
        status_entered_at=NOW - timedelta(seconds=1),
    )
    TicketRepo(database).apply_linear_status(
        ticket.key,
        TicketStatus.CI_PENDING,
        now=NOW,
        created_by_id="test:current-ci-entry",
    )
    issues = client.fetch_project_issues(PROJECT_ID)
    selected = select_fair_ci_handoff_candidate(
        db=database,
        tickets=TicketRepo(database),
        initial_issues=issues,
        now=NOW,
    )
    assert selected.episode is not None
    recorded = record_fair_ci_handoff_evaluation(
        db=database,
        selection=selected,
        result=_held(),
        now=NOW + timedelta(seconds=1),
    )
    high_water = PmRecoveryRepo(database).sequence_high_water(PRODUCT_ID)
    TicketStatusTransitionRepo(database).record(
        TicketStatusTransition(
            id=uuid4(),
            ticket_id=ticket.id,
            from_status=TicketStatus.PR_OPEN.value,
            to_status=TicketStatus.CI_PENDING.value,
            occurred_at=NOW - timedelta(minutes=1),
            created_by_type=ActorType.SYSTEM,
            created_by_id="test:stale-ci-entry",
        )
    )
    database.engine.dispose()

    rebuilt = Database(f"sqlite:///{path}")
    replay = select_fair_ci_handoff_candidate(
        db=rebuilt,
        tickets=TicketRepo(rebuilt),
        initial_issues=issues,
        now=NOW + timedelta(seconds=2),
    )
    assert replay.episode is not None
    assert replay.episode.id == recorded.id
    assert replay.episode.fairness_cursor == recorded.fairness_cursor
    assert PmRecoveryRepo(rebuilt).sequence_high_water(PRODUCT_ID) == high_water


def test_selected_candidate_evaluation_clears_only_its_stale_starvation_membership(
    tmp_path: Path,
) -> None:
    path = tmp_path / "starvation-relief.db"
    issues = _seed(path, ("ATLAS-290", "ATLAS-291", "ATLAS-292"))
    database = Database(f"sqlite:///{path}")
    first = select_fair_ci_handoff_candidate(
        db=database,
        tickets=TicketRepo(database),
        initial_issues=list(issues),
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
        initial_issues=list(issues),
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
    assert first_blocker.capacity_impact

    third = select_fair_ci_handoff_candidate(
        db=database,
        tickets=TicketRepo(database),
        initial_issues=list(issues),
        now=NOW + timedelta(seconds=2),
    )
    record_fair_ci_handoff_evaluation(
        db=database,
        selection=third,
        result=_held(),
        now=NOW + timedelta(seconds=2),
    )
    fully_relieved = PmRecoveryRepo(database).get_blocker(blocker.id)
    assert fully_relieved is not None
    assert fully_relieved.starved_candidates == ()
    assert not fully_relieved.capacity_impact


def test_starvation_projection_is_bounded_before_storage_validation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bounded-starvation.db"
    keys = tuple(f"ATLAS-{number}" for number in range(1000, 1130))
    issues = _seed(path, keys)
    database = Database(f"sqlite:///{path}")
    selection = select_fair_ci_handoff_candidate(
        db=database,
        tickets=TicketRepo(database),
        initial_issues=list(issues),
        now=NOW,
    )
    assert selection.candidate is not None

    record_fair_ci_handoff_evaluation(
        db=database,
        selection=selection,
        result=_held(),
        now=NOW,
    )

    [blocker] = PmRecoveryRepo(database).list_blockers(
        product_id=PRODUCT_ID, active_only=True
    )
    assert len(blocker.starved_candidates) == MAX_PM_STARVED_CANDIDATES
    assert blocker.starved_candidates_truncated
    assert blocker.capacity_impact
    expected = [ticket.key for ticket in selection.candidates[1:129]]
    assert [item.ticket_key for item in blocker.starved_candidates] == expected
    replay = select_fair_ci_handoff_candidate(
        db=database,
        tickets=TicketRepo(database),
        initial_issues=list(issues),
        now=NOW + timedelta(seconds=1),
    )
    assert replay.candidate is not None
    assert replay.candidate.key == "ATLAS-1001"


def test_cross_product_hold_advances_without_cross_product_starvation_members(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cross-product.db"
    database = Database(f"sqlite:///{path}")
    database.create_all()
    client = RecordingClient()
    second_product_id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    first_ticket = seed_ticket(
        database,
        client,
        key="ATLAS-290",
        product_id=PRODUCT_ID,
        status=TicketStatus.CI_PENDING,
        issue_state=CI_PENDING_STATE,
        linear_synced_at=NOW,
        status_entered_at=NOW,
    )
    second_ticket = seed_ticket(
        database,
        client,
        key="OTHER-290",
        product_id=second_product_id,
        status=TicketStatus.CI_PENDING,
        issue_state=CI_PENDING_STATE,
        linear_synced_at=NOW,
        status_entered_at=NOW,
    )
    issues = client.fetch_project_issues(PROJECT_ID)
    first = select_fair_ci_handoff_candidate(
        db=database,
        tickets=TicketRepo(database),
        initial_issues=issues,
        now=NOW,
    )
    assert first.candidate == first_ticket
    record_fair_ci_handoff_evaluation(
        db=database, selection=first, result=_held(), now=NOW
    )

    [blocker] = PmRecoveryRepo(database).list_blockers(
        product_id=PRODUCT_ID, active_only=True
    )
    assert blocker.starved_candidates == ()
    assert not blocker.capacity_impact
    second = select_fair_ci_handoff_candidate(
        db=database,
        tickets=TicketRepo(database),
        initial_issues=issues,
        now=NOW + timedelta(seconds=1),
    )
    assert second.candidate == second_ticket


def test_new_product_cannot_cut_ahead_of_older_high_cursor_work(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cross-product-new-arrival.db"
    database = Database(f"sqlite:///{path}")
    database.create_all()
    client = RecordingClient()
    older = seed_ticket(
        database,
        client,
        key="ATLAS-290",
        product_id=PRODUCT_ID,
        status=TicketStatus.CI_PENDING,
        issue_state=CI_PENDING_STATE,
        linear_synced_at=NOW,
        status_entered_at=NOW,
    )
    issues = client.fetch_project_issues(PROJECT_ID)
    for offset in range(5):
        selection = select_fair_ci_handoff_candidate(
            db=database,
            tickets=TicketRepo(database),
            initial_issues=issues,
            now=NOW + timedelta(seconds=offset),
        )
        assert selection.candidate == older
        record_fair_ci_handoff_evaluation(
            db=database,
            selection=selection,
            result=_held(),
            now=NOW + timedelta(seconds=offset),
        )

    newcomer = seed_ticket(
        database,
        client,
        key="OTHER-290",
        product_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        status=TicketStatus.CI_PENDING,
        issue_state=CI_PENDING_STATE,
        linear_synced_at=NOW + timedelta(seconds=5),
        status_entered_at=NOW + timedelta(seconds=5),
    )
    issue = client.fetch_issue(newcomer.external_linear_id or "")
    assert issue is not None
    issues.append(issue)

    selected = select_fair_ci_handoff_candidate(
        db=database,
        tickets=TicketRepo(database),
        initial_issues=issues,
        now=NOW + timedelta(seconds=5),
    )

    assert selected.candidate == older
    assert selected.episode is not None
    assert selected.episode.fairness_cursor > 1


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
    database_url = str(database.engine.url)
    rebuilt_client = RecordingClient()
    rebuilt_client._issues = {
        issue.id: replace(issue) for issue in client.fetch_project_issues(PROJECT_ID)
    }
    database.engine.dispose()
    rebuilt = Database(database_url)

    rebuilt_client.seed_github_publication(
        ticket.external_linear_id,
        owner="derekrivers",
        repo="atlas",
        pr_number=391,
        attachment_id="publication-b",
    )
    replacement = select_fair_ci_handoff_candidate(
        db=rebuilt,
        tickets=TicketRepo(rebuilt),
        initial_issues=rebuilt_client.fetch_project_issues(PROJECT_ID),
        now=NOW + timedelta(seconds=1),
    )
    assert replacement.episode is not None
    assert replacement.episode.id != old_episode_id
    assert replacement.episode.replaces_episode_id == old_episode_id
    retained = PmRecoveryRepo(rebuilt).get_blocker(old_blocker.id)
    assert retained is not None and retained.superseded_at is not None
    assert retained.supersession_kind is not None
    assert replacement.episode.episode_created_sequence > (
        first.episode.episode_created_sequence
    )
    rebuilt.engine.dispose()


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
        initial_issues=list(issues),
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
        initial_issues=list(issues),
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
        initial_issues=list(issues),
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
        initial_issues=list(issues),
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
        initial_issues=list(issues),
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
        initial_issues=list(issues),
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


def test_changed_blocker_cause_atomically_supersedes_obsolete_diagnosis(
    tmp_path: Path,
) -> None:
    path = tmp_path / "changed-cause.db"
    issues = _seed(path, ("ATLAS-290",))
    database = Database(f"sqlite:///{path}")
    first = select_fair_ci_handoff_candidate(
        db=database,
        tickets=TicketRepo(database),
        initial_issues=list(issues),
        now=NOW,
    )
    record_fair_ci_handoff_evaluation(
        db=database,
        selection=first,
        result=CIHandoffAdapterResult(
            reason=CIHandoffAdapterReason.PUBLICATION_AMBIGUOUS,
            candidate_count=1,
            ticket_key="ATLAS-290",
        ),
        now=NOW,
    )
    [obsolete] = PmRecoveryRepo(database).list_blockers(
        product_id=PRODUCT_ID, active_only=True
    )

    second = select_fair_ci_handoff_candidate(
        db=database,
        tickets=TicketRepo(database),
        initial_issues=list(issues),
        now=NOW + timedelta(seconds=1),
    )
    record_fair_ci_handoff_evaluation(
        db=database,
        selection=second,
        result=CIHandoffAdapterResult(
            reason=CIHandoffAdapterReason.RECONCILED,
            candidate_count=1,
            ticket_key="ATLAS-290",
            reconciliation=CIHandoffResult(
                classification=CIHandoffClassification.PENDING,
                decision=CIHandoffDecision.HOLD,
                reason=CIHandoffReason.REQUIRED_CHECKS_PENDING,
                ticket_key="ATLAS-290",
            ),
        ),
        now=NOW + timedelta(seconds=1),
    )

    retained = PmRecoveryRepo(database).get_blocker(obsolete.id)
    assert retained is not None
    assert retained.superseded_at == NOW + timedelta(seconds=1)
    [active] = PmRecoveryRepo(database).list_blockers(
        product_id=PRODUCT_ID, active_only=True
    )
    assert active.code is PmBlockerCode.CI_EVIDENCE_NOT_YET_COMPLETE


def test_changed_blocker_cause_failure_rolls_back_full_evaluation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "changed-cause-rollback.db"
    issues = _seed(path, ("ATLAS-290", "ATLAS-291"))
    database = Database(f"sqlite:///{path}")
    first = select_fair_ci_handoff_candidate(
        db=database,
        tickets=TicketRepo(database),
        initial_issues=list(issues),
        now=NOW,
    )
    record_fair_ci_handoff_evaluation(
        db=database,
        selection=first,
        result=CIHandoffAdapterResult(
            reason=CIHandoffAdapterReason.PUBLICATION_AMBIGUOUS,
            candidate_count=1,
            ticket_key="ATLAS-290",
        ),
        now=NOW,
    )
    [obsolete] = PmRecoveryRepo(database).list_blockers(
        product_id=PRODUCT_ID, active_only=True
    )
    assert first.episode is not None
    updated_episode = PmRecoveryRepo(database).get_episode(first.episode.id)
    assert updated_episode is not None
    second = FairCIHandoffSelection(
        candidates=first.candidates,
        candidate=first.candidate,
        episode=updated_episode,
    )
    assert second.episode is not None
    old_cursor = second.episode.fairness_cursor
    old_high_water = PmRecoveryRepo(database).sequence_high_water(PRODUCT_ID)

    def fail_observation(*args: object, **kwargs: object) -> object:
        raise RuntimeError("seeded replacement blocker insert failure")

    with monkeypatch.context() as patcher:
        patcher.setattr(PmRecoveryRepo, "_observe_blocker", fail_observation)
        with pytest.raises(RuntimeError, match="seeded replacement"):
            record_fair_ci_handoff_evaluation(
                db=database,
                selection=second,
                result=CIHandoffAdapterResult(
                    reason=CIHandoffAdapterReason.RECONCILED,
                    candidate_count=1,
                    ticket_key="ATLAS-290",
                    reconciliation=CIHandoffResult(
                        classification=CIHandoffClassification.PENDING,
                        decision=CIHandoffDecision.HOLD,
                        reason=CIHandoffReason.REQUIRED_CHECKS_PENDING,
                        ticket_key="ATLAS-290",
                    ),
                ),
                now=NOW + timedelta(seconds=1),
            )

    retained_episode = PmRecoveryRepo(database).get_episode(second.episode.id)
    assert retained_episode is not None
    assert retained_episode.fairness_cursor == old_cursor
    assert PmRecoveryRepo(database).sequence_high_water(PRODUCT_ID) == old_high_water
    retained = PmRecoveryRepo(database).get_blocker(obsolete.id)
    assert retained is not None and retained.superseded_at is None
    assert retained.active_fingerprint == retained.blocker_fingerprint
    assert [item.ticket_key for item in retained.starved_candidates] == ["ATLAS-291"]

    record_fair_ci_handoff_evaluation(
        db=database,
        selection=second,
        result=CIHandoffAdapterResult(
            reason=CIHandoffAdapterReason.RECONCILED,
            candidate_count=1,
            ticket_key="ATLAS-290",
            reconciliation=CIHandoffResult(
                classification=CIHandoffClassification.PENDING,
                decision=CIHandoffDecision.HOLD,
                reason=CIHandoffReason.REQUIRED_CHECKS_PENDING,
                ticket_key="ATLAS-290",
            ),
        ),
        now=NOW + timedelta(seconds=1),
    )
    [active] = PmRecoveryRepo(database).list_blockers(
        product_id=PRODUCT_ID, active_only=True
    )
    assert active.code is PmBlockerCode.CI_EVIDENCE_NOT_YET_COMPLETE

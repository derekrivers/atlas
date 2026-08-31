"""Durable PM recovery, fairness, blocker, and reconstruction contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from pm_temporal_harness import (
    ProcessGeneration,
    SimulatedProcessDeath,
    TemporalHarness,
)

from atlas.core.enums import ActorType, EntityStatus, RiskLevel
from atlas.core.models import Product, Ticket, TicketStatus, TicketType
from atlas.core.models.pm_recovery import (
    PmBlockerAuthorityKind,
    PmBlockerKind,
    PmBlockerObservationIntent,
    PmBlockerSupersessionKind,
    PmRecoveryEpisodeClosureKind,
    PmRecoveryEpisodeIdentity,
    PmStarvedCandidateRef,
)
from atlas.pm.health import (
    PmHealthAssessment,
    PmHealthInputs,
    PmHealthPolicy,
    PmHealthReasonCode,
    PmHealthStatus,
    assess_pm_health,
)
from atlas.pm.recovery_projection import project_durable_blocker
from atlas.storage import (
    Database,
    PmRecoveryRepo,
    PmRecoveryStorageCode,
    PmRecoveryStorageError,
    ProductRepo,
    TicketRepo,
)
from atlas.storage.tables import (
    PmBlockerStarvedCandidateRow,
    PmRecoveryEpisodeRow,
)

NOW = datetime(2026, 8, 31, 12, tzinfo=UTC)
PRODUCT_ID = UUID("11111111-1111-4111-8111-111111111111")
SECOND_PRODUCT_ID = UUID("22222222-2222-4222-8222-222222222222")


def _product(product_id: UUID, key: str) -> Product:
    return Product(
        id=product_id,
        key=key,
        name=key,
        description="Durable PM recovery test product",
        vision="Recover across complete process reconstruction",
        status=EntityStatus.ACTIVE,
        created_by_type=ActorType.HUMAN,
        created_by_id="operator",
        created_at=NOW,
        updated_at=NOW,
    )


def _ticket(product_id: UUID, key: str) -> Ticket:
    return Ticket(
        id=uuid4(),
        product_id=product_id,
        key=key,
        title=f"Candidate {key}",
        objective="Exercise durable PM recovery",
        context="ATLAS-094M",
        status=TicketStatus.CI_PENDING,
        ticket_type=TicketType.INFRASTRUCTURE,
        risk_level=RiskLevel.HIGH,
        priority=1,
        source_anchor="docs/atlas/pm-resilience-and-retrospective-recovery.md",
        created_by_type=ActorType.SYSTEM,
        created_by_id="test",
        created_at=NOW,
        updated_at=NOW,
    )


@dataclass(frozen=True)
class SeededStore:
    database: Database
    candidate: Ticket
    starved: tuple[Ticket, Ticket]


def _seed_store(path: Path) -> SeededStore:
    database = Database(f"sqlite:///{path}")
    database.create_all()
    ProductRepo(database).add(_product(PRODUCT_ID, "ATLAS"))
    candidate = TicketRepo(database).add(_ticket(PRODUCT_ID, "ATLAS-290"))
    starved = (
        TicketRepo(database).add(_ticket(PRODUCT_ID, "ATLAS-291")),
        TicketRepo(database).add(_ticket(PRODUCT_ID, "ATLAS-292")),
    )
    return SeededStore(database, candidate, starved)


def _identity(
    candidate: Ticket | None = None,
    *,
    authoritative_episode_id: str = "ci-handoff:ATLAS-290:attachment-1",
    operation: str = "ci_handoff",
    product_id: UUID = PRODUCT_ID,
) -> PmRecoveryEpisodeIdentity:
    return PmRecoveryEpisodeIdentity(
        product_id=product_id,
        operation=operation,
        authority_id="pm:ci-handoff",
        authoritative_episode_id=authoritative_episode_id,
        candidate_ticket_id=None if candidate is None else candidate.id,
        candidate_ticket_key=None if candidate is None else candidate.key,
    )


def _blocker(
    starved: tuple[Ticket, ...] = (),
    *,
    code: str = "provider_unavailable",
) -> PmBlockerObservationIntent:
    return PmBlockerObservationIntent(
        code=code,
        kind=PmBlockerKind.RETRYABLE,
        authority_kind=PmBlockerAuthorityKind.OPERATION,
        authority_id="github:pull-request-observation",
        next_safe_retry_at=NOW + timedelta(minutes=1),
        capacity_impact=bool(starved),
        starved_candidates=tuple(
            PmStarvedCandidateRef(ticket_id=ticket.id, ticket_key=ticket.key)
            for ticket in reversed(starved)
        ),
        policy_namespace="pm-resilience",
        policy_revision=1,
        policy_fingerprint="a" * 64,
    )


@dataclass(frozen=True)
class RecoveryGeneration:
    database: Database
    repository: PmRecoveryRepo


def _register_recovery_generations(
    harness: TemporalHarness,
    built: list[tuple[RecoveryGeneration, Database, PmRecoveryRepo]],
) -> None:
    def factory(generation: ProcessGeneration) -> RecoveryGeneration:
        database = Database(f"sqlite:///{generation.harness.db_path}")
        resource = RecoveryGeneration(database, PmRecoveryRepo(database))
        built.append((resource, database, resource.repository))
        return resource

    def dispose(resource: object) -> None:
        assert isinstance(resource, RecoveryGeneration)
        resource.database.engine.dispose()

    harness.register_generation_resource("recovery", factory, disposer=dispose)


def _resource(generation: ProcessGeneration) -> RecoveryGeneration:
    resource = generation.resource("recovery")
    assert isinstance(resource, RecoveryGeneration)
    return resource


def test_episode_replay_survives_complete_repository_reconstruction(
    tmp_path: Path,
) -> None:
    path = tmp_path / "episode-replay.db"
    seeded = _seed_store(path)
    seeded.database.engine.dispose()
    built: list[tuple[RecoveryGeneration, Database, PmRecoveryRepo]] = []
    with TemporalHarness(db_path=path, initial_time=NOW) as harness:
        _register_recovery_generations(harness, built)
        with harness.new_generation() as first:
            episode = _resource(first).repository.establish_episode(
                _identity(seeded.candidate), created_at=NOW
            )
        with harness.new_generation() as second:
            replay = _resource(second).repository.establish_episode(
                _identity(seeded.candidate), created_at=NOW
            )
        with harness.new_generation() as third:
            stored = _resource(third).repository.get_episode(
                _identity(seeded.candidate).authoritative_episode_id
            )
            assert _resource(third).repository.sequence_high_water(PRODUCT_ID) == 1

    assert episode == replay == stored
    assert episode.episode_created_sequence == 1
    assert len(built) == 3
    assert all(left[0] is not right[0] for left, right in pairwise(built))
    assert all(left[1] is not right[1] for left, right in pairwise(built))
    assert all(left[2] is not right[2] for left, right in pairwise(built))


def test_conflicting_episode_replay_fails_without_consuming_sequence(
    tmp_path: Path,
) -> None:
    seeded = _seed_store(tmp_path / "episode-conflict.db")
    repo = PmRecoveryRepo(seeded.database)
    repo.establish_episode(_identity(seeded.candidate), created_at=NOW)
    changed = _identity(
        seeded.candidate,
        operation="admission",
    ).model_copy(
        update={
            "authoritative_episode_id": (
                _identity(seeded.candidate).authoritative_episode_id
            )
        }
    )

    with pytest.raises(PmRecoveryStorageError) as raised:
        repo.establish_episode(changed, created_at=NOW)

    assert raised.value.code is PmRecoveryStorageCode.EPISODE_IDENTITY_CONFLICT
    assert repo.sequence_high_water(PRODUCT_ID) == 1
    stored = repo.get_episode(changed.authoritative_episode_id)
    assert stored is not None
    assert stored.operation == "ci_handoff"


def test_global_sequence_orders_creation_and_evaluation_across_generations(
    tmp_path: Path,
) -> None:
    path = tmp_path / "global-sequence.db"
    seeded = _seed_store(path)
    ProductRepo(seeded.database).add(_product(SECOND_PRODUCT_ID, "OTHER"))
    seeded.database.engine.dispose()
    built: list[tuple[RecoveryGeneration, Database, PmRecoveryRepo]] = []
    with TemporalHarness(db_path=path, initial_time=NOW) as harness:
        _register_recovery_generations(harness, built)
        identities = [
            _identity(
                seeded.candidate,
                authoritative_episode_id=f"episode-{letter}",
            )
            for letter in "ABC"
        ]
        with harness.new_generation() as generation:
            a = _resource(generation).repository.establish_episode(
                identities[0], created_at=NOW
            )
        with harness.new_generation() as generation:
            b = _resource(generation).repository.establish_episode(
                identities[1], created_at=NOW
            )
        with harness.new_generation() as generation:
            a_eval = _resource(generation).repository.record_evaluation(
                authoritative_episode_id="episode-A",
                evaluation_id="evaluation-A",
                evaluated_at=NOW + timedelta(seconds=1),
            )
        with harness.new_generation() as generation:
            b_eval = _resource(generation).repository.record_evaluation(
                authoritative_episode_id="episode-B",
                evaluation_id="evaluation-B",
                evaluated_at=NOW + timedelta(seconds=1),
            )
        with harness.new_generation() as generation:
            c = _resource(generation).repository.establish_episode(
                identities[2], created_at=NOW + timedelta(seconds=2)
            )
        with harness.new_generation() as generation:
            repo = _resource(generation).repository
            ordered = repo.list_active_episodes_ordered(PRODUCT_ID)
            other = repo.establish_episode(
                _identity(
                    authoritative_episode_id="other-product-episode",
                    product_id=SECOND_PRODUCT_ID,
                ),
                created_at=NOW,
            )

    assert (a.episode_created_sequence, b.episode_created_sequence) == (1, 2)
    assert a_eval.episode.last_evaluated_sequence == 3
    assert b_eval.episode.last_evaluated_sequence == 4
    assert c.episode_created_sequence == 5
    assert [episode.authoritative_episode_id for episode in ordered] == [
        "episode-A",
        "episode-B",
        "episode-C",
    ]
    assert other.episode_created_sequence == 1


def test_concurrent_first_product_sequence_allocations_are_unique(
    tmp_path: Path,
) -> None:
    path = tmp_path / "concurrent-sequence.db"
    seeded = _seed_store(path)
    seeded.database.engine.dispose()
    identities = [
        _identity(
            seeded.candidate,
            authoritative_episode_id=f"concurrent-episode-{index}",
        )
        for index in range(8)
    ]

    def establish(identity: PmRecoveryEpisodeIdentity) -> int:
        database = Database(f"sqlite:///{path}")
        try:
            return (
                PmRecoveryRepo(database)
                .establish_episode(identity, created_at=NOW)
                .episode_created_sequence
            )
        finally:
            database.engine.dispose()

    with ThreadPoolExecutor(max_workers=len(identities)) as pool:
        sequences = list(pool.map(establish, identities))

    database = Database(f"sqlite:///{path}")
    repo = PmRecoveryRepo(database)
    assert sorted(sequences) == list(range(1, len(identities) + 1))
    assert repo.sequence_high_water(PRODUCT_ID) == len(identities)
    database.engine.dispose()


def test_concurrent_episode_evaluations_share_the_existing_product_counter(
    tmp_path: Path,
) -> None:
    path = tmp_path / "concurrent-evaluations.db"
    seeded = _seed_store(path)
    repo = PmRecoveryRepo(seeded.database)
    identities = [
        _identity(
            seeded.candidate,
            authoritative_episode_id=f"evaluation-episode-{index}",
        )
        for index in range(6)
    ]
    for identity in identities:
        repo.establish_episode(identity, created_at=NOW)
    seeded.database.engine.dispose()

    def evaluate(identity: PmRecoveryEpisodeIdentity) -> int:
        database = Database(f"sqlite:///{path}")
        try:
            result = PmRecoveryRepo(database).record_evaluation(
                authoritative_episode_id=identity.authoritative_episode_id,
                evaluation_id=f"evaluate:{identity.authoritative_episode_id}",
                evaluated_at=NOW + timedelta(seconds=1),
            )
            assert result.episode.last_evaluated_sequence is not None
            return result.episode.last_evaluated_sequence
        finally:
            database.engine.dispose()

    with ThreadPoolExecutor(max_workers=len(identities)) as pool:
        sequences = list(pool.map(evaluate, identities))

    database = Database(f"sqlite:///{path}")
    assert sorted(sequences) == list(range(7, 13))
    assert PmRecoveryRepo(database).sequence_high_water(PRODUCT_ID) == 12
    database.engine.dispose()


def test_blocker_recurrence_and_post_commit_replay_survive_reconstruction(
    tmp_path: Path,
) -> None:
    path = tmp_path / "recurrence.db"
    seeded = _seed_store(path)
    seeded.database.engine.dispose()
    built: list[tuple[RecoveryGeneration, Database, PmRecoveryRepo]] = []
    with TemporalHarness(db_path=path, initial_time=NOW) as harness:
        _register_recovery_generations(harness, built)
        with harness.new_generation() as generation:
            repo = _resource(generation).repository
            repo.establish_episode(_identity(seeded.candidate), created_at=NOW)
            first = repo.record_evaluation(
                authoritative_episode_id=_identity(
                    seeded.candidate
                ).authoritative_episode_id,
                evaluation_id="evaluation-1",
                evaluated_at=NOW + timedelta(seconds=1),
                blocker=_blocker(),
            )
            with pytest.raises(SimulatedProcessDeath):
                raise SimulatedProcessDeath("result lost after commit")
        with harness.new_generation() as generation:
            replay = _resource(generation).repository.record_evaluation(
                authoritative_episode_id=_identity(
                    seeded.candidate
                ).authoritative_episode_id,
                evaluation_id="evaluation-1",
                evaluated_at=NOW + timedelta(seconds=1),
                blocker=_blocker(),
            )
        with harness.new_generation() as generation:
            recurring = _resource(generation).repository.record_evaluation(
                authoritative_episode_id=_identity(
                    seeded.candidate
                ).authoritative_episode_id,
                evaluation_id="evaluation-2",
                evaluated_at=NOW + timedelta(seconds=2),
                blocker=_blocker(),
            )
        with harness.new_generation() as generation:
            changed = _resource(generation).repository.record_evaluation(
                authoritative_episode_id=_identity(
                    seeded.candidate
                ).authoritative_episode_id,
                evaluation_id="evaluation-3",
                evaluated_at=NOW + timedelta(seconds=3),
                blocker=_blocker(code="publication_ambiguous"),
            )
            active = _resource(generation).repository.list_blockers(
                product_id=PRODUCT_ID, active_only=True
            )

    assert first.blocker is not None
    assert replay.changed is False
    assert replay.blocker == first.blocker
    assert recurring.blocker is not None
    assert recurring.blocker.id == first.blocker.id
    assert recurring.blocker.blocker_fingerprint == first.blocker.blocker_fingerprint
    assert recurring.blocker.first_observed_at == NOW + timedelta(seconds=1)
    assert recurring.blocker.latest_observed_at == NOW + timedelta(seconds=2)
    assert recurring.blocker.consecutive_observations == 2
    assert changed.blocker is not None
    assert changed.blocker.blocker_fingerprint != first.blocker.blocker_fingerprint
    assert len(active) == 2


def test_supersession_is_durable_idempotent_and_allows_a_later_occurrence(
    tmp_path: Path,
) -> None:
    seeded = _seed_store(tmp_path / "supersession.db")
    repo = PmRecoveryRepo(seeded.database)
    repo.establish_episode(_identity(seeded.candidate), created_at=NOW)
    first = repo.record_evaluation(
        authoritative_episode_id=_identity(seeded.candidate).authoritative_episode_id,
        evaluation_id="evaluation-1",
        evaluated_at=NOW + timedelta(seconds=1),
        blocker=_blocker(),
    )
    assert first.blocker is not None

    changed = repo.supersede_blocker(
        product_id=PRODUCT_ID,
        blocker_fingerprint=first.blocker.blocker_fingerprint,
        superseded_by_event_id="progress-1",
        supersession_kind=PmBlockerSupersessionKind.PROGRESS,
        superseded_at=NOW + timedelta(seconds=2),
    )
    replay = repo.supersede_blocker(
        product_id=PRODUCT_ID,
        blocker_fingerprint=first.blocker.blocker_fingerprint,
        superseded_by_event_id="progress-1",
        supersession_kind=PmBlockerSupersessionKind.PROGRESS,
        superseded_at=NOW + timedelta(seconds=2),
    )
    with pytest.raises(PmRecoveryStorageError) as conflict:
        repo.supersede_blocker(
            product_id=PRODUCT_ID,
            blocker_fingerprint=first.blocker.blocker_fingerprint,
            superseded_by_event_id="different-progress",
            supersession_kind=PmBlockerSupersessionKind.PROGRESS,
            superseded_at=NOW + timedelta(seconds=2),
        )
    later = repo.record_evaluation(
        authoritative_episode_id=_identity(seeded.candidate).authoritative_episode_id,
        evaluation_id="evaluation-2",
        evaluated_at=NOW + timedelta(seconds=3),
        blocker=_blocker(),
    )

    assert changed.changed is True
    assert replay.changed is False
    assert conflict.value.code is PmRecoveryStorageCode.BLOCKER_SUPERSESSION_CONFLICT
    assert later.blocker is not None
    assert later.blocker.id != first.blocker.id
    assert later.blocker.blocker_fingerprint == first.blocker.blocker_fingerprint
    blockers = repo.list_blockers(product_id=PRODUCT_ID)
    assert len(blockers) == 2
    assert blockers[0].superseded_by_event_id == "progress-1"
    assert blockers[1].superseded_at is None


def test_starvation_state_projects_deterministically_into_health_after_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "starvation.db"
    seeded = _seed_store(path)
    repo = PmRecoveryRepo(seeded.database)
    repo.establish_episode(_identity(seeded.candidate), created_at=NOW)
    result = repo.record_evaluation(
        authoritative_episode_id=_identity(seeded.candidate).authoritative_episode_id,
        evaluation_id="evaluation-1",
        evaluated_at=NOW + timedelta(seconds=1),
        blocker=_blocker(seeded.starved),
    )
    assert result.blocker is not None
    seeded.database.engine.dispose()

    rebuilt = Database(f"sqlite:///{path}")
    blocker = PmRecoveryRepo(rebuilt).list_blockers(
        product_id=PRODUCT_ID, active_only=True
    )[0]
    observation = project_durable_blocker(blocker)
    before = NOW + timedelta(minutes=4)
    after = NOW + timedelta(minutes=6)

    def health(at: datetime) -> PmHealthAssessment:
        return assess_pm_health(
            PmHealthInputs(
                observed_at=at,
                last_heartbeat_at=at,
                last_coherent_board_at=at,
                last_convergence_at=at,
                last_progress_at=at,
                progress_expected=True,
                blocker_observations=(observation,),
            ),
            PmHealthPolicy(),
        )

    degraded = health(before)
    blocked = health(after)
    assert observation.starved_candidate_keys == ("ATLAS-291", "ATLAS-292")
    assert observation.starvation_started_at == NOW + timedelta(seconds=1)
    assert observation.fingerprint == blocker.blocker_fingerprint
    assert degraded.status is PmHealthStatus.DEGRADED
    assert blocked.status is PmHealthStatus.BLOCKED
    assert PmHealthReasonCode.STARVATION in {reason.code for reason in blocked.reasons}
    rebuilt.engine.dispose()


def test_episode_and_counter_roll_back_together_on_late_insert_failure(
    tmp_path: Path,
) -> None:
    seeded = _seed_store(tmp_path / "transaction.db")
    repo = PmRecoveryRepo(seeded.database)
    with seeded.database.engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TRIGGER fail_pm_episode_insert "
                "BEFORE INSERT ON pm_recovery_episodes "
                "BEGIN SELECT RAISE(ABORT, 'seeded episode failure'); END"
            )
        )

    with pytest.raises(sa.exc.IntegrityError):
        repo.establish_episode(_identity(seeded.candidate), created_at=NOW)
    assert repo.sequence_high_water(PRODUCT_ID) == 0
    assert (
        repo.get_episode(_identity(seeded.candidate).authoritative_episode_id) is None
    )

    with seeded.database.engine.begin() as connection:
        connection.execute(sa.text("DROP TRIGGER fail_pm_episode_insert"))
    episode = repo.establish_episode(_identity(seeded.candidate), created_at=NOW)
    assert episode.episode_created_sequence == 1


def test_evaluation_cursor_and_blocker_roll_back_together_on_late_failure(
    tmp_path: Path,
) -> None:
    seeded = _seed_store(tmp_path / "evaluation-transaction.db")
    repo = PmRecoveryRepo(seeded.database)
    identity = _identity(seeded.candidate)
    repo.establish_episode(identity, created_at=NOW)
    with seeded.database.engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TRIGGER fail_pm_blocker_insert "
                "BEFORE INSERT ON pm_blocker_occurrences "
                "BEGIN SELECT RAISE(ABORT, 'seeded blocker failure'); END"
            )
        )

    with pytest.raises(sa.exc.IntegrityError):
        repo.record_evaluation(
            authoritative_episode_id=identity.authoritative_episode_id,
            evaluation_id="evaluation-1",
            evaluated_at=NOW + timedelta(seconds=1),
            blocker=_blocker(),
        )
    rolled_back = repo.get_episode(identity.authoritative_episode_id)
    assert rolled_back is not None
    assert rolled_back.last_evaluated_sequence is None
    assert repo.sequence_high_water(PRODUCT_ID) == 1
    assert repo.list_blockers(product_id=PRODUCT_ID) == []

    with seeded.database.engine.begin() as connection:
        connection.execute(sa.text("DROP TRIGGER fail_pm_blocker_insert"))
    committed = repo.record_evaluation(
        authoritative_episode_id=identity.authoritative_episode_id,
        evaluation_id="evaluation-1",
        evaluated_at=NOW + timedelta(seconds=1),
        blocker=_blocker(),
    )
    assert committed.episode.last_evaluated_sequence == 2
    assert committed.blocker is not None


def test_closed_episode_is_durable_and_cannot_be_evaluated(tmp_path: Path) -> None:
    seeded = _seed_store(tmp_path / "closure.db")
    repo = PmRecoveryRepo(seeded.database)
    identity = _identity(seeded.candidate)
    repo.establish_episode(identity, created_at=NOW)
    close = repo.close_episode(
        authoritative_episode_id=identity.authoritative_episode_id,
        closure_event_id="publication-replaced-1",
        closure_kind=PmRecoveryEpisodeClosureKind.PUBLICATION_REPLACEMENT,
        closed_at=NOW + timedelta(seconds=1),
    )
    replay = repo.close_episode(
        authoritative_episode_id=identity.authoritative_episode_id,
        closure_event_id="publication-replaced-1",
        closure_kind=PmRecoveryEpisodeClosureKind.PUBLICATION_REPLACEMENT,
        closed_at=NOW + timedelta(seconds=1),
    )
    with pytest.raises(PmRecoveryStorageError) as raised:
        repo.record_evaluation(
            authoritative_episode_id=identity.authoritative_episode_id,
            evaluation_id="too-late",
            evaluated_at=NOW + timedelta(seconds=2),
        )

    assert close.changed is True
    assert replay.changed is False
    assert raised.value.code is PmRecoveryStorageCode.EPISODE_CLOSED
    assert repo.list_active_episodes_ordered(PRODUCT_ID) == []


def test_sqlite_constraints_bound_episode_and_starvation_rows(tmp_path: Path) -> None:
    seeded = _seed_store(tmp_path / "constraints.db")
    repo = PmRecoveryRepo(seeded.database)
    episode = repo.establish_episode(_identity(seeded.candidate), created_at=NOW)
    evaluated = repo.record_evaluation(
        authoritative_episode_id=episode.authoritative_episode_id,
        evaluation_id="evaluation-1",
        evaluated_at=NOW + timedelta(seconds=1),
        blocker=_blocker(seeded.starved),
    )
    assert evaluated.blocker is not None

    with (
        pytest.raises(sa.exc.IntegrityError),
        seeded.database.engine.begin() as connection,
    ):
        connection.execute(
            sa.update(PmRecoveryEpisodeRow)
            .where(PmRecoveryEpisodeRow.id == episode.id)
            .values(operation="x" * 129)
        )
    with (
        pytest.raises(sa.exc.IntegrityError),
        seeded.database.engine.begin() as connection,
    ):
        connection.execute(
            sa.insert(PmBlockerStarvedCandidateRow).values(
                blocker_occurrence_id=evaluated.blocker.id,
                ordinal=129,
                ticket_id=seeded.starved[0].id,
                ticket_key="ATLAS-999",
                started_at=NOW,
            )
        )

    indexes = {
        index["name"]
        for table in ("pm_recovery_episodes", "pm_blocker_occurrences")
        for index in sa.inspect(seeded.database.engine).get_indexes(table)
    }
    assert {
        "ix_pm_recovery_episodes_fairness",
        "ix_pm_blocker_occurrences_active_operation",
        "ix_pm_blocker_occurrences_active_candidate",
    } <= indexes

"""Durable PM recovery, fairness, blocker, and reconstruction contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from threading import Barrier
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
    PmBlockerCode,
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
    PmBlockerOccurrenceRow,
    PmBlockerStarvedCandidateRow,
    PmRecoveryEpisodeRow,
    PmRecoverySequenceCounterRow,
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
    authority_id: str = "pm:ci-handoff",
) -> PmRecoveryEpisodeIdentity:
    return PmRecoveryEpisodeIdentity(
        product_id=product_id,
        operation=operation,
        authority_id=authority_id,
        authoritative_episode_id=authoritative_episode_id,
        candidate_ticket_id=None if candidate is None else candidate.id,
        candidate_ticket_key=None if candidate is None else candidate.key,
    )


def _blocker(
    starved: tuple[Ticket, ...] = (),
    *,
    code: PmBlockerCode = PmBlockerCode.PROVIDER_UNAVAILABLE,
    authority_kind: PmBlockerAuthorityKind = PmBlockerAuthorityKind.OPERATION,
) -> PmBlockerObservationIntent:
    return PmBlockerObservationIntent(
        code=code,
        kind=PmBlockerKind.RETRYABLE,
        authority_kind=authority_kind,
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
                _identity(seeded.candidate), created_at=NOW + timedelta(hours=1)
            )
        with harness.new_generation() as third:
            stored = _resource(third).repository.get_episode(
                _identity(seeded.candidate).episode_id
            )
            assert _resource(third).repository.sequence_high_water(PRODUCT_ID) == 1

    assert episode == replay == stored
    assert episode.episode_created_sequence == 1
    assert len(built) == 3
    assert all(left[0] is not right[0] for left, right in pairwise(built))
    assert all(left[1] is not right[1] for left, right in pairwise(built))
    assert all(left[2] is not right[2] for left, right in pairwise(built))


def test_distinct_identity_in_an_active_scope_fails_without_consuming_sequence(
    tmp_path: Path,
) -> None:
    seeded = _seed_store(tmp_path / "episode-conflict.db")
    repo = PmRecoveryRepo(seeded.database)
    repo.establish_episode(_identity(seeded.candidate), created_at=NOW)
    changed = _identity(
        seeded.candidate, authoritative_episode_id="replacement-without-proof"
    )

    with pytest.raises(PmRecoveryStorageError) as raised:
        repo.establish_episode(changed, created_at=NOW)

    assert raised.value.code is PmRecoveryStorageCode.EPISODE_ACTIVE_SCOPE_CONFLICT
    assert repo.sequence_high_water(PRODUCT_ID) == 1
    stored = repo.get_episode(_identity(seeded.candidate).episode_id)
    assert stored is not None
    assert stored.operation == "ci_handoff"


def test_authoritative_episode_token_is_scoped_by_full_identity(tmp_path: Path) -> None:
    seeded = _seed_store(tmp_path / "episode-scope.db")
    ProductRepo(seeded.database).add(_product(SECOND_PRODUCT_ID, "OTHER"))
    repo = PmRecoveryRepo(seeded.database)
    first_identity = _identity(
        authoritative_episode_id="provider-local-episode", product_id=PRODUCT_ID
    )
    second_identity = _identity(
        authoritative_episode_id="provider-local-episode",
        product_id=SECOND_PRODUCT_ID,
    )

    first = repo.establish_episode(first_identity, created_at=NOW)
    second = repo.establish_episode(second_identity, created_at=NOW)

    assert first.id != second.id
    assert repo.get_episode(first.id) == first
    assert repo.get_episode(second.id) == second
    assert first.episode_created_sequence == second.episode_created_sequence == 1


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
                authority_id=f"pm:ci-handoff:{letter}",
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
                episode_id=identities[0].episode_id,
                expected_cursor_sequence=a.episode_created_sequence,
                evaluation_id="evaluation-A",
                evaluated_at=NOW + timedelta(seconds=1),
            )
        with harness.new_generation() as generation:
            b_eval = _resource(generation).repository.record_evaluation(
                episode_id=identities[1].episode_id,
                expected_cursor_sequence=b.episode_created_sequence,
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
            authority_id=f"pm:concurrent:{index}",
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
            authority_id=f"pm:evaluation:{index}",
        )
        for index in range(6)
    ]
    episodes = [
        repo.establish_episode(identity, created_at=NOW) for identity in identities
    ]
    seeded.database.engine.dispose()

    def evaluate(item: tuple[PmRecoveryEpisodeIdentity, int]) -> int:
        identity, expected_cursor = item
        database = Database(f"sqlite:///{path}")
        try:
            result = PmRecoveryRepo(database).record_evaluation(
                episode_id=identity.episode_id,
                expected_cursor_sequence=expected_cursor,
                evaluation_id=f"evaluate:{identity.authoritative_episode_id}",
                evaluated_at=NOW + timedelta(seconds=1),
            )
            assert result.episode.last_evaluated_sequence is not None
            return result.episode.last_evaluated_sequence
        finally:
            database.engine.dispose()

    with ThreadPoolExecutor(max_workers=len(identities)) as pool:
        sequences = list(
            pool.map(
                evaluate,
                zip(
                    identities,
                    (episode.episode_created_sequence for episode in episodes),
                    strict=True,
                ),
            )
        )

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
            episode = repo.establish_episode(
                _identity(seeded.candidate), created_at=NOW
            )
            first = repo.record_evaluation(
                episode_id=episode.id,
                expected_cursor_sequence=episode.fairness_cursor,
                evaluation_id="evaluation-1",
                evaluated_at=NOW + timedelta(seconds=1),
                blocker=_blocker(),
            )
            with pytest.raises(SimulatedProcessDeath):
                raise SimulatedProcessDeath("result lost after commit")
        with harness.new_generation() as generation:
            replay = _resource(generation).repository.record_evaluation(
                episode_id=_identity(seeded.candidate).episode_id,
                expected_cursor_sequence=1,
                evaluation_id="evaluation-1",
                evaluated_at=NOW + timedelta(seconds=1),
                blocker=_blocker(),
            )
        with harness.new_generation() as generation:
            recurring = _resource(generation).repository.record_evaluation(
                episode_id=_identity(seeded.candidate).episode_id,
                expected_cursor_sequence=2,
                evaluation_id="evaluation-2",
                evaluated_at=NOW + timedelta(seconds=2),
                blocker=_blocker(),
            )
        with harness.new_generation() as generation:
            changed = _resource(generation).repository.record_evaluation(
                episode_id=_identity(seeded.candidate).episode_id,
                expected_cursor_sequence=3,
                evaluation_id="evaluation-3",
                evaluated_at=NOW + timedelta(seconds=3),
                blocker=_blocker(code=PmBlockerCode.PUBLICATION_AMBIGUOUS),
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


def test_stale_evaluation_identity_cannot_reenter_after_a_later_evaluation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "stale-evaluation.db"
    seeded = _seed_store(path)
    repo = PmRecoveryRepo(seeded.database)
    episode = repo.establish_episode(_identity(seeded.candidate), created_at=NOW)
    first = repo.record_evaluation(
        episode_id=episode.id,
        expected_cursor_sequence=episode.fairness_cursor,
        evaluation_id="evaluation-1",
        evaluated_at=NOW + timedelta(seconds=1),
        blocker=_blocker(),
    )
    assert first.blocker is not None
    second = repo.record_evaluation(
        episode_id=episode.id,
        expected_cursor_sequence=first.episode.fairness_cursor,
        evaluation_id="evaluation-2",
        evaluated_at=NOW + timedelta(seconds=2),
        blocker=_blocker(),
    )
    assert second.blocker is not None
    seeded.database.engine.dispose()

    rebuilt = Database(f"sqlite:///{path}")
    rebuilt_repo = PmRecoveryRepo(rebuilt)
    with pytest.raises(PmRecoveryStorageError) as stale:
        rebuilt_repo.record_evaluation(
            episode_id=episode.id,
            expected_cursor_sequence=episode.fairness_cursor,
            evaluation_id="evaluation-1",
            evaluated_at=NOW + timedelta(seconds=3),
            blocker=_blocker(),
        )

    assert stale.value.code is PmRecoveryStorageCode.EVALUATION_CURSOR_CONFLICT
    assert rebuilt_repo.sequence_high_water(PRODUCT_ID) == 3
    blocker = rebuilt_repo.get_blocker(first.blocker.id)
    assert blocker is not None
    assert blocker.consecutive_observations == 2
    rebuilt.engine.dispose()


def test_timestamp_regression_is_rejected_after_repository_reconstruction(
    tmp_path: Path,
) -> None:
    path = tmp_path / "evaluation-time-regression.db"
    seeded = _seed_store(path)
    seeded.database.engine.dispose()
    built: list[tuple[RecoveryGeneration, Database, PmRecoveryRepo]] = []
    with TemporalHarness(db_path=path, initial_time=NOW) as harness:
        _register_recovery_generations(harness, built)
        with harness.new_generation() as generation:
            repo = _resource(generation).repository
            episode = repo.establish_episode(
                _identity(seeded.candidate), created_at=NOW
            )
            evaluated = repo.record_evaluation(
                episode_id=episode.id,
                expected_cursor_sequence=episode.fairness_cursor,
                evaluation_id="evaluation-1",
                evaluated_at=NOW + timedelta(seconds=2),
                blocker=_blocker(),
            )
            assert evaluated.blocker is not None
        with harness.new_generation() as generation:
            repo = _resource(generation).repository
            before_episode = repo.get_episode(episode.id)
            before_blocker = repo.get_blocker(evaluated.blocker.id)
            with pytest.raises(PmRecoveryStorageError) as regressed:
                repo.record_evaluation(
                    episode_id=episode.id,
                    expected_cursor_sequence=evaluated.episode.fairness_cursor,
                    evaluation_id="evaluation-2",
                    evaluated_at=NOW + timedelta(seconds=2),
                    blocker=_blocker(),
                )
            after_episode = repo.get_episode(episode.id)
            after_blocker = repo.get_blocker(evaluated.blocker.id)
            high_water = repo.sequence_high_water(PRODUCT_ID)

    assert regressed.value.code is PmRecoveryStorageCode.EVALUATION_OUT_OF_ORDER
    assert before_episode == after_episode == evaluated.episode
    assert before_blocker == after_blocker == evaluated.blocker
    assert high_water == evaluated.episode.last_evaluated_sequence == 2


def test_contradictory_latest_evaluation_replay_fails_without_mutation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "evaluation-replay-conflict.db"
    seeded = _seed_store(path)
    repo = PmRecoveryRepo(seeded.database)
    episode = repo.establish_episode(_identity(seeded.candidate), created_at=NOW)
    evaluated = repo.record_evaluation(
        episode_id=episode.id,
        expected_cursor_sequence=episode.fairness_cursor,
        evaluation_id="evaluation-1",
        evaluated_at=NOW + timedelta(seconds=1),
        blocker=_blocker(seeded.starved),
    )
    assert evaluated.blocker is not None
    seeded.database.engine.dispose()

    rebuilt = Database(f"sqlite:///{path}")
    rebuilt_repo = PmRecoveryRepo(rebuilt)
    with pytest.raises(PmRecoveryStorageError) as conflict:
        rebuilt_repo.record_evaluation(
            episode_id=episode.id,
            expected_cursor_sequence=episode.fairness_cursor,
            evaluation_id="evaluation-1",
            evaluated_at=NOW + timedelta(seconds=1),
            blocker=_blocker(seeded.starved, code=PmBlockerCode.PUBLICATION_AMBIGUOUS),
        )

    assert conflict.value.code is PmRecoveryStorageCode.EVALUATION_REPLAY_CONFLICT
    assert rebuilt_repo.sequence_high_water(PRODUCT_ID) == 2
    assert rebuilt_repo.get_episode(episode.id) == evaluated.episode
    assert rebuilt_repo.get_blocker(evaluated.blocker.id) == evaluated.blocker
    assert len(rebuilt_repo.list_blockers(product_id=PRODUCT_ID)) == 1
    rebuilt.engine.dispose()


def test_reused_evaluation_id_replays_no_blocker_without_historical_leak(
    tmp_path: Path,
) -> None:
    path = tmp_path / "reused-evaluation-no-blocker.db"
    seeded = _seed_store(path)
    repo = PmRecoveryRepo(seeded.database)
    episode = repo.establish_episode(_identity(seeded.candidate), created_at=NOW)
    first = repo.record_evaluation(
        episode_id=episode.id,
        expected_cursor_sequence=episode.fairness_cursor,
        evaluation_id="evaluation-X",
        evaluated_at=NOW + timedelta(seconds=1),
        blocker=_blocker(),
    )
    assert first.blocker is not None
    intervening = repo.record_evaluation(
        episode_id=episode.id,
        expected_cursor_sequence=first.episode.fairness_cursor,
        evaluation_id="evaluation-Y",
        evaluated_at=NOW + timedelta(seconds=2),
    )
    reused = repo.record_evaluation(
        episode_id=episode.id,
        expected_cursor_sequence=intervening.episode.fairness_cursor,
        evaluation_id="evaluation-X",
        evaluated_at=NOW + timedelta(seconds=3),
    )
    seeded.database.engine.dispose()

    rebuilt = Database(f"sqlite:///{path}")
    replay = PmRecoveryRepo(rebuilt).record_evaluation(
        episode_id=episode.id,
        expected_cursor_sequence=intervening.episode.fairness_cursor,
        evaluation_id="evaluation-X",
        evaluated_at=NOW + timedelta(seconds=3),
    )

    assert reused.blocker is None
    assert replay.blocker is None
    assert replay.episode == reused.episode
    assert replay.changed is False
    rebuilt.engine.dispose()


def test_reused_evaluation_id_cannot_collide_with_superseded_occurrence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "reused-evaluation-superseded-blocker.db"
    seeded = _seed_store(path)
    repo = PmRecoveryRepo(seeded.database)
    episode = repo.establish_episode(_identity(seeded.candidate), created_at=NOW)
    first = repo.record_evaluation(
        episode_id=episode.id,
        expected_cursor_sequence=episode.fairness_cursor,
        evaluation_id="evaluation-X",
        evaluated_at=NOW + timedelta(seconds=1),
        blocker=_blocker(),
    )
    assert first.blocker is not None
    repo.supersede_blocker(
        blocker_id=first.blocker.id,
        superseded_by_event_id="progress-1",
        supersession_kind=PmBlockerSupersessionKind.PROGRESS,
        superseded_at=NOW + timedelta(seconds=2),
    )
    intervening = repo.record_evaluation(
        episode_id=episode.id,
        expected_cursor_sequence=first.episode.fairness_cursor,
        evaluation_id="evaluation-Y",
        evaluated_at=NOW + timedelta(seconds=3),
    )
    reused = repo.record_evaluation(
        episode_id=episode.id,
        expected_cursor_sequence=intervening.episode.fairness_cursor,
        evaluation_id="evaluation-X",
        evaluated_at=NOW + timedelta(seconds=4),
        blocker=_blocker(),
    )
    assert reused.blocker is not None
    seeded.database.engine.dispose()

    rebuilt = Database(f"sqlite:///{path}")
    replay = PmRecoveryRepo(rebuilt).record_evaluation(
        episode_id=episode.id,
        expected_cursor_sequence=intervening.episode.fairness_cursor,
        evaluation_id="evaluation-X",
        evaluated_at=NOW + timedelta(seconds=4),
        blocker=_blocker(),
    )

    assert reused.blocker.id != first.blocker.id
    assert replay.blocker == reused.blocker
    assert replay.changed is False
    rebuilt.engine.dispose()


def test_authority_kind_is_part_of_stable_blocker_identity(tmp_path: Path) -> None:
    seeded = _seed_store(tmp_path / "authority-kind.db")
    repo = PmRecoveryRepo(seeded.database)
    episode = repo.establish_episode(_identity(seeded.candidate), created_at=NOW)
    first = repo.record_evaluation(
        episode_id=episode.id,
        expected_cursor_sequence=episode.fairness_cursor,
        evaluation_id="evaluation-1",
        evaluated_at=NOW + timedelta(seconds=1),
        blocker=_blocker(authority_kind=PmBlockerAuthorityKind.OPERATION),
    )
    second = repo.record_evaluation(
        episode_id=episode.id,
        expected_cursor_sequence=first.episode.fairness_cursor,
        evaluation_id="evaluation-2",
        evaluated_at=NOW + timedelta(seconds=2),
        blocker=_blocker(authority_kind=PmBlockerAuthorityKind.LEASE),
    )

    assert first.blocker is not None
    assert second.blocker is not None
    assert first.blocker.blocker_fingerprint != second.blocker.blocker_fingerprint
    assert len(repo.list_blockers(product_id=PRODUCT_ID, active_only=True)) == 2


def test_supersession_is_durable_idempotent_and_allows_a_later_occurrence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "supersession.db"
    seeded = _seed_store(path)
    seeded.database.engine.dispose()
    built: list[tuple[RecoveryGeneration, Database, PmRecoveryRepo]] = []
    with TemporalHarness(db_path=path, initial_time=NOW) as harness:
        _register_recovery_generations(harness, built)
        with harness.new_generation() as generation:
            repo = _resource(generation).repository
            episode = repo.establish_episode(
                _identity(seeded.candidate), created_at=NOW
            )
            first = repo.record_evaluation(
                episode_id=episode.id,
                expected_cursor_sequence=episode.fairness_cursor,
                evaluation_id="evaluation-1",
                evaluated_at=NOW + timedelta(seconds=1),
                blocker=_blocker(),
            )
            assert first.blocker is not None
        with harness.new_generation() as generation:
            changed = _resource(generation).repository.supersede_blocker(
                blocker_id=first.blocker.id,
                superseded_by_event_id="progress-1",
                supersession_kind=PmBlockerSupersessionKind.PROGRESS,
                superseded_at=NOW + timedelta(seconds=2),
            )
        with harness.new_generation() as generation:
            replay = _resource(generation).repository.supersede_blocker(
                blocker_id=first.blocker.id,
                superseded_by_event_id="progress-1",
                supersession_kind=PmBlockerSupersessionKind.PROGRESS,
                superseded_at=NOW + timedelta(seconds=2),
            )
            with pytest.raises(PmRecoveryStorageError) as conflict:
                _resource(generation).repository.supersede_blocker(
                    blocker_id=first.blocker.id,
                    superseded_by_event_id="different-progress",
                    supersession_kind=PmBlockerSupersessionKind.PROGRESS,
                    superseded_at=NOW + timedelta(seconds=2),
                )
        with harness.new_generation() as generation:
            later = _resource(generation).repository.record_evaluation(
                episode_id=episode.id,
                expected_cursor_sequence=2,
                evaluation_id="evaluation-2",
                evaluated_at=NOW + timedelta(seconds=3),
                blocker=_blocker(),
            )
        with harness.new_generation() as generation:
            old_replay = _resource(generation).repository.supersede_blocker(
                blocker_id=first.blocker.id,
                superseded_by_event_id="progress-1",
                supersession_kind=PmBlockerSupersessionKind.PROGRESS,
                superseded_at=NOW + timedelta(seconds=2),
            )
            blockers = _resource(generation).repository.list_blockers(
                product_id=PRODUCT_ID
            )

    assert changed.changed is True
    assert replay.changed is False
    assert old_replay.changed is False
    assert conflict.value.code is PmRecoveryStorageCode.BLOCKER_SUPERSESSION_CONFLICT
    assert later.blocker is not None
    assert later.blocker.id != first.blocker.id
    assert later.blocker.blocker_fingerprint == first.blocker.blocker_fingerprint
    assert len(blockers) == 2
    assert blockers[0].superseded_by_event_id == "progress-1"
    assert blockers[1].superseded_at is None


def test_concurrent_contradictory_supersessions_have_one_winner(tmp_path: Path) -> None:
    path = tmp_path / "concurrent-supersession.db"
    seeded = _seed_store(path)
    repo = PmRecoveryRepo(seeded.database)
    episode = repo.establish_episode(_identity(seeded.candidate), created_at=NOW)
    observed = repo.record_evaluation(
        episode_id=episode.id,
        expected_cursor_sequence=episode.fairness_cursor,
        evaluation_id="evaluation-1",
        evaluated_at=NOW + timedelta(seconds=1),
        blocker=_blocker(),
    )
    assert observed.blocker is not None
    blocker_id = observed.blocker.id
    seeded.database.engine.dispose()
    barrier = Barrier(2)

    def supersede(event_id: str) -> tuple[str, object]:
        database = Database(f"sqlite:///{path}")
        try:
            barrier.wait()
            try:
                result = PmRecoveryRepo(database).supersede_blocker(
                    blocker_id=blocker_id,
                    superseded_by_event_id=event_id,
                    supersession_kind=PmBlockerSupersessionKind.PROGRESS,
                    superseded_at=NOW + timedelta(seconds=2),
                )
                return ("changed", result.changed)
            except PmRecoveryStorageError as error:
                return ("error", error.code)
        finally:
            database.engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(supersede, ("progress-a", "progress-b")))

    assert outcomes.count(("changed", True)) == 1
    assert (
        outcomes.count(("error", PmRecoveryStorageCode.BLOCKER_SUPERSESSION_CONFLICT))
        == 1
    )


def test_authoritative_replacement_is_atomic_and_replayable_after_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "replacement.db"
    seeded = _seed_store(path)
    seeded.database.engine.dispose()
    original_identity = _identity(seeded.candidate)
    replacement_identity = _identity(
        seeded.candidate, authoritative_episode_id="replacement-episode"
    )
    built: list[tuple[RecoveryGeneration, Database, PmRecoveryRepo]] = []
    with TemporalHarness(db_path=path, initial_time=NOW) as harness:
        _register_recovery_generations(harness, built)
        with harness.new_generation() as generation:
            repo = _resource(generation).repository
            original = repo.establish_episode(original_identity, created_at=NOW)
            observed = repo.record_evaluation(
                episode_id=original.id,
                expected_cursor_sequence=original.fairness_cursor,
                evaluation_id="evaluation-before-replacement",
                evaluated_at=NOW + timedelta(seconds=1),
                blocker=_blocker(),
            )
            assert observed.blocker is not None
        with harness.new_generation() as generation:
            replacement = _resource(generation).repository.replace_episode(
                expected_episode_id=original.id,
                replacement=replacement_identity,
                closure_event_id="publication-replacement-1",
                closure_kind=PmRecoveryEpisodeClosureKind.PUBLICATION_REPLACEMENT,
                replaced_at=NOW + timedelta(seconds=2),
            )
            with pytest.raises(SimulatedProcessDeath):
                raise SimulatedProcessDeath("replacement result lost after commit")
        with harness.new_generation() as generation:
            repo = _resource(generation).repository
            replay = repo.replace_episode(
                expected_episode_id=original.id,
                replacement=replacement_identity,
                closure_event_id="publication-replacement-1",
                closure_kind=PmRecoveryEpisodeClosureKind.PUBLICATION_REPLACEMENT,
                replaced_at=NOW + timedelta(seconds=2),
            )
            old = repo.get_episode(original.id)
            old_blocker = repo.get_blocker(observed.blocker.id)
            new_observation = repo.record_evaluation(
                episode_id=replay.episode.id,
                expected_cursor_sequence=replay.episode.fairness_cursor,
                evaluation_id="evaluation-after-replacement",
                evaluated_at=NOW + timedelta(seconds=3),
                blocker=_blocker(),
            )

    assert replacement.changed is True
    assert replay.changed is False
    assert replacement.episode.episode_created_sequence == 3
    assert replay.episode.replaces_episode_id == original.id
    assert replay.episode.replacement_event_id == "publication-replacement-1"
    assert old is not None and old.closed_at == NOW + timedelta(seconds=2)
    assert old_blocker is not None
    assert old_blocker.superseded_by_event_id == "publication-replacement-1"
    assert new_observation.blocker is not None
    assert (
        new_observation.blocker.blocker_fingerprint
        != observed.blocker.blocker_fingerprint
    )


def test_historical_episode_cannot_masquerade_as_replacement_replay(
    tmp_path: Path,
) -> None:
    seeded = _seed_store(tmp_path / "replacement-lineage.db")
    repo = PmRecoveryRepo(seeded.database)
    historical_identity = _identity(
        seeded.candidate, authoritative_episode_id="historical-successor"
    )
    historical = repo.establish_episode(historical_identity, created_at=NOW)
    repo.close_episode(
        episode_id=historical.id,
        closure_event_id="historical-close",
        closure_kind=PmRecoveryEpisodeClosureKind.RECOVERY_COMPLETED,
        closed_at=NOW + timedelta(seconds=1),
    )
    predecessor_identity = _identity(
        seeded.candidate, authoritative_episode_id="later-predecessor"
    )
    predecessor = repo.establish_episode(
        predecessor_identity, created_at=NOW + timedelta(seconds=2)
    )
    repo.close_episode(
        episode_id=predecessor.id,
        closure_event_id="replacement-event",
        closure_kind=PmRecoveryEpisodeClosureKind.PUBLICATION_REPLACEMENT,
        closed_at=NOW + timedelta(seconds=3),
    )

    with pytest.raises(PmRecoveryStorageError) as conflict:
        repo.replace_episode(
            expected_episode_id=predecessor.id,
            replacement=historical_identity,
            closure_event_id="replacement-event",
            closure_kind=PmRecoveryEpisodeClosureKind.PUBLICATION_REPLACEMENT,
            replaced_at=NOW + timedelta(seconds=3),
        )

    assert conflict.value.code is PmRecoveryStorageCode.EPISODE_CLOSURE_CONFLICT


def test_concurrent_competing_replacements_have_one_winner(tmp_path: Path) -> None:
    path = tmp_path / "competing-replacements.db"
    seeded = _seed_store(path)
    original_identity = _identity(seeded.candidate)
    original = PmRecoveryRepo(seeded.database).establish_episode(
        original_identity, created_at=NOW
    )
    seeded.database.engine.dispose()
    barrier = Barrier(2)
    replacements = (
        _identity(seeded.candidate, authoritative_episode_id="replacement-a"),
        _identity(seeded.candidate, authoritative_episode_id="replacement-b"),
    )

    def replace(identity: PmRecoveryEpisodeIdentity) -> tuple[str, object]:
        database = Database(f"sqlite:///{path}")
        try:
            barrier.wait()
            try:
                result = PmRecoveryRepo(database).replace_episode(
                    expected_episode_id=original.id,
                    replacement=identity,
                    closure_event_id=f"event:{identity.authoritative_episode_id}",
                    closure_kind=(PmRecoveryEpisodeClosureKind.PUBLICATION_REPLACEMENT),
                    replaced_at=NOW + timedelta(seconds=1),
                )
                return ("changed", result.changed)
            except PmRecoveryStorageError as error:
                return ("error", error.code)
        finally:
            database.engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(replace, replacements))

    assert outcomes.count(("changed", True)) == 1
    assert (
        outcomes.count(("error", PmRecoveryStorageCode.EPISODE_CLOSURE_CONFLICT)) == 1
    )
    rebuilt = Database(f"sqlite:///{path}")
    repo = PmRecoveryRepo(rebuilt)
    assert repo.sequence_high_water(PRODUCT_ID) == 2
    assert len(repo.list_active_episodes_ordered(PRODUCT_ID)) == 1
    rebuilt.engine.dispose()


def test_replacement_insert_failure_rolls_back_old_scope_blocker_and_counter(
    tmp_path: Path,
) -> None:
    path = tmp_path / "replacement-rollback.db"
    seeded = _seed_store(path)
    repo = PmRecoveryRepo(seeded.database)
    original = repo.establish_episode(_identity(seeded.candidate), created_at=NOW)
    observed = repo.record_evaluation(
        episode_id=original.id,
        expected_cursor_sequence=original.fairness_cursor,
        evaluation_id="evaluation-1",
        evaluated_at=NOW + timedelta(seconds=1),
        blocker=_blocker(),
    )
    assert observed.blocker is not None
    with seeded.database.engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TRIGGER fail_pm_replacement_insert "
                "BEFORE INSERT ON pm_recovery_episodes "
                "BEGIN SELECT RAISE(ABORT, 'seeded replacement failure'); END"
            )
        )
    replacement_identity = _identity(
        seeded.candidate, authoritative_episode_id="replacement-after-failure"
    )
    with pytest.raises(sa.exc.IntegrityError):
        repo.replace_episode(
            expected_episode_id=original.id,
            replacement=replacement_identity,
            closure_event_id="replacement-event",
            closure_kind=PmRecoveryEpisodeClosureKind.PUBLICATION_REPLACEMENT,
            replaced_at=NOW + timedelta(seconds=2),
        )
    seeded.database.engine.dispose()

    rebuilt = Database(f"sqlite:///{path}")
    rebuilt_repo = PmRecoveryRepo(rebuilt)
    stored_original = rebuilt_repo.get_episode(original.id)
    stored_blocker = rebuilt_repo.get_blocker(observed.blocker.id)
    assert stored_original is not None and stored_original.closed_at is None
    assert stored_blocker is not None and stored_blocker.superseded_at is None
    assert rebuilt_repo.sequence_high_water(PRODUCT_ID) == 2
    with rebuilt.engine.begin() as connection:
        connection.execute(sa.text("DROP TRIGGER fail_pm_replacement_insert"))
    replacement = rebuilt_repo.replace_episode(
        expected_episode_id=original.id,
        replacement=replacement_identity,
        closure_event_id="replacement-event",
        closure_kind=PmRecoveryEpisodeClosureKind.PUBLICATION_REPLACEMENT,
        replaced_at=NOW + timedelta(seconds=2),
    )
    with pytest.raises(PmRecoveryStorageError) as contradiction:
        rebuilt_repo.replace_episode(
            expected_episode_id=original.id,
            replacement=replacement_identity,
            closure_event_id="different-event",
            closure_kind=PmRecoveryEpisodeClosureKind.PUBLICATION_REPLACEMENT,
            replaced_at=NOW + timedelta(seconds=2),
        )
    assert replacement.episode.episode_created_sequence == 3
    assert contradiction.value.code is PmRecoveryStorageCode.EPISODE_CLOSURE_CONFLICT
    rebuilt.engine.dispose()


def test_starvation_state_projects_deterministically_into_health_after_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "starvation.db"
    seeded = _seed_store(path)
    repo = PmRecoveryRepo(seeded.database)
    episode = repo.establish_episode(_identity(seeded.candidate), created_at=NOW)
    result = repo.record_evaluation(
        episode_id=episode.id,
        expected_cursor_sequence=episode.fairness_cursor,
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
    path = tmp_path / "transaction.db"
    seeded = _seed_store(path)
    with seeded.database.engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TRIGGER fail_pm_episode_insert "
                "BEFORE INSERT ON pm_recovery_episodes "
                "BEGIN SELECT RAISE(ABORT, 'seeded episode failure'); END"
            )
        )

    seeded.database.engine.dispose()
    built: list[tuple[RecoveryGeneration, Database, PmRecoveryRepo]] = []
    with TemporalHarness(db_path=path, initial_time=NOW) as harness:
        _register_recovery_generations(harness, built)
        with (
            harness.new_generation() as generation,
            pytest.raises(sa.exc.IntegrityError),
        ):
            _resource(generation).repository.establish_episode(
                _identity(seeded.candidate), created_at=NOW
            )
        with harness.new_generation() as generation:
            repo = _resource(generation).repository
            assert repo.sequence_high_water(PRODUCT_ID) == 0
            assert repo.get_episode(_identity(seeded.candidate).episode_id) is None
            with _resource(generation).database.engine.begin() as connection:
                connection.execute(sa.text("DROP TRIGGER fail_pm_episode_insert"))
        with harness.new_generation() as generation:
            episode = _resource(generation).repository.establish_episode(
                _identity(seeded.candidate), created_at=NOW
            )
    assert episode.episode_created_sequence == 1


def test_evaluation_cursor_and_blocker_roll_back_together_on_late_failure(
    tmp_path: Path,
) -> None:
    path = tmp_path / "evaluation-transaction.db"
    seeded = _seed_store(path)
    repo = PmRecoveryRepo(seeded.database)
    identity = _identity(seeded.candidate)
    episode = repo.establish_episode(identity, created_at=NOW)
    with seeded.database.engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TRIGGER fail_pm_blocker_insert "
                "BEFORE INSERT ON pm_blocker_occurrences "
                "BEGIN SELECT RAISE(ABORT, 'seeded blocker failure'); END"
            )
        )

    seeded.database.engine.dispose()
    built: list[tuple[RecoveryGeneration, Database, PmRecoveryRepo]] = []
    with TemporalHarness(db_path=path, initial_time=NOW) as harness:
        _register_recovery_generations(harness, built)
        with (
            harness.new_generation() as generation,
            pytest.raises(sa.exc.IntegrityError),
        ):
            _resource(generation).repository.record_evaluation(
                episode_id=episode.id,
                expected_cursor_sequence=episode.fairness_cursor,
                evaluation_id="evaluation-1",
                evaluated_at=NOW + timedelta(seconds=1),
                blocker=_blocker(),
            )
        with harness.new_generation() as generation:
            repo = _resource(generation).repository
            rolled_back = repo.get_episode(identity.episode_id)
            assert rolled_back is not None
            assert rolled_back.last_evaluated_sequence is None
            assert repo.sequence_high_water(PRODUCT_ID) == 1
            assert repo.list_blockers(product_id=PRODUCT_ID) == []
            with _resource(generation).database.engine.begin() as connection:
                connection.execute(sa.text("DROP TRIGGER fail_pm_blocker_insert"))
        with harness.new_generation() as generation:
            committed = _resource(generation).repository.record_evaluation(
                episode_id=episode.id,
                expected_cursor_sequence=episode.fairness_cursor,
                evaluation_id="evaluation-1",
                evaluated_at=NOW + timedelta(seconds=1),
                blocker=_blocker(),
            )
    assert committed.episode.last_evaluated_sequence == 2
    assert committed.blocker is not None


def test_closed_episode_is_durable_and_cannot_be_evaluated(tmp_path: Path) -> None:
    path = tmp_path / "closure.db"
    seeded = _seed_store(path)
    repo = PmRecoveryRepo(seeded.database)
    identity = _identity(seeded.candidate)
    episode = repo.establish_episode(identity, created_at=NOW)
    observed = repo.record_evaluation(
        episode_id=episode.id,
        expected_cursor_sequence=episode.fairness_cursor,
        evaluation_id="evaluation-before-close",
        evaluated_at=NOW + timedelta(seconds=1),
        blocker=_blocker(),
    )
    assert observed.blocker is not None
    close = repo.close_episode(
        episode_id=identity.episode_id,
        closure_event_id="publication-replaced-1",
        closure_kind=PmRecoveryEpisodeClosureKind.PUBLICATION_REPLACEMENT,
        closed_at=NOW + timedelta(seconds=2),
    )
    seeded.database.engine.dispose()
    rebuilt = Database(f"sqlite:///{path}")
    repo = PmRecoveryRepo(rebuilt)
    replay = repo.close_episode(
        episode_id=identity.episode_id,
        closure_event_id="publication-replaced-1",
        closure_kind=PmRecoveryEpisodeClosureKind.PUBLICATION_REPLACEMENT,
        closed_at=NOW + timedelta(seconds=2),
    )
    with pytest.raises(PmRecoveryStorageError) as raised:
        repo.record_evaluation(
            episode_id=identity.episode_id,
            expected_cursor_sequence=2,
            evaluation_id="too-late",
            evaluated_at=NOW + timedelta(seconds=2),
        )

    assert close.changed is True
    assert replay.changed is False
    assert raised.value.code is PmRecoveryStorageCode.EPISODE_CLOSED
    assert repo.list_active_episodes_ordered(PRODUCT_ID) == []
    assert repo.list_blockers(product_id=PRODUCT_ID, active_only=True) == []
    retained = repo.get_blocker(observed.blocker.id)
    assert retained is not None
    assert retained.superseded_by_event_id == "publication-replaced-1"
    rebuilt.engine.dispose()


def test_concurrent_contradictory_closures_have_one_winner(tmp_path: Path) -> None:
    path = tmp_path / "concurrent-close.db"
    seeded = _seed_store(path)
    episode = PmRecoveryRepo(seeded.database).establish_episode(
        _identity(seeded.candidate), created_at=NOW
    )
    seeded.database.engine.dispose()
    barrier = Barrier(2)

    def close(event_id: str) -> tuple[str, object]:
        database = Database(f"sqlite:///{path}")
        try:
            barrier.wait()
            try:
                result = PmRecoveryRepo(database).close_episode(
                    episode_id=episode.id,
                    closure_event_id=event_id,
                    closure_kind=(PmRecoveryEpisodeClosureKind.PUBLICATION_REPLACEMENT),
                    closed_at=NOW + timedelta(seconds=1),
                )
                return ("changed", result.changed)
            except PmRecoveryStorageError as error:
                return ("error", error.code)
        finally:
            database.engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(close, ("closure-a", "closure-b")))

    assert outcomes.count(("changed", True)) == 1
    assert (
        outcomes.count(("error", PmRecoveryStorageCode.EPISODE_CLOSURE_CONFLICT)) == 1
    )


def test_concurrent_close_and_evaluation_cannot_leave_an_active_blocker(
    tmp_path: Path,
) -> None:
    path = tmp_path / "close-versus-evaluation.db"
    seeded = _seed_store(path)
    episode = PmRecoveryRepo(seeded.database).establish_episode(
        _identity(seeded.candidate), created_at=NOW
    )
    seeded.database.engine.dispose()
    barrier = Barrier(2)

    def close() -> tuple[str, object]:
        database = Database(f"sqlite:///{path}")
        try:
            barrier.wait()
            result = PmRecoveryRepo(database).close_episode(
                episode_id=episode.id,
                closure_event_id="close-race",
                closure_kind=PmRecoveryEpisodeClosureKind.PUBLICATION_REPLACEMENT,
                closed_at=NOW + timedelta(seconds=2),
            )
            return ("close", result.changed)
        finally:
            database.engine.dispose()

    def evaluate() -> tuple[str, object]:
        database = Database(f"sqlite:///{path}")
        try:
            barrier.wait()
            try:
                result = PmRecoveryRepo(database).record_evaluation(
                    episode_id=episode.id,
                    expected_cursor_sequence=episode.fairness_cursor,
                    evaluation_id="evaluation-race",
                    evaluated_at=NOW + timedelta(seconds=1),
                    blocker=_blocker(),
                )
                return ("evaluation", result.changed)
            except PmRecoveryStorageError as error:
                return ("error", error.code)
        finally:
            database.engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as pool:
        close_future = pool.submit(close)
        evaluation_future = pool.submit(evaluate)
        outcomes = (close_future.result(), evaluation_future.result())

    assert ("close", True) in outcomes
    assert outcomes[1] in {
        ("evaluation", True),
        ("error", PmRecoveryStorageCode.EPISODE_CLOSED),
    }
    rebuilt = Database(f"sqlite:///{path}")
    repo = PmRecoveryRepo(rebuilt)
    stored = repo.get_episode(episode.id)
    assert stored is not None and stored.closed_at is not None
    assert repo.list_blockers(product_id=PRODUCT_ID, active_only=True) == []
    rebuilt.engine.dispose()


def test_sequence_maximum_and_exhaustion_are_typed_and_atomic(tmp_path: Path) -> None:
    seeded = _seed_store(tmp_path / "sequence-exhaustion.db")
    repo = PmRecoveryRepo(seeded.database)
    episode = repo.establish_episode(_identity(seeded.candidate), created_at=NOW)
    maximum = 9_223_372_036_854_775_807
    with seeded.database.engine.begin() as connection:
        connection.execute(
            sa.update(PmRecoverySequenceCounterRow)
            .where(PmRecoverySequenceCounterRow.product_id == PRODUCT_ID)
            .values(high_water=maximum - 1)
        )
    final = repo.record_evaluation(
        episode_id=episode.id,
        expected_cursor_sequence=episode.fairness_cursor,
        evaluation_id="final-sequence",
        evaluated_at=NOW + timedelta(seconds=1),
    )
    with pytest.raises(PmRecoveryStorageError) as exhausted:
        repo.record_evaluation(
            episode_id=episode.id,
            expected_cursor_sequence=maximum,
            evaluation_id="past-maximum",
            evaluated_at=NOW + timedelta(seconds=2),
        )

    assert final.episode.last_evaluated_sequence == maximum
    assert exhausted.value.code is PmRecoveryStorageCode.SEQUENCE_EXHAUSTED
    assert repo.sequence_high_water(PRODUCT_ID) == maximum
    stored = repo.get_episode(episode.id)
    assert stored is not None
    assert stored.last_evaluation_id == "final-sequence"


def test_sqlite_constraints_bound_episode_and_starvation_rows(tmp_path: Path) -> None:
    seeded = _seed_store(tmp_path / "constraints.db")
    repo = PmRecoveryRepo(seeded.database)
    episode = repo.establish_episode(_identity(seeded.candidate), created_at=NOW)
    evaluated = repo.record_evaluation(
        episode_id=episode.id,
        expected_cursor_sequence=episode.fairness_cursor,
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
    with (
        pytest.raises(sa.exc.IntegrityError),
        seeded.database.engine.begin() as connection,
    ):
        connection.execute(
            sa.update(PmRecoveryEpisodeRow)
            .where(PmRecoveryEpisodeRow.id == episode.id)
            .values(identity_fingerprint="short")
        )
    with (
        pytest.raises(sa.exc.IntegrityError),
        seeded.database.engine.begin() as connection,
    ):
        connection.execute(
            sa.update(PmBlockerOccurrenceRow)
            .where(PmBlockerOccurrenceRow.id == evaluated.blocker.id)
            .values(code="RuntimeError: secret token")
        )
    for episode_values in (
        {"active_scope_fingerprint": None},
        {"candidate_ticket_key": None},
        {"last_evaluation_id": None},
        {
            "closed_at": NOW + timedelta(seconds=2),
            "active_scope_fingerprint": None,
        },
    ):
        with (
            pytest.raises(sa.exc.IntegrityError),
            seeded.database.engine.begin() as connection,
        ):
            connection.execute(
                sa.update(PmRecoveryEpisodeRow)
                .where(PmRecoveryEpisodeRow.id == episode.id)
                .values(**episode_values)
            )
    for blocker_values in (
        {"active_fingerprint": None},
        {"policy_revision": None},
        {"candidate_ticket_key": None},
    ):
        with (
            pytest.raises(sa.exc.IntegrityError),
            seeded.database.engine.begin() as connection,
        ):
            connection.execute(
                sa.update(PmBlockerOccurrenceRow)
                .where(PmBlockerOccurrenceRow.id == evaluated.blocker.id)
                .values(**blocker_values)
            )

    replacement = repo.replace_episode(
        expected_episode_id=episode.id,
        replacement=_identity(
            seeded.candidate, authoritative_episode_id="constraint-replacement"
        ),
        closure_event_id="constraint-replacement-event",
        closure_kind=PmRecoveryEpisodeClosureKind.PUBLICATION_REPLACEMENT,
        replaced_at=NOW + timedelta(seconds=2),
    )
    with (
        pytest.raises(sa.exc.IntegrityError),
        seeded.database.engine.begin() as connection,
    ):
        connection.execute(
            sa.update(PmRecoveryEpisodeRow)
            .where(PmRecoveryEpisodeRow.id == replacement.episode.id)
            .values(replacement_event_id=None)
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

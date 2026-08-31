"""Transactional persistence for dormant PM recovery and blocker state."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid5

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from atlas.core.models.pm_recovery import (
    MAX_PM_RECURRENCE_COUNT,
    DurablePmBlocker,
    PmBlockerIdentity,
    PmBlockerObservationIntent,
    PmBlockerSupersessionKind,
    PmRecoveryEpisode,
    PmRecoveryEpisodeClosureKind,
    PmRecoveryEpisodeIdentity,
    PmStarvedCandidate,
)
from atlas.storage.db import Database
from atlas.storage.tables import (
    PmBlockerOccurrenceRow,
    PmBlockerStarvedCandidateRow,
    PmRecoveryEpisodeRow,
    PmRecoverySequenceCounterRow,
    TicketRow,
)

PM_BLOCKER_OCCURRENCE_NAMESPACE = UUID("ce4b5ab1-4dd4-46f3-92b6-46792911031c")


class PmRecoveryStorageCode(StrEnum):
    """Bounded fail-closed outcomes from the recovery repository."""

    EPISODE_IDENTITY_CONFLICT = "episode_identity_conflict"
    EPISODE_NOT_FOUND = "episode_not_found"
    EPISODE_CLOSED = "episode_closed"
    EPISODE_CLOSURE_CONFLICT = "episode_closure_conflict"
    EPISODE_ACTIVE_SCOPE_CONFLICT = "episode_active_scope_conflict"
    CANDIDATE_IDENTITY_CONFLICT = "candidate_identity_conflict"
    EVALUATION_REPLAY_CONFLICT = "evaluation_replay_conflict"
    EVALUATION_CURSOR_CONFLICT = "evaluation_cursor_conflict"
    EVALUATION_OUT_OF_ORDER = "evaluation_out_of_order"
    BLOCKER_IDENTITY_CONFLICT = "blocker_identity_conflict"
    BLOCKER_NOT_FOUND = "blocker_not_found"
    BLOCKER_SUPERSESSION_CONFLICT = "blocker_supersession_conflict"
    SEQUENCE_EXHAUSTED = "sequence_exhausted"
    UNSUPPORTED_DIALECT = "unsupported_dialect"


class PmRecoveryStorageError(RuntimeError):
    def __init__(self, code: PmRecoveryStorageCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class PmRecoveryEvaluationRecord:
    """Result of one atomic evaluation cursor and optional blocker write."""

    episode: PmRecoveryEpisode
    blocker: DurablePmBlocker | None
    changed: bool


@dataclass(frozen=True)
class PmRecoveryMutationRecord:
    """Idempotent mutation result for closure or supersession."""

    changed: bool


@dataclass(frozen=True)
class PmRecoveryReplacementRecord:
    """Result of one atomic authoritative episode replacement."""

    episode: PmRecoveryEpisode
    changed: bool


def _aware(value: datetime, *, name: str) -> datetime:
    if value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _bounded_identifier(value: str, *, name: str) -> str:
    if not value or value != value.strip() or len(value) > 128:
        raise ValueError(f"{name} must be a non-empty identifier of at most 128 chars")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{name} must not contain control characters")
    return value


def _episode_model(row: PmRecoveryEpisodeRow) -> PmRecoveryEpisode:
    return PmRecoveryEpisode.model_validate(row, from_attributes=True)


def _row_values(row: PmBlockerOccurrenceRow) -> dict[str, object]:
    return {
        column.name: getattr(row, column.name)
        for column in PmBlockerOccurrenceRow.__table__.columns
    }


def _blocker_model(session: Session, row: PmBlockerOccurrenceRow) -> DurablePmBlocker:
    starved = [
        PmStarvedCandidate.model_validate(item, from_attributes=True)
        for item in session.scalars(
            sa.select(PmBlockerStarvedCandidateRow)
            .where(PmBlockerStarvedCandidateRow.blocker_occurrence_id == row.id)
            .order_by(PmBlockerStarvedCandidateRow.ordinal)
        )
    ]
    values = _row_values(row)
    values["starved_candidates"] = tuple(starved)
    return DurablePmBlocker.model_validate(values)


def _episode_identity_matches(
    episode: PmRecoveryEpisode,
    identity: PmRecoveryEpisodeIdentity,
) -> bool:
    fields = (
        "schema_version",
        "product_id",
        "operation",
        "authority_id",
        "authoritative_episode_id",
        "candidate_ticket_id",
        "candidate_ticket_key",
    )
    return bool(
        all(getattr(episode, field) == getattr(identity, field) for field in fields)
        and episode.id == identity.episode_id
        and episode.identity_fingerprint == identity.computed_identity_fingerprint
    )


def _evaluation_fingerprint(
    *,
    episode_id: UUID,
    expected_cursor_sequence: int,
    evaluation_id: str,
    evaluated_at: datetime,
    blocker: PmBlockerObservationIntent | None,
    relieve_starvation_for_candidate: bool,
    supersede_prior_blockers_for_episode: bool,
) -> str:
    payload = {
        "episode_id": str(episode_id),
        "expected_cursor_sequence": expected_cursor_sequence,
        "blocker": None if blocker is None else blocker.model_dump(mode="json"),
        "evaluated_at": evaluated_at.isoformat(),
        "evaluation_id": evaluation_id,
        "relieve_starvation_for_candidate": relieve_starvation_for_candidate,
        "supersede_prior_blockers_for_episode": (supersede_prior_blockers_for_episode),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _blocker_identity(
    episode: PmRecoveryEpisodeRow,
    observation: PmBlockerObservationIntent,
) -> PmBlockerIdentity:
    return PmBlockerIdentity(
        product_id=episode.product_id,
        operation=episode.operation,
        code=observation.code,
        kind=observation.kind,
        authority_kind=observation.authority_kind,
        authority_id=observation.authority_id,
        recovery_episode_id=episode.id,
        candidate_ticket_id=episode.candidate_ticket_id,
        candidate_ticket_key=episode.candidate_ticket_key,
    )


def _episode_scope_fingerprint(identity: PmRecoveryEpisodeIdentity) -> str:
    payload = {
        "authority_id": identity.authority_id,
        "candidate_ticket_id": (
            None
            if identity.candidate_ticket_id is None
            else str(identity.candidate_ticket_id)
        ),
        "candidate_ticket_key": identity.candidate_ticket_key,
        "operation": identity.operation,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class PmRecoveryRepo:
    """Own recurrence, replay, supersession, and product-global fairness state.

    This repository exposes dormant storage capabilities only.  It performs no
    candidate selection, provider request, workflow mutation, or scheduler work.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def _validate_ticket_identity(
        self,
        session: Session,
        *,
        product_id: UUID,
        ticket_id: UUID | None,
        ticket_key: str | None,
    ) -> None:
        if ticket_id is None and ticket_key is None:
            return
        if ticket_id is None or ticket_key is None:
            raise PmRecoveryStorageError(
                PmRecoveryStorageCode.CANDIDATE_IDENTITY_CONFLICT
            )
        row = session.get(TicketRow, ticket_id)
        if row is None or row.product_id != product_id or row.key != ticket_key:
            raise PmRecoveryStorageError(
                PmRecoveryStorageCode.CANDIDATE_IDENTITY_CONFLICT
            )

    def _lock_sequence_counter(self, session: Session, product_id: UUID) -> None:
        bind = session.get_bind()
        dialect = bind.dialect.name
        if dialect == "sqlite":
            sqlite_statement = sqlite_insert(PmRecoverySequenceCounterRow).values(
                product_id=product_id, high_water=0
            )
            session.execute(
                sqlite_statement.on_conflict_do_nothing(index_elements=["product_id"])
            )
        elif dialect == "postgresql":
            postgres_statement = postgresql_insert(PmRecoverySequenceCounterRow).values(
                product_id=product_id, high_water=0
            )
            session.execute(
                postgres_statement.on_conflict_do_nothing(index_elements=["product_id"])
            )
        else:  # pragma: no cover - Atlas supports SQLite and PostgreSQL
            raise PmRecoveryStorageError(PmRecoveryStorageCode.UNSUPPORTED_DIALECT)

        locked = session.execute(
            sa.update(PmRecoverySequenceCounterRow)
            .where(PmRecoverySequenceCounterRow.product_id == product_id)
            .values(high_water=PmRecoverySequenceCounterRow.high_water)
        )
        if getattr(locked, "rowcount", 0) != 1:
            raise PmRecoveryStorageError(PmRecoveryStorageCode.SEQUENCE_EXHAUSTED)

    def _allocate_locked_sequence(self, session: Session, product_id: UUID) -> int:
        value = session.scalar(
            sa.update(PmRecoverySequenceCounterRow)
            .where(
                PmRecoverySequenceCounterRow.product_id == product_id,
                PmRecoverySequenceCounterRow.high_water < 9_223_372_036_854_775_807,
            )
            .values(high_water=PmRecoverySequenceCounterRow.high_water + 1)
            .returning(PmRecoverySequenceCounterRow.high_water)
        )
        if value is None:
            raise PmRecoveryStorageError(PmRecoveryStorageCode.SEQUENCE_EXHAUSTED)
        return value

    def sequence_high_water(self, product_id: UUID) -> int:
        """Return the durable product counter for diagnostics and tests."""

        with self._db.session() as session:
            value = session.scalar(
                sa.select(PmRecoverySequenceCounterRow.high_water).where(
                    PmRecoverySequenceCounterRow.product_id == product_id
                )
            )
            return 0 if value is None else value

    def get_episode(self, episode_id: UUID) -> PmRecoveryEpisode | None:
        with self._db.session() as session:
            row = session.get(PmRecoveryEpisodeRow, episode_id)
            return None if row is None else _episode_model(row)

    def establish_episode(
        self,
        identity: PmRecoveryEpisodeIdentity,
        *,
        created_at: datetime,
    ) -> PmRecoveryEpisode:
        """Atomically allocate and persist a new episode, or replay it exactly."""

        identity = PmRecoveryEpisodeIdentity.model_validate(
            identity.model_dump(mode="python")
        )
        created = _aware(created_at, name="episode created_at")
        active_scope_fingerprint = _episode_scope_fingerprint(identity)
        try:
            with self._db.session() as session, session.begin():
                self._lock_sequence_counter(session, identity.product_id)
                existing_row = session.get(PmRecoveryEpisodeRow, identity.episode_id)
                if existing_row is not None:
                    existing = _episode_model(existing_row)
                    if _episode_identity_matches(existing, identity):
                        return existing
                    raise PmRecoveryStorageError(
                        PmRecoveryStorageCode.EPISODE_IDENTITY_CONFLICT
                    )
                active_scope = session.scalar(
                    sa.select(PmRecoveryEpisodeRow).where(
                        PmRecoveryEpisodeRow.product_id == identity.product_id,
                        PmRecoveryEpisodeRow.active_scope_fingerprint
                        == active_scope_fingerprint,
                    )
                )
                if active_scope is not None:
                    raise PmRecoveryStorageError(
                        PmRecoveryStorageCode.EPISODE_ACTIVE_SCOPE_CONFLICT
                    )
                self._validate_ticket_identity(
                    session,
                    product_id=identity.product_id,
                    ticket_id=identity.candidate_ticket_id,
                    ticket_key=identity.candidate_ticket_key,
                )
                sequence = self._allocate_locked_sequence(session, identity.product_id)
                session.add(
                    PmRecoveryEpisodeRow(
                        **identity.model_dump(mode="python"),
                        id=identity.episode_id,
                        identity_fingerprint=identity.computed_identity_fingerprint,
                        active_scope_fingerprint=active_scope_fingerprint,
                        episode_created_sequence=sequence,
                        last_evaluated_sequence=None,
                        last_evaluation_id=None,
                        last_evaluation_fingerprint=None,
                        created_at=created,
                        last_evaluated_at=None,
                        closed_at=None,
                        closure_event_id=None,
                        closure_kind=None,
                        replaces_episode_id=None,
                        replacement_event_id=None,
                    )
                )
                session.flush()
        except sa.exc.IntegrityError:
            replay = self.get_episode(identity.episode_id)
            if replay is not None and _episode_identity_matches(replay, identity):
                return replay
            if replay is not None:
                raise PmRecoveryStorageError(
                    PmRecoveryStorageCode.EPISODE_IDENTITY_CONFLICT
                ) from None
            raise
        result = self.get_episode(identity.episode_id)
        if result is None:  # pragma: no cover - committed insert invariant
            raise PmRecoveryStorageError(PmRecoveryStorageCode.EPISODE_NOT_FOUND)
        return result

    def close_episode(
        self,
        *,
        episode_id: UUID,
        closure_event_id: str,
        closure_kind: PmRecoveryEpisodeClosureKind,
        closed_at: datetime,
    ) -> PmRecoveryMutationRecord:
        """Close one exact episode without deleting its fairness history."""

        closed = _aware(closed_at, name="episode closed_at")
        closure_event_id = _bounded_identifier(
            closure_event_id, name="closure_event_id"
        )
        current = self.get_episode(episode_id)
        if current is None:
            raise PmRecoveryStorageError(PmRecoveryStorageCode.EPISODE_NOT_FOUND)
        with self._db.session() as session, session.begin():
            self._lock_sequence_counter(session, current.product_id)
            row = session.get(PmRecoveryEpisodeRow, episode_id)
            if row is None:
                raise PmRecoveryStorageError(PmRecoveryStorageCode.EPISODE_NOT_FOUND)
            if row.closed_at is not None:
                if (
                    row.closed_at == closed
                    and row.closure_event_id == closure_event_id
                    and row.closure_kind == closure_kind.value
                ):
                    return PmRecoveryMutationRecord(changed=False)
                raise PmRecoveryStorageError(
                    PmRecoveryStorageCode.EPISODE_CLOSURE_CONFLICT
                )
            if closed < row.created_at or (
                row.last_evaluated_at is not None and closed < row.last_evaluated_at
            ):
                raise PmRecoveryStorageError(
                    PmRecoveryStorageCode.EPISODE_CLOSURE_CONFLICT
                )
            row.closed_at = closed
            row.closure_event_id = closure_event_id
            row.closure_kind = closure_kind.value
            row.active_scope_fingerprint = None
            for blocker in session.scalars(
                sa.select(PmBlockerOccurrenceRow).where(
                    PmBlockerOccurrenceRow.recovery_episode_id == row.id,
                    PmBlockerOccurrenceRow.active_fingerprint.is_not(None),
                )
            ):
                blocker.active_fingerprint = None
                blocker.superseded_at = closed
                blocker.superseded_by_event_id = closure_event_id
                blocker.supersession_kind = PmBlockerSupersessionKind.RECOVERY.value
            return PmRecoveryMutationRecord(changed=True)

    def replace_episode(
        self,
        *,
        expected_episode_id: UUID,
        replacement: PmRecoveryEpisodeIdentity,
        closure_event_id: str,
        closure_kind: PmRecoveryEpisodeClosureKind,
        replaced_at: datetime,
    ) -> PmRecoveryReplacementRecord:
        """Atomically retire one scope and create its authoritative replacement."""

        replacement = PmRecoveryEpisodeIdentity.model_validate(
            replacement.model_dump(mode="python")
        )
        replaced = _aware(replaced_at, name="episode replaced_at")
        replacement_scope_fingerprint = _episode_scope_fingerprint(replacement)
        closure_event_id = _bounded_identifier(
            closure_event_id, name="closure_event_id"
        )
        with self._db.session() as session, session.begin():
            self._lock_sequence_counter(session, replacement.product_id)
            old = session.get(PmRecoveryEpisodeRow, expected_episode_id)
            if old is None:
                raise PmRecoveryStorageError(PmRecoveryStorageCode.EPISODE_NOT_FOUND)
            if old.product_id != replacement.product_id:
                raise PmRecoveryStorageError(
                    PmRecoveryStorageCode.EPISODE_IDENTITY_CONFLICT
                )
            existing = session.get(PmRecoveryEpisodeRow, replacement.episode_id)
            if existing is not None:
                existing_model = _episode_model(existing)
                if (
                    not _episode_identity_matches(existing_model, replacement)
                    or old.closed_at != replaced
                    or old.closure_event_id != closure_event_id
                    or old.closure_kind != closure_kind.value
                    or existing.replaces_episode_id != old.id
                    or existing.replacement_event_id != closure_event_id
                ):
                    raise PmRecoveryStorageError(
                        PmRecoveryStorageCode.EPISODE_CLOSURE_CONFLICT
                    )
                return PmRecoveryReplacementRecord(
                    episode=existing_model, changed=False
                )
            if old.closed_at is not None:
                raise PmRecoveryStorageError(
                    PmRecoveryStorageCode.EPISODE_CLOSURE_CONFLICT
                )
            if (
                old.operation != replacement.operation
                or old.authority_id != replacement.authority_id
                or old.candidate_ticket_id != replacement.candidate_ticket_id
                or old.candidate_ticket_key != replacement.candidate_ticket_key
                or old.active_scope_fingerprint != replacement_scope_fingerprint
            ):
                raise PmRecoveryStorageError(
                    PmRecoveryStorageCode.EPISODE_ACTIVE_SCOPE_CONFLICT
                )
            if replaced < old.created_at or (
                old.last_evaluated_at is not None and replaced < old.last_evaluated_at
            ):
                raise PmRecoveryStorageError(
                    PmRecoveryStorageCode.EPISODE_CLOSURE_CONFLICT
                )
            self._validate_ticket_identity(
                session,
                product_id=replacement.product_id,
                ticket_id=replacement.candidate_ticket_id,
                ticket_key=replacement.candidate_ticket_key,
            )
            old.closed_at = replaced
            old.closure_event_id = closure_event_id
            old.closure_kind = closure_kind.value
            old.active_scope_fingerprint = None
            for blocker in session.scalars(
                sa.select(PmBlockerOccurrenceRow).where(
                    PmBlockerOccurrenceRow.recovery_episode_id == old.id,
                    PmBlockerOccurrenceRow.active_fingerprint.is_not(None),
                )
            ):
                blocker.active_fingerprint = None
                blocker.superseded_at = replaced
                blocker.superseded_by_event_id = closure_event_id
                blocker.supersession_kind = PmBlockerSupersessionKind.RECOVERY.value
            sequence = self._allocate_locked_sequence(session, replacement.product_id)
            session.add(
                PmRecoveryEpisodeRow(
                    **replacement.model_dump(mode="python"),
                    id=replacement.episode_id,
                    identity_fingerprint=(replacement.computed_identity_fingerprint),
                    active_scope_fingerprint=replacement_scope_fingerprint,
                    episode_created_sequence=sequence,
                    last_evaluated_sequence=None,
                    last_evaluation_id=None,
                    last_evaluation_fingerprint=None,
                    created_at=replaced,
                    last_evaluated_at=None,
                    closed_at=None,
                    closure_event_id=None,
                    closure_kind=None,
                    replaces_episode_id=old.id,
                    replacement_event_id=closure_event_id,
                )
            )
            session.flush()
        stored = self.get_episode(replacement.episode_id)
        if stored is None:  # pragma: no cover - committed insert invariant
            raise PmRecoveryStorageError(PmRecoveryStorageCode.EPISODE_NOT_FOUND)
        return PmRecoveryReplacementRecord(episode=stored, changed=True)

    def _replace_starved_candidates(
        self,
        session: Session,
        *,
        blocker_id: UUID,
        observation: PmBlockerObservationIntent,
        observed_at: datetime,
    ) -> None:
        existing = {
            row.ticket_id: row.started_at
            for row in session.scalars(
                sa.select(PmBlockerStarvedCandidateRow).where(
                    PmBlockerStarvedCandidateRow.blocker_occurrence_id == blocker_id
                )
            )
        }
        session.execute(
            sa.delete(PmBlockerStarvedCandidateRow).where(
                PmBlockerStarvedCandidateRow.blocker_occurrence_id == blocker_id
            )
        )
        session.flush()
        for ordinal, candidate in enumerate(observation.starved_candidates, start=1):
            session.add(
                PmBlockerStarvedCandidateRow(
                    blocker_occurrence_id=blocker_id,
                    ordinal=ordinal,
                    ticket_id=candidate.ticket_id,
                    ticket_key=candidate.ticket_key,
                    started_at=existing.get(candidate.ticket_id, observed_at),
                )
            )

    def _observe_blocker(
        self,
        session: Session,
        *,
        episode: PmRecoveryEpisodeRow,
        evaluation_sequence: int,
        evaluation_id: str,
        evaluated_at: datetime,
        observation: PmBlockerObservationIntent,
    ) -> UUID:
        identity = _blocker_identity(episode, observation)
        fingerprint = identity.fingerprint
        row = session.scalar(
            sa.select(PmBlockerOccurrenceRow).where(
                PmBlockerOccurrenceRow.product_id == episode.product_id,
                PmBlockerOccurrenceRow.active_fingerprint == fingerprint,
            )
        )
        if row is None:
            occurrence_id = uuid5(
                PM_BLOCKER_OCCURRENCE_NAMESPACE,
                f"{episode.product_id}:{fingerprint}:{evaluation_sequence}",
            )
            row = PmBlockerOccurrenceRow(
                **identity.model_dump(mode="python"),
                id=occurrence_id,
                blocker_fingerprint=fingerprint,
                active_fingerprint=fingerprint,
                first_evaluation_id=evaluation_id,
                latest_evaluation_id=evaluation_id,
                first_observed_at=evaluated_at,
                latest_observed_at=evaluated_at,
                consecutive_observations=1,
                next_safe_retry_at=observation.next_safe_retry_at,
                capacity_impact=observation.capacity_impact,
                starved_candidates_truncated=(observation.starved_candidates_truncated),
                policy_namespace=observation.policy_namespace,
                policy_revision=observation.policy_revision,
                policy_fingerprint=observation.policy_fingerprint,
                superseded_at=None,
                superseded_by_event_id=None,
                supersession_kind=None,
            )
            session.add(row)
            session.flush()
        else:
            current = _blocker_model(session, row)
            immutable_fields = (
                "schema_version",
                "product_id",
                "operation",
                "code",
                "kind",
                "authority_kind",
                "authority_id",
                "recovery_episode_id",
                "candidate_ticket_id",
                "candidate_ticket_key",
            )
            if any(
                getattr(current, field) != getattr(identity, field)
                for field in immutable_fields
            ):
                raise PmRecoveryStorageError(
                    PmRecoveryStorageCode.BLOCKER_IDENTITY_CONFLICT
                )
            if evaluated_at <= row.latest_observed_at:
                raise PmRecoveryStorageError(
                    PmRecoveryStorageCode.EVALUATION_OUT_OF_ORDER
                )
            row.latest_evaluation_id = evaluation_id
            row.latest_observed_at = evaluated_at
            row.consecutive_observations = min(
                row.consecutive_observations + 1, MAX_PM_RECURRENCE_COUNT
            )
            row.next_safe_retry_at = observation.next_safe_retry_at
            row.capacity_impact = observation.capacity_impact
            row.starved_candidates_truncated = observation.starved_candidates_truncated
            row.policy_namespace = observation.policy_namespace
            row.policy_revision = observation.policy_revision
            row.policy_fingerprint = observation.policy_fingerprint

        for candidate in observation.starved_candidates:
            self._validate_ticket_identity(
                session,
                product_id=episode.product_id,
                ticket_id=candidate.ticket_id,
                ticket_key=candidate.ticket_key,
            )
        self._replace_starved_candidates(
            session,
            blocker_id=row.id,
            observation=observation,
            observed_at=evaluated_at,
        )
        session.flush()
        return row.id

    def record_evaluation(
        self,
        *,
        episode_id: UUID,
        expected_cursor_sequence: int,
        evaluation_id: str,
        evaluated_at: datetime,
        blocker: PmBlockerObservationIntent | None = None,
        relieve_starvation_for_candidate: bool = False,
        supersede_prior_blockers_for_episode: bool = False,
    ) -> PmRecoveryEvaluationRecord:
        """Move one episode to the product tail and record its blocker atomically."""

        evaluated = _aware(evaluated_at, name="evaluation time")
        if expected_cursor_sequence < 1:
            raise ValueError("expected_cursor_sequence must be positive")
        evaluation_id = _bounded_identifier(evaluation_id, name="evaluation_id")
        if blocker is not None:
            blocker = PmBlockerObservationIntent.model_validate(
                blocker.model_dump(mode="python")
            )
        fingerprint = _evaluation_fingerprint(
            episode_id=episode_id,
            expected_cursor_sequence=expected_cursor_sequence,
            evaluation_id=evaluation_id,
            evaluated_at=evaluated,
            blocker=blocker,
            relieve_starvation_for_candidate=relieve_starvation_for_candidate,
            supersede_prior_blockers_for_episode=(supersede_prior_blockers_for_episode),
        )
        blocker_id: UUID | None = None
        current = self.get_episode(episode_id)
        if current is None:
            raise PmRecoveryStorageError(PmRecoveryStorageCode.EPISODE_NOT_FOUND)
        with self._db.session() as session, session.begin():
            self._lock_sequence_counter(session, current.product_id)
            episode = session.get(PmRecoveryEpisodeRow, episode_id)
            if episode is None:
                raise PmRecoveryStorageError(PmRecoveryStorageCode.EPISODE_NOT_FOUND)
            if episode.closed_at is not None:
                raise PmRecoveryStorageError(PmRecoveryStorageCode.EPISODE_CLOSED)
            if episode.last_evaluation_id == evaluation_id:
                if (
                    episode.last_evaluation_fingerprint != fingerprint
                    or episode.last_evaluated_at != evaluated
                ):
                    raise PmRecoveryStorageError(
                        PmRecoveryStorageCode.EVALUATION_REPLAY_CONFLICT
                    )
                replay_blockers: list[PmBlockerOccurrenceRow] = []
                if blocker is not None:
                    replay_identity = _blocker_identity(episode, blocker)
                    replay_blockers = list(
                        session.scalars(
                            sa.select(PmBlockerOccurrenceRow).where(
                                PmBlockerOccurrenceRow.recovery_episode_id
                                == episode.id,
                                PmBlockerOccurrenceRow.blocker_fingerprint
                                == replay_identity.fingerprint,
                                PmBlockerOccurrenceRow.latest_evaluation_id
                                == evaluation_id,
                                PmBlockerOccurrenceRow.latest_observed_at == evaluated,
                            )
                        )
                    )
                    if len(replay_blockers) != 1:
                        raise PmRecoveryStorageError(
                            PmRecoveryStorageCode.EVALUATION_REPLAY_CONFLICT
                        )
                return PmRecoveryEvaluationRecord(
                    episode=_episode_model(episode),
                    blocker=(
                        None
                        if not replay_blockers
                        else _blocker_model(session, replay_blockers[0])
                    ),
                    changed=False,
                )
            current_cursor = (
                episode.last_evaluated_sequence or episode.episode_created_sequence
            )
            if current_cursor != expected_cursor_sequence:
                raise PmRecoveryStorageError(
                    PmRecoveryStorageCode.EVALUATION_CURSOR_CONFLICT
                )
            if (
                episode.last_evaluated_at is not None
                and evaluated <= episode.last_evaluated_at
            ):
                raise PmRecoveryStorageError(
                    PmRecoveryStorageCode.EVALUATION_OUT_OF_ORDER
                )
            sequence = self._allocate_locked_sequence(session, episode.product_id)
            episode.last_evaluated_sequence = sequence
            episode.last_evaluation_id = evaluation_id
            episode.last_evaluation_fingerprint = fingerprint
            episode.last_evaluated_at = evaluated
            if blocker is not None:
                blocker_id = self._observe_blocker(
                    session,
                    episode=episode,
                    evaluation_sequence=sequence,
                    evaluation_id=evaluation_id,
                    evaluated_at=evaluated,
                    observation=blocker,
                )
                if supersede_prior_blockers_for_episode:
                    obsolete = list(
                        session.scalars(
                            sa.select(PmBlockerOccurrenceRow).where(
                                PmBlockerOccurrenceRow.recovery_episode_id
                                == episode.id,
                                PmBlockerOccurrenceRow.active_fingerprint.is_not(None),
                                PmBlockerOccurrenceRow.id != blocker_id,
                            )
                        )
                    )
                    for prior in obsolete:
                        prior.active_fingerprint = None
                        prior.superseded_at = evaluated
                        prior.superseded_by_event_id = evaluation_id
                        prior.supersession_kind = (
                            PmBlockerSupersessionKind.PROGRESS.value
                        )
            if (
                relieve_starvation_for_candidate
                and episode.candidate_ticket_id is not None
            ):
                # A committed exact evaluation proves this candidate is no
                # longer currently starved by an older candidate. Clear only
                # that mutable membership atomically with the cursor; retain
                # the blocker occurrence and all of its historical diagnosis.
                affected_blocker_ids = list(
                    session.scalars(
                        sa.select(PmBlockerStarvedCandidateRow.blocker_occurrence_id)
                        .join(
                            PmBlockerOccurrenceRow,
                            PmBlockerOccurrenceRow.id
                            == PmBlockerStarvedCandidateRow.blocker_occurrence_id,
                        )
                        .where(
                            PmBlockerStarvedCandidateRow.ticket_id
                            == episode.candidate_ticket_id,
                            PmBlockerOccurrenceRow.product_id == episode.product_id,
                            PmBlockerOccurrenceRow.operation == episode.operation,
                            PmBlockerOccurrenceRow.active_fingerprint.is_not(None),
                        )
                    )
                )
                session.execute(
                    sa.delete(PmBlockerStarvedCandidateRow).where(
                        PmBlockerStarvedCandidateRow.ticket_id
                        == episode.candidate_ticket_id,
                        PmBlockerStarvedCandidateRow.blocker_occurrence_id.in_(
                            sa.select(PmBlockerOccurrenceRow.id).where(
                                PmBlockerOccurrenceRow.product_id == episode.product_id,
                                PmBlockerOccurrenceRow.operation == episode.operation,
                                PmBlockerOccurrenceRow.active_fingerprint.is_not(None),
                            )
                        ),
                    )
                )
                session.flush()
                for affected_id in affected_blocker_ids:
                    remaining = session.scalar(
                        sa.select(sa.func.count())
                        .select_from(PmBlockerStarvedCandidateRow)
                        .where(
                            PmBlockerStarvedCandidateRow.blocker_occurrence_id
                            == affected_id
                        )
                    )
                    if remaining == 0:
                        affected = session.get(PmBlockerOccurrenceRow, affected_id)
                        if (
                            affected is not None
                            and not affected.starved_candidates_truncated
                        ):
                            affected.capacity_impact = False
            session.flush()

        stored_episode = self.get_episode(episode_id)
        if stored_episode is None:  # pragma: no cover - committed update invariant
            raise PmRecoveryStorageError(PmRecoveryStorageCode.EPISODE_NOT_FOUND)
        stored_blocker = None if blocker_id is None else self.get_blocker(blocker_id)
        return PmRecoveryEvaluationRecord(
            episode=stored_episode,
            blocker=stored_blocker,
            changed=True,
        )

    def get_blocker(self, blocker_id: UUID) -> DurablePmBlocker | None:
        with self._db.session() as session:
            row = session.get(PmBlockerOccurrenceRow, blocker_id)
            return None if row is None else _blocker_model(session, row)

    def supersede_blocker(
        self,
        *,
        blocker_id: UUID,
        superseded_by_event_id: str,
        supersession_kind: PmBlockerSupersessionKind,
        superseded_at: datetime,
    ) -> PmRecoveryMutationRecord:
        """Persist explicit progress/recovery; silence never clears a blocker."""

        observed = _aware(superseded_at, name="blocker superseded_at")
        superseded_by_event_id = _bounded_identifier(
            superseded_by_event_id, name="superseded_by_event_id"
        )
        current = self.get_blocker(blocker_id)
        if current is None:
            raise PmRecoveryStorageError(PmRecoveryStorageCode.BLOCKER_NOT_FOUND)
        with self._db.session() as session, session.begin():
            self._lock_sequence_counter(session, current.product_id)
            row = session.get(PmBlockerOccurrenceRow, blocker_id)
            if row is None:
                raise PmRecoveryStorageError(PmRecoveryStorageCode.BLOCKER_NOT_FOUND)
            if row.superseded_at is not None:
                if (
                    row.superseded_at == observed
                    and row.superseded_by_event_id == superseded_by_event_id
                    and row.supersession_kind == supersession_kind.value
                ):
                    return PmRecoveryMutationRecord(changed=False)
                raise PmRecoveryStorageError(
                    PmRecoveryStorageCode.BLOCKER_SUPERSESSION_CONFLICT
                )
            if row.active_fingerprint is None:
                raise PmRecoveryStorageError(
                    PmRecoveryStorageCode.BLOCKER_SUPERSESSION_CONFLICT
                )
            if observed < row.latest_observed_at:
                raise PmRecoveryStorageError(
                    PmRecoveryStorageCode.BLOCKER_SUPERSESSION_CONFLICT
                )
            row.active_fingerprint = None
            row.superseded_at = observed
            row.superseded_by_event_id = superseded_by_event_id
            row.supersession_kind = supersession_kind.value
            return PmRecoveryMutationRecord(changed=True)

    def list_blockers(
        self,
        *,
        product_id: UUID,
        active_only: bool = False,
        operation: str | None = None,
        candidate_ticket_id: UUID | None = None,
    ) -> list[DurablePmBlocker]:
        with self._db.session() as session:
            statement = sa.select(PmBlockerOccurrenceRow).where(
                PmBlockerOccurrenceRow.product_id == product_id
            )
            if active_only:
                statement = statement.where(
                    PmBlockerOccurrenceRow.active_fingerprint.is_not(None)
                )
            if operation is not None:
                statement = statement.where(
                    PmBlockerOccurrenceRow.operation == operation
                )
            if candidate_ticket_id is not None:
                statement = statement.where(
                    PmBlockerOccurrenceRow.candidate_ticket_id == candidate_ticket_id
                )
            rows = session.scalars(
                statement.order_by(
                    PmBlockerOccurrenceRow.first_observed_at,
                    PmBlockerOccurrenceRow.id,
                )
            )
            return [_blocker_model(session, row) for row in rows]

    def list_active_episodes_ordered(self, product_id: UUID) -> list[PmRecoveryEpisode]:
        """Expose the dormant fairness projection without selecting runtime work."""

        with self._db.session() as session:
            rows = session.scalars(
                sa.select(PmRecoveryEpisodeRow)
                .where(
                    PmRecoveryEpisodeRow.product_id == product_id,
                    PmRecoveryEpisodeRow.closed_at.is_(None),
                )
                .order_by(
                    sa.func.coalesce(
                        PmRecoveryEpisodeRow.last_evaluated_sequence,
                        PmRecoveryEpisodeRow.episode_created_sequence,
                    ),
                    sa.func.coalesce(PmRecoveryEpisodeRow.candidate_ticket_key, ""),
                    PmRecoveryEpisodeRow.id,
                )
            )
            return [_episode_model(row) for row in rows]

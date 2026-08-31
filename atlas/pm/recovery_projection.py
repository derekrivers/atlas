"""Pure PM-boundary projections from durable recovery storage."""

from __future__ import annotations

from atlas.core.models.pm_recovery import DurablePmBlocker
from atlas.pm.health import PmBlockerObservation


def project_durable_blocker(blocker: DurablePmBlocker) -> PmBlockerObservation:
    """Project one durable blocker without inventing any freshness timestamp."""

    observation = PmBlockerObservation(
        schema_version=blocker.schema_version,
        operation=blocker.operation,
        code=blocker.code,
        kind=blocker.kind,
        authority_id=blocker.authority_id,
        episode_id=str(blocker.recovery_episode_id),
        candidate_key=blocker.candidate_ticket_key,
        first_observed_at=blocker.first_observed_at,
        last_observed_at=blocker.latest_observed_at,
        consecutive_observations=blocker.consecutive_observations,
        next_safe_retry_at=blocker.next_safe_retry_at,
        capacity_impact=blocker.capacity_impact,
        starved_candidate_keys=tuple(
            candidate.ticket_key for candidate in blocker.starved_candidates
        ),
        starvation_started_at=blocker.starvation_started_at,
        superseded_at=blocker.superseded_at,
    )
    if observation.fingerprint != blocker.blocker_fingerprint:
        raise ValueError("durable and health blocker fingerprints diverged")
    return observation

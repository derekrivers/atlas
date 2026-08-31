"""Pure PM operational-health and temporal recovery contract."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from atlas.pm.health import (
    PmBlockerKind,
    PmBlockerObservation,
    PmHealthInputs,
    PmHealthPolicy,
    PmHealthReasonCode,
    PmHealthStatus,
    assess_pm_health,
)

NOW = datetime(2026, 8, 31, 12, tzinfo=UTC)
CONTRACT_PATH = (
    Path(__file__).parents[1]
    / "docs"
    / "atlas"
    / "pm-resilience-and-retrospective-recovery.md"
)


def _contract_section(start: str, end: str) -> str:
    contract = " ".join(CONTRACT_PATH.read_text(encoding="utf-8").split())
    assert start in contract
    assert end in contract
    return contract.split(start, maxsplit=1)[1].split(end, maxsplit=1)[0]


def _inputs(
    *,
    blockers: tuple[PmBlockerObservation, ...] = (),
    heartbeat: datetime | None = NOW,
    board: datetime | None = NOW,
    convergence: datetime | None = NOW,
    progress: datetime | None = NOW,
    progress_expected: bool = False,
    schema_version: str = "pm-health-inputs-v1",
) -> PmHealthInputs:
    return PmHealthInputs(
        schema_version=schema_version,
        observed_at=NOW,
        last_heartbeat_at=heartbeat,
        last_coherent_board_at=board,
        last_convergence_at=convergence,
        last_progress_at=progress,
        progress_expected=progress_expected,
        blocker_observations=blockers,
    )


def _blocker(
    *,
    kind: PmBlockerKind = PmBlockerKind.ROUTINE_WAIT,
    consecutive: int = 1,
    first_observed_at: datetime = NOW - timedelta(seconds=30),
    last_observed_at: datetime = NOW,
    next_safe_retry_at: datetime | None = NOW + timedelta(seconds=30),
    capacity_impact: bool = False,
    starved: tuple[str, ...] = (),
    starvation_started_at: datetime | None = None,
    superseded_at: datetime | None = None,
    schema_version: str = "pm-blocker-observation-v1",
    authority_id: str = "pm-ci-handoff-v1",
    episode_id: str = "ci-pending:ATLAS-290:episode-1",
    operation: str = "ci_handoff",
    code: str = "publication_not_yet_complete",
    candidate_key: str | None = "ATLAS-290",
) -> PmBlockerObservation:
    return PmBlockerObservation(
        schema_version=schema_version,
        operation=operation,
        code=code,
        kind=kind,
        authority_id=authority_id,
        episode_id=episode_id,
        candidate_key=candidate_key,
        first_observed_at=first_observed_at,
        last_observed_at=last_observed_at,
        consecutive_observations=consecutive,
        next_safe_retry_at=next_safe_retry_at,
        capacity_impact=capacity_impact,
        starved_candidate_keys=starved,
        starvation_started_at=starvation_started_at,
        superseded_at=superseded_at,
    )


def _codes(inputs: PmHealthInputs) -> set[PmHealthReasonCode]:
    result = assess_pm_health(inputs, PmHealthPolicy())
    return {reason.code for reason in result.reasons}


def test_fresh_converged_inputs_are_healthy() -> None:
    result = assess_pm_health(_inputs(), PmHealthPolicy())

    assert result.status is PmHealthStatus.HEALTHY
    assert result.reasons == ()


def test_cadence_freshness_blocks_at_explicit_heartbeat_threshold() -> None:
    policy = PmHealthPolicy(
        expected_cadence=timedelta(seconds=30),
        heartbeat_stale_after=timedelta(seconds=90),
        coherent_board_stale_after=timedelta(seconds=90),
    )

    result = assess_pm_health(
        _inputs(heartbeat=NOW - timedelta(seconds=90)),
        policy,
    )

    assert result.status is PmHealthStatus.BLOCKED
    assert PmHealthReasonCode.HEARTBEAT_STALE in {r.code for r in result.reasons}


def test_routine_wait_within_retry_window_remains_healthy() -> None:
    result = assess_pm_health(_inputs(blockers=(_blocker(),)), PmHealthPolicy())

    assert result.status is PmHealthStatus.HEALTHY
    assert _codes(_inputs(blockers=(_blocker(),))) == {PmHealthReasonCode.ROUTINE_WAIT}


def test_transient_retryable_observation_is_degraded() -> None:
    blocker = _blocker(kind=PmBlockerKind.RETRYABLE)

    result = assess_pm_health(_inputs(blockers=(blocker,)), PmHealthPolicy())

    assert result.status is PmHealthStatus.DEGRADED
    assert PmHealthReasonCode.TRANSIENT_RETRY in {r.code for r in result.reasons}


def test_recurrence_threshold_makes_completed_ticks_operationally_blocked() -> None:
    policy = PmHealthPolicy(retryable_degraded_after=2, retryable_blocked_after=4)
    blocker = _blocker(kind=PmBlockerKind.RETRYABLE, consecutive=4)

    result = assess_pm_health(_inputs(blockers=(blocker,)), policy)

    assert result.status is PmHealthStatus.BLOCKED
    assert PmHealthReasonCode.RECURRING_BLOCKER in {r.code for r in result.reasons}


def test_unresolved_write_fence_blocks_immediately() -> None:
    blocker = _blocker(kind=PmBlockerKind.UNRESOLVED_FENCE)

    result = assess_pm_health(_inputs(blockers=(blocker,)), PmHealthPolicy())

    assert result.status is PmHealthStatus.BLOCKED
    assert PmHealthReasonCode.UNRESOLVED_FENCE in {r.code for r in result.reasons}


def test_starvation_blocks_after_policy_threshold() -> None:
    blocker = _blocker(
        capacity_impact=True,
        starved=("ATLAS-292", "ATLAS-291"),
        starvation_started_at=NOW - timedelta(minutes=5),
    )

    result = assess_pm_health(_inputs(blockers=(blocker,)), PmHealthPolicy())

    assert result.status is PmHealthStatus.BLOCKED
    assert PmHealthReasonCode.STARVATION in {r.code for r in result.reasons}


def test_progress_supersedes_an_obsolete_blocker() -> None:
    blocker = _blocker(
        kind=PmBlockerKind.UNRESOLVED_FENCE,
        first_observed_at=NOW - timedelta(minutes=3),
        last_observed_at=NOW - timedelta(minutes=2),
        superseded_at=NOW - timedelta(minutes=1),
    )

    result = assess_pm_health(_inputs(blockers=(blocker,)), PmHealthPolicy())

    assert result.status is PmHealthStatus.HEALTHY
    assert result.active_blocker_fingerprints == ()


@pytest.mark.parametrize(
    ("inputs", "expected_code"),
    [
        (
            _inputs(schema_version="legacy-untyped-receipt-v0"),
            PmHealthReasonCode.UNKNOWN_OR_LEGACY_INPUT,
        ),
        (
            _inputs(blockers=(_blocker(kind=PmBlockerKind.UNKNOWN),)),
            PmHealthReasonCode.UNKNOWN_OR_LEGACY_INPUT,
        ),
    ],
)
def test_unknown_or_legacy_input_fails_closed(
    inputs: PmHealthInputs,
    expected_code: PmHealthReasonCode,
) -> None:
    result = assess_pm_health(inputs, PmHealthPolicy())

    assert result.status is PmHealthStatus.BLOCKED
    assert expected_code in {reason.code for reason in result.reasons}


def test_future_blocker_metadata_fails_closed() -> None:
    blocker = _blocker(
        first_observed_at=NOW,
        last_observed_at=NOW,
        superseded_at=NOW + timedelta(seconds=1),
    )

    result = assess_pm_health(_inputs(blockers=(blocker,)), PmHealthPolicy())

    assert result.status is PmHealthStatus.BLOCKED
    assert PmHealthReasonCode.UNKNOWN_OR_LEGACY_INPUT in {
        reason.code for reason in result.reasons
    }


def test_heartbeat_board_convergence_and_progress_are_independent_signals() -> None:
    policy = PmHealthPolicy()

    live_but_not_converging = assess_pm_health(
        _inputs(convergence=NOW - policy.convergence_degraded_after),
        policy,
    )
    coherent_but_not_progressing = assess_pm_health(
        _inputs(
            progress=NOW - policy.progress_blocked_after,
            progress_expected=True,
        ),
        policy,
    )
    stale_board = assess_pm_health(
        _inputs(board=NOW - policy.coherent_board_stale_after),
        policy,
    )

    assert live_but_not_converging.status is PmHealthStatus.DEGRADED
    assert _codes(_inputs(convergence=NOW - policy.convergence_degraded_after)) == {
        PmHealthReasonCode.CONVERGENCE_STALE
    }
    assert coherent_but_not_progressing.status is PmHealthStatus.BLOCKED
    assert stale_board.status is PmHealthStatus.BLOCKED


def test_idle_progress_is_not_required_but_demanded_stall_is_unhealthy() -> None:
    policy = PmHealthPolicy()

    idle = assess_pm_health(
        _inputs(progress=None, progress_expected=False),
        policy,
    )
    demanded_without_progress = assess_pm_health(
        _inputs(progress=None, progress_expected=True),
        policy,
    )
    demanded_and_stalled = assess_pm_health(
        _inputs(
            progress=NOW - policy.progress_blocked_after,
            progress_expected=True,
        ),
        policy,
    )

    assert idle.status is PmHealthStatus.HEALTHY
    assert idle.progress_expected is False
    assert demanded_without_progress.status is PmHealthStatus.DEGRADED
    assert demanded_without_progress.progress_expected is True
    assert demanded_and_stalled.status is PmHealthStatus.BLOCKED


def test_temporal_reconstruction_recurs_then_recovers_without_process_memory() -> None:
    policy = PmHealthPolicy(retryable_degraded_after=2, retryable_blocked_after=3)

    tick_one = assess_pm_health(
        _inputs(blockers=(_blocker(kind=PmBlockerKind.RETRYABLE),)),
        policy,
    )
    tick_three = assess_pm_health(
        _inputs(blockers=(_blocker(kind=PmBlockerKind.RETRYABLE, consecutive=3),)),
        policy,
    )
    recovered = assess_pm_health(
        _inputs(
            blockers=(
                _blocker(
                    kind=PmBlockerKind.RETRYABLE,
                    consecutive=3,
                    first_observed_at=NOW - timedelta(minutes=3),
                    last_observed_at=NOW - timedelta(minutes=2),
                    superseded_at=NOW - timedelta(minutes=1),
                ),
            )
        ),
        policy,
    )

    assert tick_one.status is PmHealthStatus.DEGRADED
    assert tick_three.status is PmHealthStatus.BLOCKED
    assert recovered.status is PmHealthStatus.HEALTHY


def test_order_and_timezone_do_not_change_fingerprints() -> None:
    offset = timezone(timedelta(hours=2))
    blocker_a = _blocker(starved=("ATLAS-292", "ATLAS-291"))
    assert blocker_a.next_safe_retry_at is not None
    blocker_b = PmBlockerObservation(
        **{
            **blocker_a.model_dump(),
            "first_observed_at": blocker_a.first_observed_at.astimezone(offset),
            "last_observed_at": blocker_a.last_observed_at.astimezone(offset),
            "next_safe_retry_at": blocker_a.next_safe_retry_at.astimezone(offset),
            "starved_candidate_keys": ("ATLAS-291", "ATLAS-292"),
        }
    )
    other = PmBlockerObservation(
        operation="admission",
        code="lease_unavailable",
        kind=PmBlockerKind.RETRYABLE,
        authority_id="pm-admission-v1",
        episode_id="admission:ATLAS-300:episode-1",
        candidate_key="ATLAS-300",
        first_observed_at=NOW,
        last_observed_at=NOW,
        consecutive_observations=1,
        next_safe_retry_at=NOW + timedelta(seconds=30),
    )

    first = assess_pm_health(_inputs(blockers=(blocker_a, other)), PmHealthPolicy())
    second = assess_pm_health(_inputs(blockers=(other, blocker_b)), PmHealthPolicy())

    assert blocker_a.fingerprint == blocker_b.fingerprint
    assert first.active_blocker_fingerprints == second.active_blocker_fingerprints
    assert first.reasons == second.reasons
    assert first.fingerprint == second.fingerprint


def test_blocker_fingerprint_is_stable_across_mutable_recurrence_state() -> None:
    first = _blocker()
    recurrence = _blocker(
        consecutive=5,
        first_observed_at=NOW - timedelta(minutes=4),
        next_safe_retry_at=NOW + timedelta(minutes=3),
        capacity_impact=True,
        starved=("ATLAS-291",),
        starvation_started_at=NOW - timedelta(minutes=2),
    )

    assert first.fingerprint == recurrence.fingerprint
    changed_episode = _blocker(episode_id="ci-pending:ATLAS-290:episode-2")
    assert first.fingerprint != changed_episode.fingerprint
    assert first.fingerprint != _blocker(code="publication_ambiguous").fingerprint


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "s" * 65),
        ("operation", "o" * 129),
        ("code", "c" * 129),
        ("authority_id", "a" * 129),
        ("episode_id", "e" * 129),
        ("candidate_key", "k" * 129),
    ],
)
def test_blocker_identity_fields_are_bounded(field: str, value: str) -> None:
    payload = _blocker().model_dump()
    payload[field] = value

    with pytest.raises(ValidationError):
        PmBlockerObservation.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("operation", "ci\nhandoff"),
        ("candidate_key", "ATLAS-290\x00"),
        ("episode_id", " episode-1"),
    ],
)
def test_blocker_identity_rejects_controls_and_padding(
    field: str,
    value: str,
) -> None:
    payload = _blocker().model_dump()
    payload[field] = value

    with pytest.raises(ValidationError):
        PmBlockerObservation.model_validate(payload)


def test_blocker_and_starvation_collections_are_bounded() -> None:
    blocker_payload = _blocker().model_dump()
    blocker_payload["starved_candidate_keys"] = tuple(
        f"ATLAS-{index}" for index in range(129)
    )
    inputs_payload = _inputs().model_dump()
    inputs_payload["blocker_observations"] = tuple(
        _blocker(episode_id=f"episode-{index}").model_dump() for index in range(129)
    )

    with pytest.raises(ValidationError):
        PmBlockerObservation.model_validate(blocker_payload)
    with pytest.raises(ValidationError):
        PmHealthInputs.model_validate(inputs_payload)

    blocker_payload["starved_candidate_keys"] = ("ATLAS-\x00291",)
    with pytest.raises(ValidationError, match="control characters"):
        PmBlockerObservation.model_validate(blocker_payload)


def test_domain_inputs_forbid_unexpected_fields() -> None:
    blocker_payload = {**_blocker().model_dump(), "unexpected": True}
    inputs_payload = {**_inputs().model_dump(), "unexpected": True}
    policy_payload = {**PmHealthPolicy().model_dump(), "unexpected": True}

    with pytest.raises(ValidationError, match="extra_forbidden"):
        PmBlockerObservation.model_validate(blocker_payload)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PmHealthInputs.model_validate(inputs_payload)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PmHealthPolicy.model_validate(policy_payload)


def test_retrospective_done_contract_requires_complete_accepted_head_proof() -> None:
    contract = " ".join(CONTRACT_PATH.read_text(encoding="utf-8").split())
    complete_proof = _contract_section(
        "It must freshly establish all of the following:",
        "Evidence recovery appends",
    )

    for required_clause in (
        "persisted `PASSED` Verification Engine verdict",
        "`acceptance_criteria_fingerprint`",
        "human-tier criterion confirmations",
        "system-tier `PR_MERGED` proof",
        "strict fresh merge/main ancestry proof",
        "merge alone is insufficient",
        "distinct authenticated operator recovery decision",
        "PM Retrospective Completion Reconciler",
        "product-scoped retrospective-completion lease",
        "`retrospective_completion_write_fence`",
        "This edge is **INACTIVE**",
        "current runtime ownership remains unchanged",
    ):
        assert required_clause in contract
    assert (
        "all required human-tier criterion confirmations, every required scope "
        "decision, and blanket approval"
    ) in complete_proof


def test_operator_recovery_fallback_is_ordered_and_non_circular() -> None:
    contract = _contract_section(
        "The fallback is ordered and cannot use",
        "That action is not reconstructed",
    )
    predecision = "distinct authenticated operator recovery decision begins"
    evidence = "writes the equivalent canonical exact-head human-tier evidence"
    verdict = "Verification Engine then recompute and persist a new `PASSED` verdict"
    receipt = "append-only recovery receipt is written last"

    assert (
        "predecision must not require the unavailable verdict as an input" in contract
    )
    assert "Recovery code cannot write, copy or assume that verdict" in contract
    assert "separately verified alternative" in contract
    assert "resulting persisted verdict id and commit" in contract
    assert (
        "Only after that evidence exists does Verification Engine then recompute "
        "and persist a new `PASSED` verdict"
    ) in contract
    assert "before that evidence exists" not in contract
    assert contract.index(predecision) < contract.index(evidence)
    assert contract.index(evidence) < contract.index(verdict)
    assert contract.index(verdict) < contract.index(receipt)


def test_fairness_contract_uses_a_monotonic_sequence_without_newcomer_cutting() -> None:
    contract = _contract_section(
        "## Fair bounded evaluation",
        "## Durable blocker observations",
    )

    for required_clause in (
        "`episode_created_sequence`",
        "`last_evaluated_sequence`",
        "new global monotonic sequence",
        "finite eligible snapshot",
        "finite arrivals between ticks",
        "new arrivals cannot cut ahead of an older retry cursor",
    ):
        assert required_clause in contract
    assert (
        "Selection takes the least cursor from one finite eligible snapshot" in contract
    )
    assert "greatest cursor" not in contract


def test_manifest_keeps_specialist_authority_and_inactive_edge_coherent() -> None:
    manifest = " ".join(
        (CONTRACT_PATH.parents[1] / "MANIFEST.md").read_text(encoding="utf-8").split()
    )

    assert "pm-resilience-and-retrospective-recovery.md" in manifest
    assert "write-boundary recovery, eventual convergence" in manifest
    assert "target retrospective edge remains inactive" in manifest


def test_policy_rejects_incoherent_thresholds() -> None:
    with pytest.raises(ValidationError, match="blocked threshold precedes"):
        PmHealthPolicy(
            convergence_degraded_after=timedelta(minutes=10),
            convergence_blocked_after=timedelta(minutes=5),
        )

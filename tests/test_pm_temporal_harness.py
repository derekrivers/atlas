from __future__ import annotations

import operator
import sqlite3
from collections.abc import Mapping, MutableMapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from pm_temporal_harness import (
    DeterministicClock,
    DurableReplayConflict,
    ExternalRequest,
    ExternalResult,
    FaultPoint,
    IdempotencyConflict,
    ProcessGeneration,
    SimulatedProcessDeath,
    TemporalHarness,
    TypedHold,
    WorkflowTick,
)

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def _request(
    resource: str = "ATLAS-290", *, provider: str = "linear"
) -> ExternalRequest:
    return ExternalRequest(
        provider=provider,
        operation="patch",
        resource=resource,
        payload={"state": "Review Required"},
        idempotency_key=f"review:{resource}",
    )


def _complete_write(harness: TemporalHarness, request: ExternalRequest) -> None:
    with harness.new_generation() as generation:
        tick = generation.tick(f"recovery-{harness.generation_count}")
        if not generation.is_acknowledged(request.request_fingerprint):
            result = tick.external_write(request)
            generation.acknowledge_external(request, result, tick_id=tick.tick_id)
        if not generation.has_receipt(request.request_fingerprint):
            generation.record_receipt(
                request.request_fingerprint,
                tick_id=tick.tick_id,
                status="acknowledged",
            )


def test_clock_and_request_identity_are_canonical_and_controllable() -> None:
    clock = DeterministicClock(NOW)
    first = ExternalRequest(
        provider="github",
        operation="patch",
        resource="pr:370",
        payload={"labels": ["safe"], "merged": True},
    )
    reordered = ExternalRequest(
        provider="github",
        operation="patch",
        resource="pr:370",
        payload={"merged": True, "labels": ["safe"]},
    )

    assert first.request_fingerprint == reordered.request_fingerprint
    assert first.effect_identity == reordered.effect_identity
    assert clock() == NOW
    assert clock.advance(timedelta(minutes=3)) == NOW + timedelta(minutes=3)
    with pytest.raises(ValueError, match="cannot move backwards"):
        clock.advance(timedelta(microseconds=-1))


def test_file_store_and_generation_resources_reconstruct_completely(
    tmp_path: Path,
) -> None:
    disposed: list[int] = []
    built: list[object] = []

    class Repository:
        pass

    harness = TemporalHarness(db_path=tmp_path / "pm.sqlite3", initial_time=NOW)

    def build_repository(generation: ProcessGeneration) -> Repository:
        repository = Repository()
        built.append(repository)
        generation.connection.execute(
            "CREATE TABLE IF NOT EXISTS workflow_state (value TEXT NOT NULL)"
        )
        generation.connection.commit()
        return repository

    harness.register_generation_resource(
        "repository",
        build_repository,
        disposer=lambda resource: disposed.append(id(resource)),
    )

    first = harness.new_generation()
    first_repository = first.resource("repository")
    database_path = first.connection.execute("PRAGMA database_list").fetchone()[2]
    first.connection.execute("INSERT INTO workflow_state VALUES ('durable')")
    first.connection.commit()
    second = harness.restart()

    assert Path(database_path) == harness.db_path
    assert second.resource("repository") is not first_repository
    persisted = second.connection.execute(
        "SELECT value FROM workflow_state"
    ).fetchone()[0]
    assert persisted == "durable"
    assert disposed == [id(first_repository)]
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        first.connection.execute("SELECT 1")

    second.close()
    assert len(built) == 2
    assert disposed == [id(resource) for resource in built]
    harness.close()


def test_resource_registration_is_frozen_after_first_generation() -> None:
    with TemporalHarness(initial_time=NOW) as harness:
        with harness.new_generation():
            pass
        with pytest.raises(RuntimeError, match="before process creation"):
            harness.register_generation_resource("late", lambda _generation: object())


def test_cleanup_does_not_mask_process_death_and_disposes_every_resource() -> None:
    harness = TemporalHarness(initial_time=NOW)
    temporary_root = harness.db_path.parent
    disposed: list[str] = []
    fail_once = True

    def failing_disposer(resource: object) -> None:
        nonlocal fail_once
        disposed.append(str(resource))
        if fail_once:
            fail_once = False
            raise RuntimeError("disposer failed")

    harness.register_generation_resource(
        "good",
        lambda _generation: "good",
        disposer=lambda value: disposed.append(str(value)),
    )
    harness.register_generation_resource(
        "bad", lambda _generation: "bad", disposer=failing_disposer
    )

    first = harness.new_generation()
    with pytest.raises(SimulatedProcessDeath, match="power lost"), first:
        raise SimulatedProcessDeath("power lost")

    assert disposed == ["bad", "good"]
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        first.connection.execute("SELECT 1")

    with harness.new_generation() as second:
        assert second.connection.execute("SELECT 1").fetchone()[0] == 1
    fail_once = True
    third = harness.new_generation()
    with pytest.raises(RuntimeError, match="disposer failed"):
        harness.close()

    assert disposed == ["bad", "good", "bad", "good", "bad", "good"]
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        third.connection.execute("SELECT 1")
    assert not temporary_root.exists()


def test_provider_world_survives_restart_and_separates_attempts_from_effects() -> None:
    request = _request()
    with TemporalHarness(initial_time=NOW) as harness:
        harness.providers.set_resource("linear", "ATLAS-290", {"state": "CI Pending"})
        with harness.new_generation() as generation:
            result = generation.tick("tick-1").external_write(request)
            generation.acknowledge_external(request, result, tick_id="tick-1")

        harness.providers.set_resource(
            "github", "pr:370", {"head": "abc", "merged": True}
        )
        with harness.new_generation() as generation:
            retry = generation.tick("tick-2").external_write(request)
            assert generation.is_acknowledged(request.request_fingerprint)

        assert retry.applied is False
        assert harness.providers.resource("linear", "ATLAS-290") == {
            "state": "Review Required"
        }
        assert harness.providers.resource("github", "pr:370")["merged"] is True
        harness.providers.ledger.assert_counts(
            request.request_fingerprint, attempts=2, effects=1
        )
        harness.providers.ledger.assert_no_duplicate_harmful_effects()
        harness.assert_durable_row(
            "temporal_acknowledgements",
            request_fingerprint=request.request_fingerprint,
            provider="linear",
        )


def test_handler_cannot_mutate_authoritative_state_before_raising() -> None:
    request = ExternalRequest(
        provider="linear",
        operation="unsafe-handler",
        resource="ATLAS-290",
        payload={"state": "Review Required"},
        idempotency_key="unsafe-handler:ATLAS-290",
    )

    def mutating_handler(current: Any, _payload: Any) -> dict[str, Any]:
        nested = current["metadata"]
        assert isinstance(nested, Mapping)
        operator.setitem(cast(MutableMapping[str, Any], nested), "attempted", True)
        raise RuntimeError("unreachable after read-only mutation")

    with TemporalHarness(initial_time=NOW) as harness:
        original = {"state": "CI Pending", "metadata": {"attempted": False}}
        harness.providers.set_resource("linear", request.resource, original)
        harness.providers.register_operation(
            "linear", "unsafe-handler", mutating_handler
        )

        with pytest.raises(TypeError), harness.new_generation() as generation:
            generation.tick("tick-1").external_write(request)

        assert harness.providers.resource("linear", request.resource) == original
        harness.providers.ledger.assert_counts(
            request.request_fingerprint, attempts=1, effects=0
        )


def test_ambiguous_effect_persists_and_consumes_the_tick_budget() -> None:
    first = _request("ATLAS-290")
    second = _request("ATLAS-291")
    with TemporalHarness(initial_time=NOW) as harness:
        harness.faults.arm(
            FaultPoint.AFTER_EFFECT_BEFORE_RETURN,
            request_fingerprint=first.request_fingerprint,
        )

        with (
            pytest.raises(SimulatedProcessDeath),
            harness.new_generation() as generation,
        ):
            generation.tick("tick-ambiguous").external_write(first)

        assert harness.providers.resource("linear", first.resource)["state"] == (
            "Review Required"
        )
        with (
            harness.new_generation() as generation,
            pytest.raises(TypedHold, match="workflow-effect-limit"),
        ):
            generation.tick("tick-ambiguous").external_write(second)

        harness.providers.ledger.assert_counts(
            first.request_fingerprint, attempts=1, effects=1
        )
        harness.providers.ledger.assert_counts(
            second.request_fingerprint, attempts=0, effects=0
        )


def test_idempotency_key_conflicts_on_changed_request_without_second_effect() -> None:
    first = _request()
    altered = ExternalRequest(
        provider=first.provider,
        operation=first.operation,
        resource=first.resource,
        payload={"state": "Done"},
        idempotency_key=first.idempotency_key,
    )
    assert first.effect_identity == altered.effect_identity
    assert first.request_fingerprint != altered.request_fingerprint

    with TemporalHarness(initial_time=NOW) as harness:
        with harness.new_generation() as generation:
            generation.tick("tick-1").external_write(first)
        with (
            harness.new_generation() as generation,
            pytest.raises(IdempotencyConflict),
        ):
            generation.tick("tick-2").external_write(altered)

        assert harness.providers.resource("linear", first.resource)["state"] == (
            "Review Required"
        )
        harness.providers.ledger.assert_counts(
            first.request_fingerprint, attempts=1, effects=1
        )
        harness.providers.ledger.assert_counts(
            altered.request_fingerprint, attempts=1, effects=0
        )


def test_unkeyed_identical_calls_repeat_effect_and_duplicate_assertion_bites() -> None:
    request = ExternalRequest(
        provider="github",
        operation="patch",
        resource="pr:370",
        payload={"comment": "retry"},
    )
    with TemporalHarness(initial_time=NOW) as harness:
        with harness.new_generation() as generation:
            generation.tick("tick-1").external_write(request)
        with harness.new_generation() as generation:
            generation.tick("tick-2").external_write(request)

        harness.providers.ledger.assert_counts(
            request.request_fingerprint, attempts=2, effects=2
        )
        with pytest.raises(AssertionError):
            harness.providers.ledger.assert_no_duplicate_harmful_effects()


def test_altered_unkeyed_requests_are_distinct_effects() -> None:
    first = ExternalRequest(
        provider="github",
        operation="patch",
        resource="pr:370",
        payload={"comment": "first"},
    )
    second = ExternalRequest(
        provider="github",
        operation="patch",
        resource="pr:370",
        payload={"comment": "second"},
    )
    assert first.request_fingerprint != second.request_fingerprint
    assert first.effect_identity != second.effect_identity

    with TemporalHarness(initial_time=NOW) as harness:
        with harness.new_generation() as generation:
            generation.tick("tick-1").external_write(first)
        with harness.new_generation() as generation:
            generation.tick("tick-2").external_write(second)

        harness.providers.ledger.assert_counts(
            first.request_fingerprint, attempts=1, effects=1
        )
        harness.providers.ledger.assert_counts(
            second.request_fingerprint, attempts=1, effects=1
        )
        harness.providers.ledger.assert_no_duplicate_harmful_effects()


def test_cached_nested_provider_result_is_deep_cloned() -> None:
    request = ExternalRequest(
        provider="github",
        operation="patch",
        resource="pr:370",
        payload={"metadata": {"head": "abc"}},
        idempotency_key="observe:pr:370",
    )
    with TemporalHarness(initial_time=NOW) as harness:
        with harness.new_generation() as generation:
            first = generation.tick("tick-1").external_write(request)
        first.value["metadata"]["head"] = "tampered"
        leaked = harness.providers.resource("github", "pr:370")
        leaked["metadata"]["head"] = "also-tampered"

        with harness.new_generation() as generation:
            replay = generation.tick("tick-2").external_write(request)

        assert replay.value == {"metadata": {"head": "abc"}}
        assert harness.providers.resource("github", "pr:370") == replay.value


@pytest.mark.parametrize(
    ("point", "attempts_before_recovery", "effects_before_recovery"),
    [
        (FaultPoint.BEFORE_PROVIDER_CALL, 0, 0),
        (FaultPoint.AFTER_EFFECT_BEFORE_RETURN, 1, 1),
        (FaultPoint.AFTER_RETURN_BEFORE_LOCAL_ACK, 1, 1),
    ],
)
def test_provider_faults_recover_without_duplicate_harmful_effects(
    point: FaultPoint,
    attempts_before_recovery: int,
    effects_before_recovery: int,
) -> None:
    request = _request()
    with TemporalHarness(initial_time=NOW) as harness:
        harness.providers.set_resource(
            "linear", request.resource, {"state": "CI Pending"}
        )
        harness.faults.arm(point, request_fingerprint=request.request_fingerprint)

        with (
            pytest.raises(SimulatedProcessDeath),
            harness.new_generation() as generation,
        ):
            generation.tick("tick-1").external_write(request)

        harness.providers.ledger.assert_counts(
            request.request_fingerprint,
            attempts=attempts_before_recovery,
            effects=effects_before_recovery,
        )
        assert harness.durable_rows("temporal_acknowledgements") == []

        _complete_write(harness, request)

        harness.providers.ledger.assert_counts(
            request.request_fingerprint,
            attempts=attempts_before_recovery + 1,
            effects=1,
        )
        harness.providers.ledger.assert_no_duplicate_harmful_effects()
        harness.assert_durable_row(
            "temporal_receipts",
            request_fingerprint=request.request_fingerprint,
            status="acknowledged",
        )


@pytest.mark.parametrize(
    ("point", "expected_attempts"),
    [
        (FaultPoint.BEFORE_DURABLE_ACK, 2),
        (FaultPoint.AFTER_DURABLE_ACK, 1),
        (FaultPoint.BEFORE_RECEIPT, 1),
        (FaultPoint.AFTER_RECEIPT, 1),
    ],
)
def test_acknowledgement_and_receipt_faults_resume_from_durable_rows(
    point: FaultPoint, expected_attempts: int
) -> None:
    request = _request()
    with TemporalHarness(initial_time=NOW) as harness:
        harness.providers.set_resource(
            "linear", request.resource, {"state": "CI Pending"}
        )
        harness.faults.arm(point, request_fingerprint=request.request_fingerprint)

        with (
            pytest.raises(SimulatedProcessDeath),
            harness.new_generation() as generation,
        ):
            tick = generation.tick("tick-1")
            result = tick.external_write(request)
            generation.acknowledge_external(request, result, tick_id=tick.tick_id)
            generation.record_receipt(
                request.request_fingerprint,
                tick_id=tick.tick_id,
                status="acknowledged",
            )

        _complete_write(harness, request)

        harness.providers.ledger.assert_counts(
            request.request_fingerprint, attempts=expected_attempts, effects=1
        )
        harness.providers.ledger.assert_no_duplicate_harmful_effects()
        assert len(harness.durable_rows("temporal_acknowledgements")) == 1
        assert len(harness.durable_rows("temporal_receipts")) == 1


def test_acknowledgement_rejects_mismatched_result_identity() -> None:
    request = _request()
    mismatch = ExternalResult(
        request_fingerprint="wrong-request",
        effect_identity=request.effect_identity,
        value={"state": "Review Required"},
        applied=True,
    )
    with TemporalHarness(initial_time=NOW) as harness:
        with (
            harness.new_generation() as generation,
            pytest.raises(DurableReplayConflict, match="ack-identity"),
        ):
            generation.acknowledge_external(request, mismatch, tick_id="tick-1")
        assert harness.durable_rows("temporal_acknowledgements") == []


def test_durable_acknowledgement_and_receipt_replay_must_be_identical() -> None:
    request = _request()
    with TemporalHarness(initial_time=NOW) as harness:
        with harness.new_generation() as generation:
            result = generation.tick("tick-1").external_write(request)
            generation.acknowledge_external(request, result, tick_id="tick-1")
            generation.record_receipt(
                request.request_fingerprint,
                tick_id="tick-1",
                status="acknowledged",
                payload={"decision": "applied"},
            )
            generation.acknowledge_external(request, result, tick_id="tick-2")
            generation.record_receipt(
                request.request_fingerprint,
                tick_id="tick-2",
                status="acknowledged",
                payload={"decision": "applied"},
            )

            altered_result = ExternalResult(
                request_fingerprint=request.request_fingerprint,
                effect_identity=request.effect_identity,
                value={"state": "Done"},
                applied=False,
            )
            with pytest.raises(DurableReplayConflict, match="ack-replay"):
                generation.acknowledge_external(
                    request, altered_result, tick_id="tick-2"
                )
            with pytest.raises(DurableReplayConflict, match="receipt-replay"):
                generation.record_receipt(
                    request.request_fingerprint,
                    tick_id="tick-2",
                    status="held",
                    payload={"decision": "changed"},
                )

        harness.assert_durable_row(
            "temporal_acknowledgements",
            request_fingerprint=request.request_fingerprint,
            result_json='{"state":"Review Required"}',
        )
        harness.assert_durable_row(
            "temporal_receipts",
            request_fingerprint=request.request_fingerprint,
            status="acknowledged",
            payload_json='{"decision":"applied"}',
        )


def test_simulated_process_death_is_not_caught_as_an_ordinary_exception() -> None:
    caught_exception = False
    caught_process_death = False
    try:
        try:
            raise SimulatedProcessDeath("power lost")
        except Exception:  # pragma: no branch - this must not catch BaseException
            caught_exception = True
    except SimulatedProcessDeath:
        caught_process_death = True

    assert caught_exception is False
    assert caught_process_death is True
    assert not issubclass(SimulatedProcessDeath, Exception)


def test_one_workflow_effect_per_tick_is_a_typed_hold_not_a_second_call() -> None:
    first = _request("ATLAS-290")
    second = _request("ATLAS-291")
    with TemporalHarness(initial_time=NOW) as harness:
        with harness.new_generation() as generation:
            tick = generation.tick("tick-1")
            tick.external_write(first)
            with pytest.raises(TypedHold) as raised:
                tick.external_write(second)

        assert raised.value.code == "workflow-effect-limit"
        harness.providers.ledger.assert_counts(
            first.request_fingerprint, attempts=1, effects=1
        )
        harness.providers.ledger.assert_counts(
            second.request_fingerprint, attempts=0, effects=0
        )

        with harness.new_generation() as generation:
            generation.tick("tick-2").external_write(second)

        harness.providers.ledger.assert_at_most_one_workflow_effect_per_tick()


def test_bounded_runner_reconstructs_each_tick_and_converges_from_durable_state() -> (
    None
):
    request = _request()
    built: list[int] = []
    disposed: list[int] = []
    with TemporalHarness(initial_time=NOW) as harness:

        def build_generation_id(generation: ProcessGeneration) -> object:
            built.append(generation.generation_id)
            return generation.generation_id

        def dispose_generation_id(resource: object) -> None:
            assert isinstance(resource, int)
            disposed.append(resource)

        harness.register_generation_resource(
            "repository",
            build_generation_id,
            disposer=dispose_generation_id,
        )

        def step(tick: WorkflowTick) -> bool:
            generation = tick.generation
            if generation.is_acknowledged(request.request_fingerprint):
                return True
            result = tick.external_write(request)
            generation.acknowledge_external(request, result, tick_id=tick.tick_id)
            return False

        result = harness.run_until_converged_or_held(step, max_ticks=3)

        assert result.outcome == "converged"
        assert result.ticks == 2
        assert built == [1, 2]
        assert disposed == [1, 2]
        assert harness.clock() == NOW + timedelta(seconds=1)
        harness.providers.ledger.assert_counts(
            request.request_fingerprint, attempts=1, effects=1
        )


def test_bounded_runner_returns_typed_hold_and_rejects_silent_nonconvergence() -> None:
    with TemporalHarness(initial_time=NOW) as harness:

        def held(_tick: WorkflowTick) -> bool:
            raise TypedHold("provider-ambiguous", "github:pr:370", retryable=False)

        result = harness.run_until_converged_or_held(held, max_ticks=4)
        assert result.outcome == "held"
        assert result.ticks == 1
        assert result.hold is not None
        assert result.hold.code == "provider-ambiguous"
        assert result.hold.retryable is False

    attempts = 0
    with TemporalHarness(initial_time=NOW) as harness:

        def eventually_available(_tick: WorkflowTick) -> bool:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise TypedHold("provider-unavailable", "github", retryable=True)
            return True

        result = harness.run_until_converged_or_held(eventually_available, max_ticks=4)
        assert result.outcome == "converged"
        assert result.ticks == 3
        assert harness.generation_count == 3
        assert harness.clock() == NOW + timedelta(seconds=2)

    with TemporalHarness(initial_time=NOW) as harness:

        def still_retryable(_tick: WorkflowTick) -> bool:
            raise TypedHold("provider-unavailable", "linear", retryable=True)

        result = harness.run_until_converged_or_held(still_retryable, max_ticks=2)
        assert result.outcome == "held"
        assert result.ticks == 2
        assert result.hold is not None
        assert result.hold.retryable is True
        assert harness.generation_count == 2
        assert harness.clock() == NOW + timedelta(seconds=1)

    with (
        TemporalHarness(initial_time=NOW) as harness,
        pytest.raises(AssertionError, match="within 2 ticks"),
    ):
        harness.run_until_converged_or_held(lambda _tick: False, max_ticks=2)

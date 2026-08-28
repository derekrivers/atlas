"""ATLAS-282 planner execution and provider-call telemetry contracts."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from atlas.core.models import (
    MeasurementAvailability,
    PlannerDigestAlgorithm,
    PlannerLogicalCall,
    PlannerPhysicalTransportAttempt,
    PlannerProcessingDisposition,
    PlannerRetryCategory,
    PlannerTransportDisposition,
    PlanningExecution,
    PlanningExecutionFailureStage,
    PlanningExecutionOutcome,
    PlanningExecutionOutcomeStatus,
    PlanRun,
    PlanRunStatus,
    ProviderEvidenceAvailability,
)
from atlas.core.models.planner_call_telemetry import (
    OptionalTimingValue,
    PlannerExecutionParameters,
    PlannerIdentity,
    PlannerInputIdentity,
    PlannerLogicalCallIdentity,
    PlannerPayloadSize,
    PlannerPhysicalAttemptIdentity,
    PlannerPostResponseDisposition,
    PlannerPromptSegmentSize,
    PlannerPromptTemplateIdentity,
    PlannerProviderUsage,
    PlanningExecutionIdentity,
    ProviderUsageValue,
)

EXECUTION_ID = UUID("11111111-1111-4111-8111-111111111111")
PRODUCT_ID = UUID("22222222-2222-4222-8222-222222222222")
PLAN_RUN_ID = UUID("33333333-3333-4333-8333-333333333333")
OTHER_PLAN_RUN_ID = UUID("44444444-4444-4444-8444-444444444444")
PREFLIGHT_AT = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)
CREATED_AT = datetime(2026, 8, 28, 9, 0, 1, tzinfo=UTC)


def planner() -> PlannerIdentity:
    return PlannerIdentity(provider="anthropic", model="claude-sonnet-4-6")


def parameters() -> PlannerExecutionParameters:
    return PlannerExecutionParameters(
        temperature=0,
        max_output_tokens=64_000,
        request_timeout_ms=900_000,
        streaming=True,
    )


def source_inputs(
    *, reverse: bool = False, uppercase: bool = False
) -> list[PlannerInputIdentity]:
    digest_a = "A" * 64 if uppercase else "a" * 64
    digest_b = "B" * 40 if uppercase else "b" * 40
    values = [
        PlannerInputIdentity(
            name="docs/atlas/planning-engine-specification.md",
            algorithm=PlannerDigestAlgorithm.SHA256,
            digest=digest_a,
        ),
        PlannerInputIdentity(
            name="docs/architecture/data-model-and-schemas.md",
            algorithm=PlannerDigestAlgorithm.GIT_SHA1,
            digest=digest_b,
        ),
    ]
    return list(reversed(values)) if reverse else values


def template(*, uppercase: bool = False) -> PlannerPromptTemplateIdentity:
    return PlannerPromptTemplateIdentity(
        stage="plan",
        template_name="planner-v1.2.0.md.j2",
        prompt_version="1.2.0",
        template_sha256=("C" if uppercase else "c") * 64,
    )


def logical_identity(
    *, stage: str = "plan", logical_attempt_no: int = 1
) -> PlannerLogicalCallIdentity:
    return PlannerLogicalCallIdentity(
        execution=PlanningExecutionIdentity(execution_id=EXECUTION_ID),
        stage=stage,
        logical_attempt_no=logical_attempt_no,
    )


def attempt(
    number: int,
    *,
    logical: PlannerLogicalCallIdentity | None = None,
    succeeded: bool = False,
    plan_run_id: UUID | None = None,
    complete_usage: bool = False,
    offset_time: bool = False,
) -> PlannerPhysicalTransportAttempt:
    logical = logical or logical_identity()
    started_at = CREATED_AT + timedelta(seconds=number)
    if offset_time:
        started_at = started_at.astimezone(timezone(timedelta(hours=2)))
    usage = (
        PlannerProviderUsage(
            input_tokens=ProviderUsageValue(
                availability=ProviderEvidenceAvailability.REPORTED, value=1200
            ),
            output_tokens=ProviderUsageValue(
                availability=ProviderEvidenceAvailability.REPORTED, value=340
            ),
            cache_creation_input_tokens=ProviderUsageValue(
                availability=ProviderEvidenceAvailability.REPORTED, value=1000
            ),
            cache_read_input_tokens=ProviderUsageValue(
                availability=ProviderEvidenceAvailability.REPORTED, value=200
            ),
            reasoning_tokens=ProviderUsageValue(
                availability=ProviderEvidenceAvailability.UNSUPPORTED
            ),
        )
        if complete_usage
        else PlannerProviderUsage(
            cache_creation_input_tokens=ProviderUsageValue(
                availability=ProviderEvidenceAvailability.UNSUPPORTED
            ),
            cache_read_input_tokens=ProviderUsageValue(
                availability=ProviderEvidenceAvailability.UNAVAILABLE
            ),
            reasoning_tokens=ProviderUsageValue(
                availability=ProviderEvidenceAvailability.UNSUPPORTED
            ),
        )
    )
    return PlannerPhysicalTransportAttempt(
        identity=PlannerPhysicalAttemptIdentity(
            logical_call=logical,
            physical_attempt_no=number,
        ),
        started_at=started_at,
        transport_disposition=(
            PlannerTransportDisposition.SUCCEEDED
            if succeeded
            else PlannerTransportDisposition.FAILED
        ),
        wall_latency_ms=250 if succeeded else 80,
        time_to_first_token=(
            OptionalTimingValue(
                availability=MeasurementAvailability.MEASURED, value_ms=45
            )
            if succeeded
            else OptionalTimingValue(availability=MeasurementAvailability.UNAVAILABLE)
        ),
        retry_category=(
            PlannerRetryCategory.NONE if succeeded else PlannerRetryCategory.CONNECTION
        ),
        output_size=(
            PlannerPayloadSize(byte_count=420, character_count=410)
            if succeeded
            else None
        ),
        provider_usage=usage,
        stop_reason="end_turn" if succeeded else None,
        processing=(
            PlannerPostResponseDisposition(
                parse=PlannerProcessingDisposition.PASSED,
                schema_validation=PlannerProcessingDisposition.PASSED,
                gate=PlannerProcessingDisposition.PASSED,
            )
            if succeeded
            else PlannerPostResponseDisposition()
        ),
        resulting_plan_run_id=plan_run_id,
    )


def logical_call(
    *,
    physical_attempts: list[PlannerPhysicalTransportAttempt] | None = None,
    input_identities: list[PlannerInputIdentity] | None = None,
    prompt_segments: list[PlannerPromptSegmentSize] | None = None,
    selected_template: PlannerPromptTemplateIdentity | None = None,
    identity: PlannerLogicalCallIdentity | None = None,
) -> PlannerLogicalCall:
    return PlannerLogicalCall(
        identity=identity or logical_identity(),
        planner=planner(),
        template=selected_template or template(),
        execution_parameters=parameters(),
        input_identities=tuple(input_identities or source_inputs()),
        prompt_size=PlannerPayloadSize(byte_count=2100, character_count=2000),
        prompt_segments=tuple(
            prompt_segments
            or [
                PlannerPromptSegmentSize(
                    name="documents", byte_count=1500, character_count=1450
                ),
                PlannerPromptSegmentSize(
                    name="instructions", byte_count=500, character_count=450
                ),
            ]
        ),
        physical_attempts=tuple(physical_attempts or []),
    )


def execution(
    *,
    calls: list[PlannerLogicalCall] | None = None,
    outcome: PlanningExecutionOutcome | None = None,
    inputs: list[PlannerInputIdentity] | None = None,
    templates: list[PlannerPromptTemplateIdentity] | None = None,
    preflight_completed_at: datetime = PREFLIGHT_AT,
    created_at: datetime = CREATED_AT,
) -> PlanningExecution:
    return PlanningExecution(
        identity=PlanningExecutionIdentity(execution_id=EXECUTION_ID),
        product_id=PRODUCT_ID,
        preflight_completed_at=preflight_completed_at,
        created_at=created_at,
        planner=planner(),
        execution_parameters=parameters(),
        input_identities=tuple(inputs or source_inputs()),
        prompt_templates=tuple(templates or [template()]),
        logical_calls=tuple(calls or []),
        outcome=outcome,
    )


def completed_outcome(
    *,
    plan_run_id: UUID = PLAN_RUN_ID,
    status: PlanningExecutionOutcomeStatus = PlanningExecutionOutcomeStatus.COMPLETED,
) -> PlanningExecutionOutcome:
    return PlanningExecutionOutcome(
        status=status,
        completed_at=CREATED_AT + timedelta(seconds=10),
        raw_output_observed=True,
        failure_stage=(
            None
            if status is PlanningExecutionOutcomeStatus.COMPLETED
            else PlanningExecutionFailureStage.PARSE
        ),
        resulting_plan_run_id=plan_run_id,
    )


def test_complete_execution_logical_and_physical_hierarchy() -> None:
    first = attempt(1, plan_run_id=PLAN_RUN_ID)
    second = attempt(
        2,
        succeeded=True,
        plan_run_id=PLAN_RUN_ID,
        complete_usage=True,
    )
    observed = execution(
        calls=[logical_call(physical_attempts=[first, second])],
        outcome=completed_outcome(),
    )

    call = observed.logical_calls[0]
    assert call.identity.execution == observed.identity
    assert [item.identity.physical_attempt_no for item in call.physical_attempts] == [
        1,
        2,
    ]
    assert call.physical_attempts[1].provider_usage.input_tokens.value == 1200
    assert call.physical_attempts[1].time_to_first_token.value_ms == 45
    assert call.physical_attempts[0].resulting_plan_run_id == PLAN_RUN_ID
    assert call.physical_attempts[1].resulting_plan_run_id == PLAN_RUN_ID
    assert observed.outcome is not None
    assert observed.outcome.resulting_plan_run_id == PLAN_RUN_ID


def test_sparse_provider_evidence_never_fabricates_zero() -> None:
    sparse = attempt(1)
    dumped = sparse.model_dump(mode="json")

    assert dumped["provider_usage"] == {
        "input_tokens": {"availability": "unavailable", "value": None},
        "output_tokens": {"availability": "unavailable", "value": None},
        "cache_creation_input_tokens": {
            "availability": "unsupported",
            "value": None,
        },
        "cache_read_input_tokens": {
            "availability": "unavailable",
            "value": None,
        },
        "reasoning_tokens": {"availability": "unsupported", "value": None},
    }
    assert dumped["time_to_first_token"] == {
        "availability": "unavailable",
        "value_ms": None,
    }

    with pytest.raises(ValidationError, match="reported_usage_requires_value"):
        ProviderUsageValue(availability=ProviderEvidenceAvailability.REPORTED)
    with pytest.raises(ValidationError, match="missing_usage_cannot_have_value"):
        ProviderUsageValue(
            availability=ProviderEvidenceAvailability.UNSUPPORTED, value=0
        )
    with pytest.raises(ValidationError, match="missing_timing_cannot_have_value"):
        OptionalTimingValue(
            availability=MeasurementAvailability.UNAVAILABLE, value_ms=0
        )


def test_contract_serialization_and_fingerprints_are_deterministic() -> None:
    baseline_attempts = [attempt(1), attempt(2, succeeded=True)]
    baseline = execution(
        calls=[logical_call(physical_attempts=baseline_attempts)],
        outcome=completed_outcome(),
    )
    reordered_attempts = [
        attempt(2, succeeded=True, offset_time=True),
        attempt(1, offset_time=True),
    ]
    reordered = execution(
        inputs=source_inputs(reverse=True, uppercase=True),
        templates=[template(uppercase=True)],
        calls=[
            logical_call(
                physical_attempts=reordered_attempts,
                input_identities=source_inputs(reverse=True, uppercase=True),
                prompt_segments=list(
                    reversed(
                        [
                            PlannerPromptSegmentSize(
                                name="documents",
                                byte_count=1500,
                                character_count=1450,
                            ),
                            PlannerPromptSegmentSize(
                                name="instructions",
                                byte_count=500,
                                character_count=450,
                            ),
                        ]
                    )
                ),
                selected_template=template(uppercase=True),
            )
        ],
        outcome=completed_outcome(),
    )

    assert baseline == reordered
    assert baseline.canonical_bytes() == reordered.canonical_bytes()
    assert baseline.fingerprint == reordered.fingerprint
    assert len(baseline.fingerprint) == 64
    assert json.loads(baseline.canonical_bytes())["created_at"].endswith("Z")


def test_post_preflight_identity_boundary_is_explicit_and_ordered() -> None:
    with pytest.raises(ValidationError, match="preflight_completed_at"):
        PlanningExecution.model_validate(
            {
                "identity": PlanningExecutionIdentity(execution_id=EXECUTION_ID),
                "product_id": PRODUCT_ID,
                "created_at": CREATED_AT,
                "planner": planner(),
                "execution_parameters": parameters(),
                "input_identities": source_inputs(),
                "prompt_templates": [template()],
            }
        )
    with pytest.raises(ValidationError, match="execution_precedes_preflight"):
        execution(
            preflight_completed_at=CREATED_AT,
            created_at=PREFLIGHT_AT,
        )

    early_attempt = attempt(1)
    early_payload = early_attempt.model_dump()
    early_payload["started_at"] = PREFLIGHT_AT
    with pytest.raises(ValidationError, match="attempt_precedes_execution"):
        execution(
            calls=[
                logical_call(
                    physical_attempts=[PlannerPhysicalTransportAttempt(**early_payload)]
                )
            ]
        )

    narrowed = execution(calls=[logical_call(input_identities=[source_inputs()[0]])])
    assert len(narrowed.logical_calls[0].input_identities) == 1

    contradictory_input = PlannerInputIdentity(
        name=source_inputs()[0].name,
        algorithm=PlannerDigestAlgorithm.SHA256,
        digest="f" * 64,
    )
    with pytest.raises(ValidationError, match="call_input_identity_mismatch"):
        execution(calls=[logical_call(input_identities=[contradictory_input])])


def test_terminal_failure_before_raw_output_has_no_plan_run() -> None:
    failed = PlanningExecutionOutcome(
        status=PlanningExecutionOutcomeStatus.FAILED,
        completed_at=CREATED_AT + timedelta(seconds=5),
        raw_output_observed=False,
        failure_stage=PlanningExecutionFailureStage.PROVIDER_BEFORE_OUTPUT,
        resulting_plan_run_id=None,
    )
    observed = execution(
        calls=[logical_call(physical_attempts=[attempt(1)])],
        outcome=failed,
    )
    assert observed.outcome is not None
    assert observed.outcome.resulting_plan_run_id is None

    with pytest.raises(ValidationError, match="post_output_plan_run_mismatch"):
        PlanningExecutionOutcome(
            status=PlanningExecutionOutcomeStatus.FAILED,
            completed_at=CREATED_AT + timedelta(seconds=5),
            raw_output_observed=False,
            failure_stage=PlanningExecutionFailureStage.PROVIDER_BEFORE_OUTPUT,
            resulting_plan_run_id=PLAN_RUN_ID,
        )


def test_terminal_post_output_failure_links_one_exact_plan_run() -> None:
    failed = completed_outcome(status=PlanningExecutionOutcomeStatus.FAILED)
    observed = execution(
        calls=[
            logical_call(
                physical_attempts=[attempt(1, succeeded=True, plan_run_id=PLAN_RUN_ID)]
            )
        ],
        outcome=failed,
    )
    assert observed.outcome is not None
    assert observed.outcome.resulting_plan_run_id == PLAN_RUN_ID

    with pytest.raises(ValidationError, match="attempt_plan_run_mismatch"):
        execution(
            calls=[
                logical_call(
                    physical_attempts=[
                        attempt(1, succeeded=True, plan_run_id=OTHER_PLAN_RUN_ID)
                    ]
                )
            ],
            outcome=failed,
        )


def test_interrupted_execution_remains_honestly_non_terminal() -> None:
    interrupted = execution(
        calls=[logical_call(physical_attempts=[attempt(1)])],
        outcome=None,
    )
    assert interrupted.outcome is None
    assert "status" not in PlanningExecution.model_fields
    assert "applied_at" not in PlanningExecution.model_fields


@pytest.mark.parametrize(
    ("builder", "error"),
    [
        (
            lambda: logical_call(physical_attempts=[attempt(2)]),
            "physical_attempts_not_contiguous",
        ),
        (
            lambda: logical_call(
                physical_attempts=[attempt(1, logical=logical_identity(stage="epics"))]
            ),
            "physical_hierarchy_mismatch",
        ),
        (
            lambda: execution(
                calls=[logical_call(identity=logical_identity(logical_attempt_no=2))]
            ),
            "logical_attempts_not_contiguous",
        ),
    ],
)
def test_invalid_attempt_hierarchy_or_numbering_is_rejected(
    builder: Any, error: str
) -> None:
    with pytest.raises(ValidationError, match=error):
        builder()


def test_raw_content_credentials_secret_hashes_and_pricing_are_excluded() -> None:
    forbidden_fields = {
        "credential",
        "credentials",
        "mutable_pricing",
        "pricing",
        "prompt",
        "prompt_hash",
        "raw_prompt",
        "raw_provider_payload",
        "raw_response",
        "response_envelope",
        "secret",
        "secret_hash",
    }
    contract_types = (
        PlanningExecution,
        PlannerLogicalCall,
        PlannerPhysicalTransportAttempt,
        PlanningExecutionOutcome,
    )
    for contract_type in contract_types:
        assert forbidden_fields.isdisjoint(contract_type.model_fields)

    with pytest.raises(ValidationError, match="extra_forbidden") as prompt_error:
        PlannerPhysicalTransportAttempt.model_validate(
            attempt(1).model_dump()
            | {"raw_provider_payload": {"secret": "do-not-retain"}}
        )
    assert "do-not-retain" not in str(prompt_error.value)

    with pytest.raises(ValidationError, match="input_name_sensitive"):
        PlannerInputIdentity(
            name="credential-hash",
            algorithm=PlannerDigestAlgorithm.SHA256,
            digest="d" * 64,
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PlannerExecutionParameters.model_validate(
            {"streaming": True, "api_key": "do-not-retain"}
        )

    encoded = execution().canonical_bytes().decode("ascii")
    assert "raw_prompt" not in encoded
    assert "provider_payload" not in encoded
    assert "pricing" not in encoded


def test_plan_run_contract_and_lifecycle_vocabulary_are_unchanged() -> None:
    assert {member.value for member in PlanRunStatus} == {
        "proposed",
        "applied",
        "rejected",
        "failed",
    }
    required_fields = {
        "id",
        "product_id",
        "status",
        "input_doc_shas",
        "model_provider",
        "model_name",
        "prompt_version",
        "prompt_hash",
        "similarity_threshold",
        "raw_output_hash",
        "created_at",
    }
    assert required_fields <= {
        name for name, field in PlanRun.model_fields.items() if field.is_required()
    }
    assert "planning_execution_id" not in PlanRun.model_fields
    assert "running" not in {member.value for member in PlanRunStatus}


def test_contract_instances_are_deeply_immutable() -> None:
    observed = execution()
    with pytest.raises(ValidationError, match="frozen"):
        observed.product_id = OTHER_PLAN_RUN_ID
    with pytest.raises(ValidationError, match="frozen"):
        observed.input_identities[0].name = "replacement"
    assert isinstance(observed.input_identities, tuple)
    assert isinstance(observed.prompt_templates, tuple)
    assert MeasurementAvailability.UNAVAILABLE.value == "unavailable"

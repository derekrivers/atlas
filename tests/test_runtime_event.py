"""ATLAS-265 bounded runtime event envelope contracts."""

from __future__ import annotations

import ast
import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from atlas.core.models.runtime_event import (
    APPROVED_RUNTIME_METADATA_FIELDS,
    MAX_RUNTIME_COORDINATION_IDS,
    MAX_RUNTIME_COUNTER,
    MAX_RUNTIME_METADATA_SET_ITEMS,
    MAX_RUNTIME_TOUCHED_PATHS,
    RUNTIME_EVENT_SCHEMA_VERSION,
    RUNTIME_TRANSPORT_EVENT_SCHEMA_VERSION,
    RuntimeEvent,
    RuntimeEventFamily,
    RuntimePhaseClassification,
    RuntimeTransportEvent,
)

ATTEMPT_ID = UUID("11111111-1111-4111-8111-111111111111")
EVENT_ID = UUID("22222222-2222-4222-8222-222222222222")
PRODUCT_ID = UUID("33333333-3333-4333-8333-333333333333")
TICKET_ID = UUID("44444444-4444-4444-8444-444444444444")
AGENT_RUN_ID = UUID("55555555-5555-4555-8555-555555555555")
NOW = datetime(2026, 8, 20, 9, 30, tzinfo=UTC)
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
HEAD_SHA = "d" * 40
BASE_SHA = "e" * 40


def transport_kwargs() -> dict[str, Any]:
    return {
        "schema_version": RUNTIME_TRANSPORT_EVENT_SCHEMA_VERSION,
        "product_scope": "atlas",
        "external_issue_id": "2ac97f48-9d14-4021-b243-707fdbf5d4d5",
        "issue_identifier": "ATL-454",
        "runtime_attempt_id": ATTEMPT_ID,
        "codex_thread_id": "thread-123",
        "codex_turn_id": "turn-456",
        "session_id": "thread-123-turn-456",
        "source_sequence_no": 7,
        "observed_at": NOW,
        "source_descriptor_fingerprint": DIGEST_A,
        "event_family": RuntimeEventFamily.OPERATION_COMPLETED.value,
        "operation_kind": "shell_command",
        "operation_identity_hash": DIGEST_B,
        "result_class": "succeeded",
        "duration_ms": 250,
        "exit_code_class": "zero",
        "touched_paths": [
            "tests/test_runtime_event.py",
            "atlas/core/models/runtime_event.py",
        ],
        "head_sha": HEAD_SHA,
        "base_sha": BASE_SHA,
        "role_id": "implementation-executor",
        "topology_id": "baseline-single-role",
        "interface_ids": ["runtime-event-v1", "source-event-key-v1"],
        "artifact_ids": ["validation-plan-1", "diff-proof-1"],
        "peer_role_id": "runtime-observer",
        "bounded_metadata": {
            "raw_method_name": "item/completed",
            "token_count": 42,
            "retry_ordinal": 0,
            "validation_profile_ids": ["python-runtime", "contract-tests"],
        },
        "payload_digest": DIGEST_C,
    }


def canonical_kwargs() -> dict[str, Any]:
    source = transport_kwargs()
    source.pop("product_scope")
    source.pop("issue_identifier")
    source["schema_version"] = RUNTIME_EVENT_SCHEMA_VERSION
    return {
        "id": EVENT_ID,
        "product_id": PRODUCT_ID,
        "ticket_id": TICKET_ID,
        "agent_run_id": AGENT_RUN_ID,
        "phase_classification": RuntimePhaseClassification.VALIDATE.value,
        **source,
    }


def transport(**overrides: Any) -> RuntimeTransportEvent:
    return RuntimeTransportEvent(**(transport_kwargs() | overrides))


def canonical(**overrides: Any) -> RuntimeEvent:
    return RuntimeEvent(**(canonical_kwargs() | overrides))


def test_envelopes_preserve_exact_source_and_atlas_identity_surfaces() -> None:
    assert list(RuntimeTransportEvent.model_fields) == [
        "schema_version",
        "product_scope",
        "external_issue_id",
        "issue_identifier",
        "runtime_attempt_id",
        "codex_thread_id",
        "codex_turn_id",
        "session_id",
        "source_sequence_no",
        "observed_at",
        "source_descriptor_fingerprint",
        "event_family",
        "operation_kind",
        "operation_identity_hash",
        "result_class",
        "duration_ms",
        "exit_code_class",
        "touched_paths",
        "head_sha",
        "base_sha",
        "role_id",
        "topology_id",
        "interface_ids",
        "artifact_ids",
        "peer_role_id",
        "bounded_metadata",
        "payload_digest",
    ]
    assert list(RuntimeEvent.model_fields) == [
        "id",
        "schema_version",
        "product_id",
        "ticket_id",
        "external_issue_id",
        "agent_run_id",
        "runtime_attempt_id",
        "codex_thread_id",
        "codex_turn_id",
        "session_id",
        "source_sequence_no",
        "observed_at",
        "source_descriptor_fingerprint",
        "event_family",
        "operation_kind",
        "operation_identity_hash",
        "phase_classification",
        "result_class",
        "duration_ms",
        "exit_code_class",
        "touched_paths",
        "head_sha",
        "base_sha",
        "role_id",
        "topology_id",
        "interface_ids",
        "artifact_ids",
        "peer_role_id",
        "bounded_metadata",
        "payload_digest",
    ]

    source = transport()
    event = canonical()
    assert source.schema_version == RUNTIME_TRANSPORT_EVENT_SCHEMA_VERSION
    assert event.schema_version == RUNTIME_EVENT_SCHEMA_VERSION
    assert source.source_event_key == (ATTEMPT_ID, 7)
    assert event.source_event_key == source.source_event_key
    assert source.event_family is RuntimeEventFamily.OPERATION_COMPLETED
    assert event.phase_classification is RuntimePhaseClassification.VALIDATE


def test_envelopes_are_deeply_immutable_and_reject_extra_fields() -> None:
    raw_paths = ["b.py", "a.py"]
    source = transport(touched_paths=raw_paths)
    raw_paths.append("later.py")
    assert source.touched_paths == ("a.py", "b.py")

    with pytest.raises(ValidationError, match="frozen"):
        source.operation_kind = "provider_call"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        transport(product_id=PRODUCT_ID)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        canonical(product_scope="atlas")


def test_event_family_and_phase_vocabularies_are_exact() -> None:
    assert {member.value for member in RuntimeEventFamily} == {
        "run_started",
        "run_completed",
        "run_failed",
        "session_started",
        "turn_completed",
        "turn_failed",
        "turn_cancelled",
        "turn_input_required",
        "operation_started",
        "operation_completed",
        "operation_failed",
        "tool_call_requested",
        "tool_call_completed",
        "tool_call_failed",
        "approval_required",
        "approval_auto_resolved",
        "notification",
        "malformed_protocol_message",
        "role_started",
        "role_completed",
        "handoff_created",
        "handoff_consumed",
        "artifact_read",
        "artifact_written",
        "interface_consumed",
        "interface_changed",
        "interface_decision",
        "peer_message",
        "capability_requested",
        "capability_allowed",
        "capability_denied",
        "steering_requested",
        "steering_applied",
        "steering_rejected_stale",
        "steering_indeterminate",
    }
    assert {member.value for member in RuntimePhaseClassification} == {
        "understand",
        "localise",
        "reproduce",
        "patch",
        "validate",
        "integrate",
        "handoff",
        "unknown",
    }
    assert canonical(phase_classification="unknown").phase_classification is (
        RuntimePhaseClassification.UNKNOWN
    )

    with pytest.raises(ValidationError, match="event_family"):
        transport(event_family="codex/item/completed")
    with pytest.raises(ValidationError, match="phase_classification"):
        canonical(phase_classification="THINKING")
    with pytest.raises(ValidationError, match="schema_version"):
        transport(schema_version="runtime-transport-event/v2")
    with pytest.raises(ValidationError, match="schema_version"):
        canonical(schema_version="runtime-event/v2")


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("product_scope", "Atlas Product", "runtime_event_product_scope_invalid"),
        ("external_issue_id", "issue id", "runtime_event_external_issue_id_invalid"),
        ("issue_identifier", "atl-454", "runtime_event_issue_identifier_invalid"),
        ("runtime_attempt_id", "not-a-uuid", "runtime_attempt_id"),
        ("source_sequence_no", 0, "source_sequence_no"),
        ("source_sequence_no", True, "source_sequence_no"),
        ("source_sequence_no", "7", "source_sequence_no"),
        ("source_sequence_no", MAX_RUNTIME_COUNTER + 1, "source_sequence_no"),
        ("observed_at", datetime(2026, 8, 20, 9, 30), "timezone_required"),
        ("source_descriptor_fingerprint", "a" * 63, "fingerprint_invalid"),
        ("operation_kind", "raw method", "operation_kind_invalid"),
        ("operation_identity_hash", "z" * 64, "identity_hash_invalid"),
        ("duration_ms", -1, "duration_ms"),
        ("duration_ms", True, "duration_ms"),
        ("exit_code_class", "Exit 0", "exit_code_class_invalid"),
        ("head_sha", "a" * 39, "head_sha_invalid"),
        ("base_sha", "g" * 40, "base_sha_invalid"),
        ("payload_digest", "sha256:" + DIGEST_C, "payload_digest_invalid"),
    ],
    ids=[
        "product-scope",
        "external-issue",
        "issue-identifier",
        "attempt-uuid",
        "sequence-zero",
        "sequence-bool",
        "sequence-string",
        "sequence-overflow",
        "naive-time",
        "descriptor-digest",
        "operation-kind",
        "operation-hash",
        "negative-duration",
        "duration-bool",
        "exit-class",
        "head-sha",
        "base-sha",
        "payload-digest",
    ],
)
def test_transport_rejects_malformed_or_out_of_bound_scalars(
    field: str, value: Any, error: str
) -> None:
    with pytest.raises(ValidationError, match=error):
        transport(**{field: value})


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        (
            {"codex_thread_id": None, "codex_turn_id": "turn-456"},
            "turn_requires_thread",
        ),
        ({"head_sha": None, "base_sha": BASE_SHA}, "base_sha_requires_head_sha"),
        ({"role_id": None, "peer_role_id": "reviewer"}, "peer_role_requires_role"),
        (
            {
                "role_id": "implementation-executor",
                "peer_role_id": "implementation-executor",
            },
            "peer_role_must_differ",
        ),
    ],
    ids=["turn-without-thread", "base-without-head", "peer-without-role", "self-peer"],
)
def test_both_envelopes_reject_contradictory_identity_combinations(
    overrides: dict[str, Any], error: str
) -> None:
    with pytest.raises(ValidationError, match=error):
        transport(**overrides)
    with pytest.raises(ValidationError, match=error):
        canonical(**overrides)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("touched_paths", "a.py", "touched_paths_collection_invalid"),
        ("touched_paths", ["/workspace/secret.py"], "touched_paths_item_invalid"),
        ("touched_paths", ["../escape.py"], "touched_paths_item_invalid"),
        ("touched_paths", ["a\\b.py"], "touched_paths_item_invalid"),
        (
            "touched_paths",
            [f"path-{index}.py" for index in range(MAX_RUNTIME_TOUCHED_PATHS + 1)],
            "touched_paths_collection_out_of_bounds",
        ),
        ("interface_ids", "interface-1", "interface_ids_collection_invalid"),
        (
            "artifact_ids",
            [f"artifact-{index}" for index in range(MAX_RUNTIME_COORDINATION_IDS + 1)],
            "artifact_ids_collection_out_of_bounds",
        ),
    ],
    ids=[
        "path-not-list",
        "absolute-workspace-path",
        "path-traversal",
        "backslash-path",
        "path-count",
        "interface-not-list",
        "artifact-count",
    ],
)
def test_semantic_sets_reject_unbounded_or_unsafe_inputs(
    field: str, value: Any, error: str
) -> None:
    with pytest.raises(ValidationError, match=error):
        transport(**{field: value})


def test_metadata_is_an_allowlisted_bounded_non_payload_map() -> None:
    assert {
        "raw_method_name",
        "tool_name",
        "capability_name",
        "error_class",
        "failure_class",
        "token_delta",
        "token_count",
        "retry_ordinal",
        "attempt_ordinal",
        "worker_host_id",
        "validation_profile_ids",
        "policy_decision_ids",
        "effect_decision_ids",
        "interface_ids",
        "handoff_ids",
    } <= APPROVED_RUNTIME_METADATA_FIELDS

    observed = transport(
        bounded_metadata={
            "handoff_ids": ["handoff-b", "handoff-a", "handoff-a"],
            "raw_method_name": "item/completed",
            "token_delta": 10,
        }
    )
    assert observed.bounded_metadata == {
        "handoff_ids": ("handoff-a", "handoff-b"),
        "raw_method_name": "item/completed",
        "token_delta": 10,
    }

    forbidden = {
        "transcript": "secret",
        "credentials": "token",
        "raw_payload": {"provider": "body"},
        "workspace_path": "/root/code/atlas",
        "prompt": "hidden evaluation",
        "command_output": "raw output",
        "peer_message_text": "message body",
        "environment": "KEY=value",
    }
    for key, value in forbidden.items():
        with pytest.raises(ValidationError, match="field_forbidden"):
            transport(bounded_metadata={key: value})

    with pytest.raises(ValidationError, match="token_count_invalid"):
        transport(bounded_metadata={"token_count": True})
    with pytest.raises(ValidationError, match="token_delta_invalid"):
        transport(bounded_metadata={"token_delta": -1})
    with pytest.raises(ValidationError, match="collection_out_of_bounds"):
        transport(
            bounded_metadata={
                "interface_ids": [
                    f"interface-{index}"
                    for index in range(MAX_RUNTIME_METADATA_SET_ITEMS + 1)
                ]
            }
        )


def test_forbidden_payload_and_fingerprint_fields_are_absent() -> None:
    forbidden = {
        "transcript",
        "secret",
        "credentials",
        "raw_payload",
        "raw_command_output",
        "prompt",
        "response",
        "workspace_path",
        "environment",
        "provider_payload",
        "peer_message_text",
        "transport_fingerprint",
        "canonical_fingerprint",
    }
    assert forbidden.isdisjoint(RuntimeTransportEvent.model_fields)
    assert forbidden.isdisjoint(RuntimeEvent.model_fields)

    with pytest.raises(ValidationError, match="extra_forbidden"):
        transport(transcript="do not retain")
    with pytest.raises(ValidationError, match="extra_forbidden"):
        canonical(raw_payload={"method": "item/completed"})
    with pytest.raises(ValidationError, match="extra_forbidden"):
        transport(transport_fingerprint="f" * 64)


def test_transport_canonical_serialization_and_fingerprint_are_stable() -> None:
    baseline = transport()
    reordered = transport(
        observed_at=datetime(
            2026,
            8,
            20,
            11,
            30,
            tzinfo=timezone(timedelta(hours=2)),
        ),
        source_descriptor_fingerprint=DIGEST_A.upper(),
        operation_identity_hash=DIGEST_B.upper(),
        payload_digest=DIGEST_C.upper(),
        head_sha=HEAD_SHA.upper(),
        base_sha=BASE_SHA.upper(),
        touched_paths=[
            "atlas/core/models/runtime_event.py",
            "tests/test_runtime_event.py",
            "atlas/core/models/runtime_event.py",
        ],
        interface_ids=["source-event-key-v1", "runtime-event-v1"],
        artifact_ids=["diff-proof-1", "validation-plan-1", "diff-proof-1"],
        bounded_metadata={
            "validation_profile_ids": ["contract-tests", "python-runtime"],
            "retry_ordinal": 0,
            "token_count": 42,
            "raw_method_name": "item/completed",
        },
    )

    assert baseline == reordered
    assert baseline.canonical_bytes() == reordered.canonical_bytes()
    assert baseline.transport_fingerprint == reordered.transport_fingerprint
    assert (
        baseline.transport_fingerprint
        == hashlib.sha256(baseline.canonical_bytes()).hexdigest()
    )
    assert len(baseline.transport_fingerprint) == 64

    payload = json.loads(baseline.canonical_bytes())
    assert payload["observed_at"] == "2026-08-20T09:30:00Z"
    assert payload["touched_paths"] == sorted(set(transport_kwargs()["touched_paths"]))
    assert "transport_fingerprint" not in payload
    assert "canonical_fingerprint" not in payload


def test_canonical_event_fingerprint_is_stable_and_atlas_identity_sensitive() -> None:
    baseline = canonical()
    reordered = canonical(
        observed_at=datetime(
            2026,
            8,
            20,
            10,
            30,
            tzinfo=timezone(timedelta(hours=1)),
        ),
        touched_paths=list(reversed(transport_kwargs()["touched_paths"])),
        interface_ids=list(reversed(transport_kwargs()["interface_ids"])),
        artifact_ids=list(reversed(transport_kwargs()["artifact_ids"])),
        bounded_metadata=dict(
            reversed(list(transport_kwargs()["bounded_metadata"].items()))
        ),
    )
    assert baseline == reordered
    assert baseline.canonical_bytes() == reordered.canonical_bytes()
    assert baseline.canonical_fingerprint == reordered.canonical_fingerprint
    assert (
        baseline.canonical_fingerprint
        == hashlib.sha256(baseline.canonical_bytes()).hexdigest()
    )
    assert canonical(product_id=UUID(int=9)).canonical_fingerprint != (
        baseline.canonical_fingerprint
    )
    assert canonical(phase_classification="unknown").canonical_fingerprint != (
        baseline.canonical_fingerprint
    )
    assert baseline.canonical_fingerprint != transport().transport_fingerprint


def test_contract_module_is_isolated_without_activation_or_export() -> None:
    repository_root = Path(__file__).resolve().parent.parent
    module_path = repository_root / "atlas/core/models/runtime_event.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(
                alias.name.split(".", maxsplit=1)[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_roots.add(node.module.split(".", maxsplit=1)[0])

    assert imported_roots == {
        "__future__",
        "datetime",
        "enum",
        "hashlib",
        "json",
        "pathlib",
        "pydantic",
        "re",
        "typing",
        "uuid",
    }
    assert RuntimeTransportEvent.__module__ == "atlas.core.models.runtime_event"
    assert RuntimeEvent.__module__ == "atlas.core.models.runtime_event"

    package_init = (repository_root / "atlas/core/models/__init__.py").read_text(
        encoding="utf-8"
    )
    assert "runtime_event" not in package_init
    forbidden_imports = {
        "atlas.storage",
        "atlas.linear",
        "atlas.github",
        "atlas.orchestration",
        "subprocess",
        "socket",
    }
    assert forbidden_imports.isdisjoint(
        {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
    )

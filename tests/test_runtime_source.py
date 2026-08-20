"""ATLAS-264 immutable runtime source identity contract."""

from __future__ import annotations

import ast
import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from atlas.core.models.runtime_source import (
    RUNTIME_SOURCE_SCHEMA_VERSION,
    RuntimeCapabilitySupport,
    RuntimeCoordinationChannel,
    RuntimeCoordinationChannelCapability,
    RuntimeEventFamily,
    RuntimeEventFamilyCapability,
    RuntimeIdentityField,
    RuntimeIdentityFieldCapability,
    RuntimeProvider,
    RuntimeSourceDescriptor,
    RuntimeSourceSequenceSemantics,
)

NOW = datetime(2026, 8, 20, 9, 30, tzinfo=UTC)
SHA_A = "a" * 40
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64


def event_capabilities() -> list[dict[str, str]]:
    return [
        {
            "event_family": family.value,
            "support": (
                RuntimeCapabilitySupport.UNINSTRUMENTED.value
                if family is RuntimeEventFamily.PEER_MESSAGE
                else RuntimeCapabilitySupport.UNSUPPORTED.value
                if family is RuntimeEventFamily.ROLE_STARTED
                else RuntimeCapabilitySupport.SUPPORTED.value
            ),
        }
        for family in RuntimeEventFamily
    ]


def identity_capabilities() -> list[dict[str, str]]:
    return [
        {
            "identity_field": identity.value,
            "support": (
                RuntimeCapabilitySupport.SUPPORTED.value
                if identity
                in {
                    RuntimeIdentityField.EXTERNAL_ISSUE_ID,
                    RuntimeIdentityField.RUNTIME_ATTEMPT_ID,
                    RuntimeIdentityField.SOURCE_SEQUENCE_NO,
                }
                else RuntimeCapabilitySupport.UNINSTRUMENTED.value
                if identity is RuntimeIdentityField.CODEX_TURN_ID
                else RuntimeCapabilitySupport.UNSUPPORTED.value
            ),
        }
        for identity in RuntimeIdentityField
    ]


def coordination_capabilities() -> list[dict[str, str]]:
    return [
        {
            "coordination_channel": channel.value,
            "support": (
                RuntimeCapabilitySupport.UNINSTRUMENTED.value
                if channel is RuntimeCoordinationChannel.PEER_MESSAGE
                else RuntimeCapabilitySupport.UNSUPPORTED.value
                if channel is RuntimeCoordinationChannel.INTERFACE
                else RuntimeCapabilitySupport.SUPPORTED.value
            ),
        }
        for channel in RuntimeCoordinationChannel
    ]


def descriptor_kwargs() -> dict[str, Any]:
    return {
        "source_id": "atlas-runtime-primary",
        "schema_version": RUNTIME_SOURCE_SCHEMA_VERSION,
        "product_scope": "atlas",
        "runtime_provider": "symphony_codex_app_server",
        "symphony_release_sha": SHA_A,
        "event_projector_version": "runtime-projector/v1",
        "codex_cli_version": "0.124.0",
        "codex_protocol_fingerprint": DIGEST_B,
        "source_sequence_semantics": ("monotonic_per_runtime_attempt_starting_at_one"),
        "supported_event_families": event_capabilities(),
        "supported_identity_fields": identity_capabilities(),
        "supported_coordination_channels": coordination_capabilities(),
        "advertised_dynamic_tool_fingerprint": DIGEST_C,
        "mcp_inventory_fingerprint": DIGEST_D,
        "governed_channel_inventory_fingerprint": DIGEST_E,
        "created_at": NOW,
    }


def descriptor(**overrides: Any) -> RuntimeSourceDescriptor:
    return RuntimeSourceDescriptor(**(descriptor_kwargs() | overrides))


def replace_capability_support(
    capabilities: list[dict[str, str]], identity_key: str, identity: str, support: str
) -> list[dict[str, str]]:
    return [
        item | {"support": support} if item[identity_key] == identity else item
        for item in capabilities
    ]


def test_descriptor_has_every_section_8_field_and_optional_inventory_digest() -> None:
    assert list(RuntimeSourceDescriptor.model_fields) == [
        "source_id",
        "schema_version",
        "product_scope",
        "runtime_provider",
        "symphony_release_sha",
        "event_projector_version",
        "codex_cli_version",
        "codex_protocol_fingerprint",
        "source_sequence_semantics",
        "supported_event_families",
        "supported_identity_fields",
        "supported_coordination_channels",
        "advertised_dynamic_tool_fingerprint",
        "mcp_inventory_fingerprint",
        "governed_channel_inventory_fingerprint",
        "created_at",
    ]

    observed = descriptor(
        advertised_dynamic_tool_fingerprint=None,
        mcp_inventory_fingerprint=None,
        governed_channel_inventory_fingerprint=None,
    )

    assert observed.schema_version == RUNTIME_SOURCE_SCHEMA_VERSION
    assert observed.runtime_provider is RuntimeProvider.SYMPHONY_CODEX_APP_SERVER
    assert observed.source_sequence_semantics is (
        RuntimeSourceSequenceSemantics.MONOTONIC_PER_RUNTIME_ATTEMPT_STARTING_AT_ONE
    )
    assert observed.advertised_dynamic_tool_fingerprint is None
    assert observed.mcp_inventory_fingerprint is None
    assert observed.governed_channel_inventory_fingerprint is None
    assert len(observed.fingerprint) == 64


def test_descriptor_and_nested_capabilities_are_deeply_immutable() -> None:
    raw_events = event_capabilities()
    observed = descriptor(supported_event_families=raw_events)

    raw_events[0]["support"] = RuntimeCapabilitySupport.UNSUPPORTED.value
    assert observed.supported_event_families[0].support is (
        RuntimeCapabilitySupport.SUPPORTED
    )

    with pytest.raises(ValidationError, match="frozen"):
        observed.product_scope = "another-product"
    with pytest.raises(ValidationError, match="frozen"):
        observed.supported_event_families[
            0
        ].support = RuntimeCapabilitySupport.UNSUPPORTED
    with pytest.raises(ValidationError, match="extra_forbidden"):
        RuntimeSourceDescriptor(**(descriptor_kwargs() | {"raw_config": "secret"}))


def test_every_closed_vocabulary_is_exact() -> None:
    assert {member.value for member in RuntimeProvider} == {"symphony_codex_app_server"}
    assert {member.value for member in RuntimeSourceSequenceSemantics} == {
        "monotonic_per_runtime_attempt_starting_at_one"
    }
    assert {member.value for member in RuntimeCapabilitySupport} == {
        "supported",
        "unsupported",
        "uninstrumented",
    }
    assert {member.value for member in RuntimeIdentityField} == {
        "product_id",
        "atlas_ticket_id",
        "external_issue_id",
        "agent_run_id",
        "runtime_attempt_id",
        "codex_thread_id",
        "codex_turn_id",
        "session_id",
        "source_sequence_no",
    }
    assert {member.value for member in RuntimeCoordinationChannel} == {
        "typed_handoff",
        "artifact",
        "interface",
        "peer_message",
    }
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


@pytest.mark.parametrize(
    ("override", "error"),
    [
        ({"runtime_provider": "other_runtime"}, "runtime_provider"),
        ({"source_sequence_semantics": "arrival_order"}, "source_sequence_semantics"),
        ({"schema_version": "runtime-source-descriptor/v2"}, "schema_version"),
    ],
    ids=["runtime-provider", "sequence-semantics", "schema-version"],
)
def test_closed_descriptor_values_reject_extensions(
    override: dict[str, str], error: str
) -> None:
    with pytest.raises(ValidationError, match=error):
        descriptor(**override)


@pytest.mark.parametrize(
    ("factory", "override", "error"),
    [
        (
            RuntimeEventFamilyCapability,
            {"event_family": "raw_transcript", "support": "supported"},
            "event_family",
        ),
        (
            RuntimeIdentityFieldCapability,
            {"identity_field": "hostname", "support": "supported"},
            "identity_field",
        ),
        (
            RuntimeCoordinationChannelCapability,
            {"coordination_channel": "generic_chat", "support": "supported"},
            "coordination_channel",
        ),
        (
            RuntimeEventFamilyCapability,
            {"event_family": "run_started", "support": "assumed"},
            "support",
        ),
    ],
    ids=["event-family", "identity-field", "coordination-channel", "support"],
)
def test_closed_capability_values_reject_extensions(
    factory: type[BaseModel],
    override: dict[str, str],
    error: str,
) -> None:
    with pytest.raises(ValidationError, match=error):
        factory(**override)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("source_id", "Atlas Runtime", "runtime_source_source_id_invalid"),
        ("source_id", "a" * 129, "runtime_source_source_id_invalid"),
        ("product_scope", "atlas/runtime", "runtime_source_product_scope_invalid"),
        (
            "symphony_release_sha",
            "a" * 39,
            "runtime_source_symphony_release_sha_invalid",
        ),
        (
            "symphony_release_sha",
            "z" * 40,
            "runtime_source_symphony_release_sha_invalid",
        ),
        (
            "event_projector_version",
            "runtime projector v1",
            "runtime_source_event_projector_version_invalid",
        ),
        (
            "event_projector_version",
            "v" * 129,
            "runtime_source_event_projector_version_invalid",
        ),
        (
            "codex_cli_version",
            "",
            "runtime_source_codex_cli_version_invalid",
        ),
        (
            "codex_protocol_fingerprint",
            "b" * 63,
            "runtime_source_codex_protocol_fingerprint_invalid",
        ),
        (
            "advertised_dynamic_tool_fingerprint",
            "sha256:" + DIGEST_C,
            "runtime_source_advertised_dynamic_tool_fingerprint_invalid",
        ),
        (
            "mcp_inventory_fingerprint",
            "g" * 64,
            "runtime_source_mcp_inventory_fingerprint_invalid",
        ),
        (
            "governed_channel_inventory_fingerprint",
            "d" * 65,
            "runtime_source_governed_channel_inventory_fingerprint_invalid",
        ),
        (
            "created_at",
            datetime(2026, 8, 20, 9, 30),
            "runtime_source_created_at_timezone_required",
        ),
    ],
    ids=[
        "source-id-character",
        "source-id-length",
        "product-scope-character",
        "release-sha-length",
        "release-sha-hex",
        "projector-version-character",
        "projector-version-length",
        "codex-version-empty",
        "protocol-digest-length",
        "dynamic-tool-digest-prefix",
        "mcp-digest-hex",
        "channel-digest-length",
        "created-at-naive",
    ],
)
def test_malformed_identifiers_versions_digests_and_time_are_named(
    field: str, value: Any, error: str
) -> None:
    with pytest.raises(ValidationError, match=error):
        descriptor(**{field: value})


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        (
            "supported_event_families",
            [],
            "runtime_source_supported_event_families_list_out_of_bounds",
        ),
        (
            "supported_event_families",
            [*event_capabilities(), event_capabilities()[0]],
            "runtime_source_supported_event_families_list_out_of_bounds",
        ),
        (
            "supported_identity_fields",
            "runtime_attempt_id",
            "runtime_source_supported_identity_fields_list_invalid",
        ),
        (
            "supported_coordination_channels",
            {},
            "runtime_source_supported_coordination_channels_list_invalid",
        ),
    ],
    ids=["empty", "oversized", "identity-not-list", "coordination-not-list"],
)
def test_capability_lists_reject_unbounded_or_non_list_inputs(
    field: str, value: Any, error: str
) -> None:
    with pytest.raises(ValidationError, match=error):
        descriptor(**{field: value})


def test_capability_sets_reject_missing_and_duplicate_identities() -> None:
    incomplete_events = event_capabilities()[:-1]
    with pytest.raises(
        ValidationError,
        match="runtime_source_supported_event_families_incomplete:steering_indeterminate",
    ):
        descriptor(supported_event_families=incomplete_events)

    duplicate_events = [*event_capabilities()[:-1], event_capabilities()[0]]
    with pytest.raises(
        ValidationError,
        match="runtime_source_supported_event_families_duplicate:run_started",
    ):
        descriptor(supported_event_families=duplicate_events)

    duplicate_identities = [
        *identity_capabilities()[:-1],
        identity_capabilities()[0],
    ]
    with pytest.raises(
        ValidationError,
        match="runtime_source_supported_identity_fields_duplicate:product_id",
    ):
        descriptor(supported_identity_fields=duplicate_identities)

    duplicate_channels = [
        *coordination_capabilities()[:-1],
        coordination_capabilities()[0],
    ]
    with pytest.raises(
        ValidationError,
        match="runtime_source_supported_coordination_channels_duplicate:typed_handoff",
    ):
        descriptor(supported_coordination_channels=duplicate_channels)


def test_unsupported_and_uninstrumented_capabilities_are_explicit_not_silent() -> None:
    observed = descriptor()
    events = {
        item.event_family: item.support for item in observed.supported_event_families
    }
    identities = {
        item.identity_field: item.support for item in observed.supported_identity_fields
    }
    channels = {
        item.coordination_channel: item.support
        for item in observed.supported_coordination_channels
    }

    assert events[RuntimeEventFamily.ROLE_STARTED] is (
        RuntimeCapabilitySupport.UNSUPPORTED
    )
    assert identities[RuntimeIdentityField.CODEX_TURN_ID] is (
        RuntimeCapabilitySupport.UNINSTRUMENTED
    )
    assert channels[RuntimeCoordinationChannel.PEER_MESSAGE] is (
        RuntimeCapabilitySupport.UNINSTRUMENTED
    )
    assert set(events) == set(RuntimeEventFamily)
    assert set(identities) == set(RuntimeIdentityField)
    assert set(channels) == set(RuntimeCoordinationChannel)


def test_contradictory_required_identity_and_peer_message_claims_are_rejected() -> None:
    unsupported_attempt = replace_capability_support(
        identity_capabilities(),
        "identity_field",
        RuntimeIdentityField.RUNTIME_ATTEMPT_ID.value,
        RuntimeCapabilitySupport.UNINSTRUMENTED.value,
    )
    with pytest.raises(
        ValidationError,
        match="runtime_source_required_identity_not_supported:runtime_attempt_id",
    ):
        descriptor(supported_identity_fields=unsupported_attempt)

    peer_event_supported = replace_capability_support(
        event_capabilities(),
        "event_family",
        RuntimeEventFamily.PEER_MESSAGE.value,
        RuntimeCapabilitySupport.SUPPORTED.value,
    )
    with pytest.raises(
        ValidationError, match="runtime_source_peer_message_capability_conflict"
    ):
        descriptor(supported_event_families=peer_event_supported)

    no_supported_events = [
        item | {"support": RuntimeCapabilitySupport.UNSUPPORTED.value}
        for item in event_capabilities()
    ]
    peer_channel_unsupported = replace_capability_support(
        coordination_capabilities(),
        "coordination_channel",
        RuntimeCoordinationChannel.PEER_MESSAGE.value,
        RuntimeCapabilitySupport.UNSUPPORTED.value,
    )
    with pytest.raises(
        ValidationError, match="runtime_source_event_family_support_empty"
    ):
        descriptor(
            supported_event_families=no_supported_events,
            supported_coordination_channels=peer_channel_unsupported,
        )


def test_canonical_bytes_and_fingerprint_are_order_and_input_form_independent() -> None:
    baseline = descriptor()
    reordered = descriptor(
        symphony_release_sha=SHA_A.upper(),
        codex_protocol_fingerprint=DIGEST_B.upper(),
        advertised_dynamic_tool_fingerprint=DIGEST_C.upper(),
        mcp_inventory_fingerprint=DIGEST_D.upper(),
        governed_channel_inventory_fingerprint=DIGEST_E.upper(),
        supported_event_families=list(reversed(event_capabilities())),
        supported_identity_fields=list(reversed(identity_capabilities())),
        supported_coordination_channels=list(reversed(coordination_capabilities())),
        created_at=datetime(
            2026,
            8,
            20,
            11,
            30,
            tzinfo=timezone(timedelta(hours=2)),
        ),
    )

    assert baseline == reordered
    assert baseline.canonical_bytes() == reordered.canonical_bytes()
    assert baseline.fingerprint == reordered.fingerprint
    assert (
        baseline.fingerprint == hashlib.sha256(baseline.canonical_bytes()).hexdigest()
    )
    assert baseline.fingerprint == (
        "4a66a76b4d088fb00ecbdbc83242a8c6d79d61300a98caf0665a2bf9bf118b5d"
    )
    assert len(baseline.fingerprint) == 64

    payload = json.loads(baseline.canonical_bytes())
    assert payload["created_at"] == "2026-08-20T09:30:00Z"
    assert "fingerprint" not in payload
    assert [
        item["event_family"] for item in payload["supported_event_families"]
    ] == sorted(member.value for member in RuntimeEventFamily)


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("source_id", "atlas-runtime-secondary"),
        ("product_scope", "atlas-staging"),
        ("symphony_release_sha", "f" * 40),
        ("event_projector_version", "runtime-projector/v2"),
        ("codex_cli_version", "0.125.0"),
        ("codex_protocol_fingerprint", "1" * 64),
        ("advertised_dynamic_tool_fingerprint", None),
        ("mcp_inventory_fingerprint", None),
        ("governed_channel_inventory_fingerprint", None),
        ("created_at", datetime(2026, 8, 20, 9, 30, 1, tzinfo=UTC)),
    ],
    ids=[
        "source",
        "product",
        "symphony-release",
        "projector",
        "codex-cli",
        "codex-protocol",
        "dynamic-tools",
        "mcp-inventory",
        "governed-channels",
        "creation-time",
    ],
)
def test_each_material_scalar_identity_changes_the_fingerprint(
    field: str, changed: Any
) -> None:
    assert descriptor(**{field: changed}).fingerprint != descriptor().fingerprint


def test_each_material_capability_set_changes_the_fingerprint() -> None:
    baseline = descriptor()
    changed_events = replace_capability_support(
        event_capabilities(),
        "event_family",
        RuntimeEventFamily.NOTIFICATION.value,
        RuntimeCapabilitySupport.UNSUPPORTED.value,
    )
    changed_identities = replace_capability_support(
        identity_capabilities(),
        "identity_field",
        RuntimeIdentityField.PRODUCT_ID.value,
        RuntimeCapabilitySupport.UNINSTRUMENTED.value,
    )
    changed_channels = replace_capability_support(
        coordination_capabilities(),
        "coordination_channel",
        RuntimeCoordinationChannel.INTERFACE.value,
        RuntimeCapabilitySupport.UNINSTRUMENTED.value,
    )

    assert descriptor(supported_event_families=changed_events).fingerprint != (
        baseline.fingerprint
    )
    assert descriptor(supported_identity_fields=changed_identities).fingerprint != (
        baseline.fingerprint
    )
    assert descriptor(supported_coordination_channels=changed_channels).fingerprint != (
        baseline.fingerprint
    )


def test_contract_module_imports_only_stdlib_and_pydantic_primitives() -> None:
    module_path = (
        Path(__file__).resolve().parent.parent / "atlas/core/models/runtime_source.py"
    )
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
        "collections",
        "datetime",
        "enum",
        "hashlib",
        "json",
        "pydantic",
        "re",
        "typing",
    }
    assert RuntimeSourceDescriptor.__module__ == "atlas.core.models.runtime_source"

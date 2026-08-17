#!/usr/bin/env python3
"""Validate the operator-owned Phase 15 Symphony ceiling ramp receipts.

This harness is intentionally read-only.  It accepts a predeclared workload
manifest and zero or more operator receipts, validates the cumulative
1 -> 3 -> 5 -> 7 -> 10 sequence, and emits a selected-field report.  It does
not observe or mutate Git, GitHub, Linear, Symphony, CI, policy or workers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from atlas.core.models.delivery_admission_policy import DeliveryAdmissionPolicySpec

MILESTONE_BRANCH = "phase-15-atlas-253-ceiling-ramp"
GATE_LEVELS = (1, 3, 5, 7, 10)
MANIFEST_SCHEMA = "phase-15-ramp-workload-v1"
RECEIPT_SCHEMA = "phase-15-ramp-gate-receipt-v1"
MAX_MANIFEST_BYTES = 60 * 1024
MAX_RECEIPT_BYTES = 60 * 1024
MAX_REPORT_BYTES = 128 * 1024
EXPECTED_WINDOW_SECONDS = 60 * 60
PHASE_15_5_HEAD = "a598798c1a6c5cabe4c80c0f04020c271f438de1"
VALIDATION_SCOPE = "offline-read-only"
FIXTURE_RUNTIME_PROCEDURE = "fixture-only-no-live-runtime-v1"
PROTECTED_LANE_REGISTRY_VERSION = "protected-integration-lanes/v1"

COMMON_INVARIANTS = (
    "configured_capacity_bound",
    "atlas_working_budget_bound",
    "ci_pending_integration_only",
    "integration_budget_bound",
    "review_budget_bound",
    "review_saturation_holds_admission",
    "changes_requested_reserve_protected",
    "risk_component_lanes_bounded",
    "protected_lanes_exclusive_through_ci_pending",
    "independent_work_remains_parallel",
    "candidate_ranking_reconstructable",
    "one_external_admission_per_pm_window",
    "paused_draining_no_admit_no_cancel",
    "invalid_inputs_fail_closed",
    "deterministic_local_validation_plan",
    "publish_once_per_unchanged_head",
    "agent_stops_after_ci_pending",
    "symphony_slot_release_within_five_seconds",
    "pm_uses_issue_bound_exact_head",
    "determinate_ci_exits_system_owned",
    "passed_ci_routes_review_required",
    "implementation_failure_routes_changes_requested",
    "indeterminate_ci_holds",
    "poll_compression_records_actual_edge",
    "no_ci_pending_reactivation",
    "determinate_reconciliation_within_bound",
    "exact_head_current_main_acceptance",
    "stale_review_uses_operator_rebase_lane",
    "synthetic_candidate_is_diagnostic_only",
    "one_pr_freeze_preserved",
    "sibling_staleness_detected_and_recovered",
    "pressure_metrics_separate_conflict_types",
    "zero_automatic_merge_rebase_push",
    "single_pm_workflow_write_owner",
    "ambiguous_write_fence_blocks_conflict",
    "policy_commands_deterministic",
    "retained_evidence_secret_free",
    "zero_prohibited_authority",
)

GATE_EXERCISES: Mapping[int, tuple[str, ...]] = {
    1: (
        "serialized_controlled_workload",
        "paused_mode_no_admission",
        "draining_mode_no_admission",
        "changes_requested_recovery",
        "review_saturation_hold",
        "integration_capacity_accounting",
        "protected_lane_ci_pending_hold",
        "ci_failure_and_indeterminate_routes",
        "exact_head_acceptance",
        "ambiguous_external_write_fence",
    ),
    3: (
        "two_independent_tickets_concurrent",
        "concurrent_ranking_reproduction",
        "integration_review_lane_pressure",
        "concurrent_ci_pending_slot_release",
        "one_ci_candidate_per_tick",
        "review_saturation_hold",
        "protected_lane_contention",
        "changes_requested_recovery",
        "sibling_merge_staleness_recovery",
    ),
    5: (
        "five_working_capacity_bound",
        "integration_pressure_independent",
        "integration_saturation_hold",
        "review_saturation_hold",
        "protected_lane_with_unrelated_parallelism",
        "ci_latency_bound",
        "review_and_stale_head_pressure_bound",
        "changes_requested_mixed_load_recovery",
    ),
    7: (
        "risk_component_protected_lanes_under_load",
        "ci_pending_lane_ownership",
        "changes_requested_recovery",
        "lower_rank_cannot_bypass_selection",
        "acceptance_throughput_adequate",
        "sibling_merge_staleness_recovery",
        "gate_ten_pressure_assessment",
    ),
    10: (
        "ten_is_maximum_not_target",
        "all_capacity_controls_under_maximum",
        "ci_handoff_cadence_bound",
        "review_saturation_hold",
        "rework_dispatchable",
        "typed_hold_reasons_complete",
        "exact_head_acceptance_at_observed_rate",
        "sustained_pressure_within_limits",
        "zero_prohibited_authority_or_secret_retention",
    ),
}

_HEX_RE = re.compile(r"^[0-9a-f]+$")
_SECRET_VALUE_RE = re.compile(
    r"(?i)(bearer\s+[a-z0-9._-]+|github_pat_|gh[pousr]_|sk-[a-z0-9]|"
    r"credential[-_ ]?canary|secret[-_ ]?canary|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)
_FORBIDDEN_KEY_PARTS = (
    "authorization",
    "credential",
    "github_response",
    "linear_response",
    "password",
    "private_key",
    "provider_payload",
    "provider_response",
    "raw_",
    "raw_payload",
    "secret",
    "token",
    "workspace_path",
)
_SAFE_SECRET_COUNT_KEYS = {
    "retained_evidence_secret_free",
    "secret_retention_count",
    "zero_prohibited_authority_or_secret_retention",
}


def _mapping(value: object, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return cast(Mapping[str, Any], value)


def _exact_fields(
    payload: Mapping[str, Any], *, expected: Sequence[str], field: str
) -> None:
    if set(payload) != set(expected):
        raise ValueError(f"{field} does not contain the exact required fields")


def _list(value: object, *, field: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    return cast(list[object], value)


def _text(value: object, *, field: str, maximum: int = 240) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{field} must be bounded non-empty text")
    return value


def _integer(value: object, *, field: str, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{field} must be an integer >= {minimum}")
    return value


def _boolean(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _sha(value: object, *, field: str, length: int = 40) -> str:
    result = _text(value, field=field, maximum=length)
    if len(result) != length or _HEX_RE.fullmatch(result) is None:
        raise ValueError(f"{field} must be a lowercase {length}-character digest")
    return result


def _instant(value: object, *, field: str) -> datetime:
    raw = _text(value, field=field, maximum=40)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def _string_list(value: object, *, field: str) -> list[str]:
    items = [
        _text(item, field=f"{field}[{index}]")
        for index, item in enumerate(_list(value, field=field))
    ]
    if len(items) != len(set(items)):
        raise ValueError(f"{field} contains duplicate identities")
    return items


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _read_json(path: Path, *, maximum: int, label: str) -> Mapping[str, Any]:
    content = path.read_bytes()
    if len(content) > maximum:
        raise ValueError(f"{label} exceeds the bounded input limit")
    try:
        return _mapping(json.loads(content), field=label)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error


def _scan_for_secrets(value: object, *, field: str = "input") -> None:
    if isinstance(value, dict):
        for raw_key, child in value.items():
            if not isinstance(raw_key, str):
                raise ValueError(f"{field} contains a non-text field name")
            lowered = raw_key.lower()
            if lowered not in _SAFE_SECRET_COUNT_KEYS and any(
                part in lowered for part in _FORBIDDEN_KEY_PARTS
            ):
                raise ValueError(f"{field} contains a forbidden secret-bearing field")
            _scan_for_secrets(child, field=f"{field}.{raw_key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_for_secrets(child, field=f"{field}[{index}]")
    elif isinstance(value, str) and (
        _SECRET_VALUE_RE.search(value)
        or value.startswith("/")
        or re.match(r"^[A-Za-z]:[\\/]", value) is not None
    ):
        raise ValueError(f"{field} contains secret-bearing material")


def _validate_phase_15_5_release(value: object) -> dict[str, object]:
    release = _mapping(value, field="phase_15_5_release")
    expected = {
        "issue": "ATL-437",
        "state": "Done",
        "pr_number": 335,
        "merged": True,
        "contributor_head": PHASE_15_5_HEAD,
        "controlled_comparison": "PASS",
        "production_reconciliation_retained": True,
        "synthetic_no_rewrite_route": "retired",
        "linear_pr_opened_automation_disabled": True,
    }
    if release != expected:
        raise ValueError("phase_15_5_release does not match the ratified entry gate")
    return expected


def _validate_limits(value: object) -> dict[str, int]:
    payload = _mapping(value, field="operational_limits")
    fields = (
        "slot_release_max_seconds",
        "reconciliation_max_seconds",
        "ci_queue_run_max_seconds",
        "review_dwell_max_seconds",
        "stale_review_head_maximum",
        "mechanical_rebase_maximum",
        "semantic_conflict_maximum",
    )
    _exact_fields(payload, expected=fields, field="operational_limits")
    result = {
        field: _integer(payload.get(field), field=f"operational_limits.{field}")
        for field in fields
    }
    if result["slot_release_max_seconds"] > 5:
        raise ValueError("operational slot-release bound may not exceed five seconds")
    if result["reconciliation_max_seconds"] > 300:
        raise ValueError("operational reconciliation bound may not exceed five minutes")
    if any(result[field] < 1 for field in fields[:4]):
        raise ValueError("operational time bounds must be positive")
    return result


def _validate_workloads(value: object) -> list[dict[str, object]]:
    raw_workloads = _list(value, field="workloads")
    if len(raw_workloads) <= 10:
        raise ValueError("the live ramp requires more than ten independent workloads")
    workloads: list[dict[str, object]] = []
    all_paths: set[str] = set()
    all_lanes: set[str] = set()
    families: set[str] = set()
    identities: set[str] = set()
    for index, raw in enumerate(raw_workloads):
        field = f"workloads[{index}]"
        item = _mapping(raw, field=field)
        _exact_fields(
            item,
            expected=(
                "workload_id",
                "independent",
                "dependency_ids",
                "touched_path_family",
                "touched_paths",
                "protected_lanes",
            ),
            field=field,
        )
        workload_id = _text(item.get("workload_id"), field=f"{field}.workload_id")
        if workload_id in identities:
            raise ValueError("workloads contain duplicate identities")
        identities.add(workload_id)
        if item.get("independent") is not True or item.get("dependency_ids") != []:
            raise ValueError(f"{field} must be predeclared dependency-independent")
        family = _text(
            item.get("touched_path_family"), field=f"{field}.touched_path_family"
        )
        if family in families:
            raise ValueError("ordinary workload path families must be distinct")
        families.add(family)
        paths = _string_list(item.get("touched_paths"), field=f"{field}.touched_paths")
        if not paths or not all_paths.isdisjoint(paths):
            raise ValueError(
                "ordinary workload touched paths must be non-empty and disjoint"
            )
        all_paths.update(paths)
        lanes = _string_list(
            item.get("protected_lanes"), field=f"{field}.protected_lanes"
        )
        if not all_lanes.isdisjoint(lanes):
            raise ValueError("ordinary workload protected lanes must be disjoint")
        all_lanes.update(lanes)
        material = {
            "dependency_ids": [],
            "independent": True,
            "protected_lanes": lanes,
            "touched_path_family": family,
            "touched_paths": paths,
            "workload_id": workload_id,
        }
        workloads.append({**material, "workload_identity": _canonical_digest(material)})
    return workloads


def _validate_exercise_catalog(value: object) -> list[dict[str, object]]:
    raw_exercises = _list(value, field="exercise_catalog")
    expected = {
        (level, exercise_id)
        for level, exercise_ids in GATE_EXERCISES.items()
        for exercise_id in exercise_ids
    }
    observed: set[tuple[int, str]] = set()
    exercises: list[dict[str, object]] = []
    for index, raw in enumerate(raw_exercises):
        field = f"exercise_catalog[{index}]"
        item = _mapping(raw, field=field)
        _exact_fields(
            item,
            expected=("gate_level", "exercise_id", "excluded_from_throughput"),
            field=field,
        )
        level = _integer(item.get("gate_level"), field=f"{field}.gate_level")
        exercise_id = _text(item.get("exercise_id"), field=f"{field}.exercise_id")
        pair = (level, exercise_id)
        if pair in observed:
            raise ValueError("exercise_catalog contains duplicate entries")
        observed.add(pair)
        exercises.append(
            {
                "excluded_from_throughput": _boolean(
                    item.get("excluded_from_throughput"),
                    field=f"{field}.excluded_from_throughput",
                ),
                "exercise_id": exercise_id,
                "gate_level": level,
            }
        )
    if observed != expected:
        raise ValueError("exercise_catalog does not predeclare every gate exercise")
    return exercises


def validate_manifest(value: Mapping[str, Any]) -> dict[str, object]:
    """Validate and return the selected pre-measurement manifest projection."""

    _scan_for_secrets(value, field="manifest")
    _exact_fields(
        value,
        expected=(
            "schema_version",
            "validation_scope",
            "milestone_ticket",
            "atlas_ticket",
            "milestone_branch",
            "ratified_at",
            "phase_15_5_release",
            "operational_limits",
            "workloads",
            "exercise_catalog",
        ),
        field="manifest",
    )
    if value.get("schema_version") != MANIFEST_SCHEMA:
        raise ValueError("manifest schema_version is unsupported")
    if value.get("validation_scope") != VALIDATION_SCOPE:
        raise ValueError("manifest must remain offline read-only validation")
    if (
        value.get("milestone_ticket") != "ATL-410"
        or value.get("atlas_ticket") != "ATLAS-253"
    ):
        raise ValueError("manifest ticket identity is not ATL-410 / ATLAS-253")
    if value.get("milestone_branch") != MILESTONE_BRANCH:
        raise ValueError("manifest milestone branch is not the dedicated branch")
    ratified_at = _instant(value.get("ratified_at"), field="manifest.ratified_at")
    release = _validate_phase_15_5_release(value.get("phase_15_5_release"))
    limits = _validate_limits(value.get("operational_limits"))
    workloads = _validate_workloads(value.get("workloads"))
    exercises = _validate_exercise_catalog(value.get("exercise_catalog"))
    selected = {
        "atlas_ticket": "ATLAS-253",
        "exercise_catalog": exercises,
        "milestone_branch": MILESTONE_BRANCH,
        "milestone_ticket": "ATL-410",
        "operational_limits": limits,
        "phase_15_5_release": release,
        "ratified_at": ratified_at.isoformat().replace("+00:00", "Z"),
        "schema_version": MANIFEST_SCHEMA,
        "validation_scope": VALIDATION_SCOPE,
        "workloads": workloads,
    }
    return {**selected, "manifest_fingerprint": _canonical_digest(selected)}


def _validate_policy(value: object, *, gate: int) -> dict[str, object]:
    policy = _mapping(value, field=f"gate_{gate}.policy")
    _exact_fields(
        policy,
        expected=(
            "policy_id",
            "revision",
            "fingerprint",
            "approved_symphony_ceiling",
            "working_budget",
            "integration_budget",
            "review_budget",
            "changes_requested_reserve",
            "risk_lane_limits",
            "component_lane_limits",
            "mode",
        ),
        field=f"gate_{gate}.policy",
    )
    spec = DeliveryAdmissionPolicySpec.model_validate(
        {
            field: policy.get(field)
            for field in (
                "mode",
                "approved_symphony_ceiling",
                "working_budget",
                "integration_budget",
                "review_budget",
                "changes_requested_reserve",
                "risk_lane_limits",
                "component_lane_limits",
            )
        }
    )
    if spec.approved_symphony_ceiling != gate:
        raise ValueError(f"gate {gate} policy is incoherent with its Symphony ceiling")
    if spec.mode.value != "running":
        raise ValueError(f"gate {gate} measurement policy must be running")
    return {
        **spec.model_dump(mode="json"),
        "fingerprint": _sha(
            policy.get("fingerprint"),
            field=f"gate_{gate}.policy.fingerprint",
            length=64,
        ),
        "policy_id": _text(
            policy.get("policy_id"), field=f"gate_{gate}.policy.policy_id"
        ),
        "revision": _integer(
            policy.get("revision"), field=f"gate_{gate}.policy.revision", minimum=1
        ),
    }


def _validate_runtime(
    value: object, *, gate: int, receipt: Mapping[str, Any]
) -> dict[str, object]:
    runtime = _mapping(value, field=f"gate_{gate}.runtime_configuration")
    _exact_fields(
        runtime,
        expected=(
            "instance_id",
            "supported_procedure_id",
            "loaded_commit_sha",
            "workflow_blob_sha",
            "configured_ceiling",
            "max_turns",
            "loaded_at",
            "proof_observed_at",
            "proof_identity",
        ),
        field=f"gate_{gate}.runtime_configuration",
    )
    procedure = _text(
        runtime.get("supported_procedure_id"),
        field=f"gate_{gate}.runtime_configuration.supported_procedure_id",
    )
    if procedure != FIXTURE_RUNTIME_PROCEDURE:
        raise ValueError(
            f"gate {gate} live runtime procedure is not registered; Gate 1 is blocked"
        )
    loaded_commit = _sha(
        runtime.get("loaded_commit_sha"),
        field=f"gate_{gate}.runtime_configuration.loaded_commit_sha",
    )
    blob = _sha(
        runtime.get("workflow_blob_sha"),
        field=f"gate_{gate}.runtime_configuration.workflow_blob_sha",
    )
    if loaded_commit != receipt.get("milestone_commit_sha") or blob != receipt.get(
        "workflow_blob_sha"
    ):
        raise ValueError(f"gate {gate} running Symphony identity mismatches the branch")
    if runtime.get("configured_ceiling") != gate or runtime.get("max_turns") != 10:
        raise ValueError(f"gate {gate} running Symphony values are incoherent")
    loaded_at = _instant(
        runtime.get("loaded_at"), field=f"gate_{gate}.runtime_configuration.loaded_at"
    )
    observed_at = _instant(
        runtime.get("proof_observed_at"),
        field=f"gate_{gate}.runtime_configuration.proof_observed_at",
    )
    if observed_at < loaded_at:
        raise ValueError(f"gate {gate} runtime proof predates the runtime load")
    return {
        "configured_ceiling": gate,
        "instance_id": _text(
            runtime.get("instance_id"),
            field=f"gate_{gate}.runtime_configuration.instance_id",
        ),
        "loaded_at": loaded_at.isoformat().replace("+00:00", "Z"),
        "loaded_commit_sha": loaded_commit,
        "max_turns": 10,
        "proof_identity": _sha(
            runtime.get("proof_identity"),
            field=f"gate_{gate}.runtime_configuration.proof_identity",
            length=64,
        ),
        "proof_observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "supported_procedure_id": procedure,
        "workflow_blob_sha": blob,
    }


def _validate_protected_lane_occupancy(
    value: object, *, gate: int
) -> list[dict[str, object]]:
    items = _list(value, field=f"gate_{gate}.snapshot.protected_lane_occupancy")
    if not items:
        raise ValueError(f"gate {gate} protected lane registry state is empty")
    result: list[dict[str, object]] = []
    lanes: set[str] = set()
    for index, raw in enumerate(items):
        field = f"gate_{gate}.snapshot.protected_lane_occupancy[{index}]"
        item = _mapping(raw, field=field)
        _exact_fields(
            item,
            expected=("lane", "count", "limit", "ticket_keys", "operator_declared"),
            field=field,
        )
        lane = _text(item.get("lane"), field=f"{field}.lane", maximum=128)
        if lane in lanes:
            raise ValueError(f"gate {gate} protected lane state contains duplicates")
        lanes.add(lane)
        result.append(
            {
                "count": _integer(item.get("count"), field=f"{field}.count"),
                "lane": lane,
                "limit": _integer(item.get("limit"), field=f"{field}.limit", minimum=1),
                "operator_declared": _boolean(
                    item.get("operator_declared"),
                    field=f"{field}.operator_declared",
                ),
                "ticket_keys": _string_list(
                    item.get("ticket_keys"), field=f"{field}.ticket_keys"
                ),
            }
        )
    return result


def _validate_snapshot(value: object, *, gate: int) -> dict[str, object]:
    snapshot = _mapping(value, field=f"gate_{gate}.snapshot")
    _exact_fields(
        snapshot,
        expected=(
            "snapshot_id",
            "snapshot_fingerprint",
            "board_fingerprint",
            "complete",
            "fresh",
            "continuous",
            "contradictory",
            "unresolved_write_fence",
            "critical_fault",
            "protected_lane_registry_version",
            "protected_lane_registry_fingerprint",
            "protected_lane_state_fingerprint",
            "protected_lane_occupancy",
            "pm_sync_receipt_ids",
            "admission_run_ids",
            "ci_handoff_reconciliation_ids",
        ),
        field=f"gate_{gate}.snapshot",
    )
    required_true = ("complete", "fresh", "continuous")
    required_false = ("contradictory", "unresolved_write_fence", "critical_fault")
    for name in required_true:
        if snapshot.get(name) is not True:
            raise ValueError(f"gate {gate} snapshot {name} must be true")
    for name in required_false:
        if snapshot.get(name) is not False:
            raise ValueError(f"gate {gate} snapshot {name} must be false")
    identity_lists = {}
    for name in (
        "pm_sync_receipt_ids",
        "admission_run_ids",
        "ci_handoff_reconciliation_ids",
    ):
        values = _string_list(snapshot.get(name), field=f"gate_{gate}.snapshot.{name}")
        if not values:
            raise ValueError(f"gate {gate} snapshot {name} must not be empty")
        identity_lists[name] = values
    registry_version = _text(
        snapshot.get("protected_lane_registry_version"),
        field=f"gate_{gate}.snapshot.protected_lane_registry_version",
        maximum=128,
    )
    if registry_version != PROTECTED_LANE_REGISTRY_VERSION:
        raise ValueError(f"gate {gate} protected lane registry version is unsupported")
    return {
        **identity_lists,
        "board_fingerprint": _sha(
            snapshot.get("board_fingerprint"),
            field=f"gate_{gate}.snapshot.board_fingerprint",
            length=64,
        ),
        "complete": True,
        "continuous": True,
        "contradictory": False,
        "critical_fault": False,
        "fresh": True,
        "protected_lane_occupancy": _validate_protected_lane_occupancy(
            snapshot.get("protected_lane_occupancy"), gate=gate
        ),
        "protected_lane_registry_fingerprint": _sha(
            snapshot.get("protected_lane_registry_fingerprint"),
            field=f"gate_{gate}.snapshot.protected_lane_registry_fingerprint",
            length=64,
        ),
        "protected_lane_registry_version": registry_version,
        "protected_lane_state_fingerprint": _sha(
            snapshot.get("protected_lane_state_fingerprint"),
            field=f"gate_{gate}.snapshot.protected_lane_state_fingerprint",
            length=64,
        ),
        "snapshot_fingerprint": _sha(
            snapshot.get("snapshot_fingerprint"),
            field=f"gate_{gate}.snapshot.snapshot_fingerprint",
            length=64,
        ),
        "snapshot_id": _text(
            snapshot.get("snapshot_id"), field=f"gate_{gate}.snapshot.snapshot_id"
        ),
        "unresolved_write_fence": False,
    }


_OBSERVATION_FIELDS = (
    "max_symphony_working_occupancy",
    "max_atlas_working_occupancy",
    "max_integration_occupancy",
    "max_review_occupancy",
    "max_changes_requested_occupancy",
    "max_slot_release_seconds",
    "max_reconciliation_latency_seconds",
    "max_ci_queue_run_seconds",
    "max_review_dwell_seconds",
    "publication_count",
    "ci_pending_entries",
    "ci_pending_exits",
    "determinate_ci_pending_exits",
    "system_tier_ci_exit_writes",
    "max_ci_handoff_candidates_per_tick",
    "max_reconciliation_ticks_per_determinate_exit",
    "admission_count",
    "max_external_admissions_per_pm_window",
    "hold_count",
    "typed_hold_reason_count",
    "untyped_hold_reason_count",
    "rank_reproduction_count",
    "unselected_admission_count",
    "changes_requested_dispatch_count",
    "changes_requested_starved_count",
    "protected_lane_hold_count",
    "protected_lane_collision_count",
    "independent_parallel_count",
    "agent_ci_poll_count",
    "repeated_unchanged_head_publication_count",
    "repeated_full_validation_wait_count",
    "indeterminate_ci_hold_count",
    "poll_compressed_edge_count",
    "invented_transition_count",
    "unexpected_ci_pending_reactivation_count",
    "review_saturation_hold_count",
    "integration_saturation_hold_count",
    "acceptance_arrival_count",
    "exact_head_acceptance_completion_count",
    "one_pr_freeze_breach_count",
    "stale_review_head_count",
    "mechanical_rebase_count",
    "semantic_conflict_count",
    "ambiguous_write_incident_count",
    "unresolved_write_fence_count",
    "conflicting_external_write_count",
    "prohibited_authority_call_count",
    "repository_mutation_count",
    "external_mutation_count",
    "secret_retention_count",
)


def _validate_observation(value: object, *, gate: int) -> dict[str, object]:
    observation = _mapping(value, field=f"gate_{gate}.observation")
    _exact_fields(
        observation,
        expected=("started_at", "finished_at", *_OBSERVATION_FIELDS),
        field=f"gate_{gate}.observation",
    )
    started = _instant(
        observation.get("started_at"), field=f"gate_{gate}.observation.started_at"
    )
    finished = _instant(
        observation.get("finished_at"), field=f"gate_{gate}.observation.finished_at"
    )
    if int((finished - started).total_seconds()) != EXPECTED_WINDOW_SECONDS:
        raise ValueError(f"gate {gate} observation window must be exactly 60 minutes")
    result: dict[str, object] = {
        field: _integer(
            observation.get(field), field=f"gate_{gate}.observation.{field}"
        )
        for field in _OBSERVATION_FIELDS
    }
    result["started_at"] = started.isoformat().replace("+00:00", "Z")
    result["finished_at"] = finished.isoformat().replace("+00:00", "Z")
    return result


def _evidence_matrix(
    value: object, *, field: str, expected: Sequence[str]
) -> dict[str, dict[str, object]]:
    payload = _mapping(value, field=field)
    if set(payload) != set(expected):
        raise ValueError(f"{field} does not contain the exact required identities")
    result: dict[str, dict[str, object]] = {}
    for name in expected:
        item = _mapping(payload[name], field=f"{field}.{name}")
        _exact_fields(
            item,
            expected=("evidence_identity", "passed"),
            field=f"{field}.{name}",
        )
        result[name] = {
            "evidence_identity": _sha(
                item.get("evidence_identity"),
                field=f"{field}.{name}.evidence_identity",
                length=64,
            ),
            "passed": _boolean(item.get("passed"), field=f"{field}.{name}.passed"),
        }
    return result


def _computed_failures(
    *,
    gate: int,
    policy: Mapping[str, object],
    snapshot: Mapping[str, object],
    observation: Mapping[str, object],
    limits: Mapping[str, int],
) -> list[str]:
    number = cast(Mapping[str, int], observation)
    failures: list[str] = []

    def maximum(field: str, bound: int, reason: str) -> None:
        if number[field] > bound:
            failures.append(reason)

    maximum("max_symphony_working_occupancy", gate, "symphony_ceiling_breached")
    maximum(
        "max_atlas_working_occupancy",
        cast(int, policy["working_budget"]),
        "working_budget_breached",
    )
    maximum(
        "max_integration_occupancy",
        cast(int, policy["integration_budget"]),
        "integration_budget_breached",
    )
    maximum(
        "max_review_occupancy",
        cast(int, policy["review_budget"]),
        "review_budget_breached",
    )
    maximum(
        "max_slot_release_seconds",
        limits["slot_release_max_seconds"],
        "slot_release_bound_breached",
    )
    maximum(
        "max_reconciliation_latency_seconds",
        limits["reconciliation_max_seconds"],
        "reconciliation_bound_breached",
    )
    maximum(
        "max_ci_queue_run_seconds",
        limits["ci_queue_run_max_seconds"],
        "ci_pressure_bound_breached",
    )
    maximum(
        "max_review_dwell_seconds",
        limits["review_dwell_max_seconds"],
        "review_pressure_bound_breached",
    )
    maximum(
        "stale_review_head_count",
        limits["stale_review_head_maximum"],
        "stale_review_pressure_breached",
    )
    maximum(
        "mechanical_rebase_count",
        limits["mechanical_rebase_maximum"],
        "mechanical_rebase_pressure_breached",
    )
    maximum(
        "semantic_conflict_count",
        limits["semantic_conflict_maximum"],
        "semantic_conflict_pressure_breached",
    )
    protected_lanes = cast(
        Sequence[Mapping[str, object]], snapshot["protected_lane_occupancy"]
    )
    if any(
        cast(int, lane["count"]) > cast(int, lane["limit"]) for lane in protected_lanes
    ):
        failures.append("protected_lane_capacity_breached")
    if number["publication_count"] < 1:
        failures.append("no_live_publication")
    if number["ci_pending_entries"] != number["publication_count"]:
        failures.append("publication_ci_pending_identity_mismatch")
    if number["ci_pending_exits"] > number["ci_pending_entries"]:
        failures.append("ci_pending_exit_count_invalid")
    if number["determinate_ci_pending_exits"] != number["system_tier_ci_exit_writes"]:
        failures.append("determinate_ci_exit_owner_mismatch")
    if number["admission_count"] != number["rank_reproduction_count"]:
        failures.append("admission_ranking_not_reproduced")
    if number["typed_hold_reason_count"] != number["hold_count"]:
        failures.append("typed_hold_reasons_incomplete")
    required_positive = (
        "changes_requested_dispatch_count",
        "protected_lane_hold_count",
        "independent_parallel_count",
        "indeterminate_ci_hold_count",
        "review_saturation_hold_count",
        "exact_head_acceptance_completion_count",
        "ambiguous_write_incident_count",
    )
    for field in required_positive:
        if number[field] < 1:
            failures.append(f"{field}_missing")
    if gate >= 5 and number["integration_saturation_hold_count"] < 1:
        failures.append("integration_saturation_hold_missing")
    required_zero = (
        "untyped_hold_reason_count",
        "unselected_admission_count",
        "changes_requested_starved_count",
        "protected_lane_collision_count",
        "agent_ci_poll_count",
        "repeated_unchanged_head_publication_count",
        "repeated_full_validation_wait_count",
        "invented_transition_count",
        "unexpected_ci_pending_reactivation_count",
        "one_pr_freeze_breach_count",
        "unresolved_write_fence_count",
        "conflicting_external_write_count",
        "prohibited_authority_call_count",
        "repository_mutation_count",
        "external_mutation_count",
        "secret_retention_count",
    )
    for field in required_zero:
        if number[field] != 0:
            failures.append(f"{field}_nonzero")
    if number["max_external_admissions_per_pm_window"] > 1:
        failures.append("multiple_external_admissions_per_pm_window")
    if number["max_ci_handoff_candidates_per_tick"] > 1:
        failures.append("multiple_ci_handoff_candidates_per_tick")
    if number["max_reconciliation_ticks_per_determinate_exit"] > 1:
        failures.append("determinate_exit_exceeded_one_tick")
    if (
        number["exact_head_acceptance_completion_count"]
        < number["acceptance_arrival_count"]
    ):
        failures.append("acceptance_throughput_inadequate")
    if gate >= 3 and number["independent_parallel_count"] < 2:
        failures.append("independent_concurrency_not_observed")
    if gate == 7 and number["exact_head_acceptance_completion_count"] < 3:
        failures.append("gate_seven_acceptance_sample_inadequate")
    return sorted(set(failures))


def _validate_receipt(
    raw: Mapping[str, Any],
    *,
    expected_gate: int,
    expected_previous_id: str | None,
    previous_proven_level: int,
    manifest: Mapping[str, object],
) -> dict[str, object]:
    _scan_for_secrets(raw, field=f"gate_{expected_gate}_receipt")
    _exact_fields(
        raw,
        expected=(
            "schema_version",
            "receipt_id",
            "gate_level",
            "outcome",
            "workload_manifest_fingerprint",
            "previous_gate_receipt_id",
            "previous_proven_level",
            "retained_or_restored_level",
            "branch",
            "milestone_commit_sha",
            "origin_main_sha",
            "merge_base_sha",
            "workflow_blob_sha",
            "main_configuration",
            "runtime_configuration",
            "policy",
            "snapshot",
            "observation",
            "common_invariants",
            "gate_exercises",
            "stop_reasons",
            "operator_identity",
            "recorded_at",
        ),
        field=f"gate_{expected_gate}_receipt",
    )
    if raw.get("schema_version") != RECEIPT_SCHEMA:
        raise ValueError(f"gate {expected_gate} receipt schema_version is unsupported")
    if raw.get("gate_level") != expected_gate:
        raise ValueError(f"gate receipts must follow the exact {GATE_LEVELS} order")
    receipt_id = _text(raw.get("receipt_id"), field=f"gate_{expected_gate}.receipt_id")
    if raw.get("previous_gate_receipt_id") != expected_previous_id:
        raise ValueError(f"gate {expected_gate} does not link to the prior receipt")
    if raw.get("previous_proven_level") != previous_proven_level:
        raise ValueError(f"gate {expected_gate} previous proven level is incorrect")
    if raw.get("workload_manifest_fingerprint") != manifest.get("manifest_fingerprint"):
        raise ValueError(f"gate {expected_gate} workload manifest identity mismatched")
    if raw.get("branch") != MILESTONE_BRANCH:
        raise ValueError(f"gate {expected_gate} receipt uses the wrong branch")
    milestone_commit = _sha(
        raw.get("milestone_commit_sha"),
        field=f"gate_{expected_gate}.milestone_commit_sha",
    )
    workflow_blob = _sha(
        raw.get("workflow_blob_sha"), field=f"gate_{expected_gate}.workflow_blob_sha"
    )
    current_origin = _sha(
        raw.get("origin_main_sha"), field=f"gate_{expected_gate}.origin_main_sha"
    )
    current_merge_base = _sha(
        raw.get("merge_base_sha"), field=f"gate_{expected_gate}.merge_base_sha"
    )
    if current_merge_base != current_origin:
        raise ValueError(
            f"gate {expected_gate} milestone branch is not fresh against origin/main"
        )
    main = _mapping(raw.get("main_configuration"), field=f"gate_{expected_gate}.main")
    _exact_fields(
        main,
        expected=("commit_sha", "max_concurrent_agents", "max_turns"),
        field=f"gate_{expected_gate}.main",
    )
    if main != {
        "commit_sha": current_origin,
        "max_concurrent_agents": 1,
        "max_turns": 10,
    }:
        raise ValueError(
            f"gate {expected_gate} does not prove committed main stayed at 1/10"
        )
    runtime = _validate_runtime(
        raw.get("runtime_configuration"), gate=expected_gate, receipt=raw
    )
    policy = _validate_policy(raw.get("policy"), gate=expected_gate)
    snapshot = _validate_snapshot(raw.get("snapshot"), gate=expected_gate)
    observation = _validate_observation(raw.get("observation"), gate=expected_gate)
    if _instant(
        observation["started_at"], field=f"gate_{expected_gate}.observation.started_at"
    ) <= _instant(manifest["ratified_at"], field="manifest.ratified_at"):
        raise ValueError(f"gate {expected_gate} began before workload ratification")
    runtime_observed = _instant(
        runtime["proof_observed_at"],
        field=f"gate_{expected_gate}.runtime_configuration.proof_observed_at",
    )
    window_started = _instant(
        observation["started_at"], field=f"gate_{expected_gate}.observation.started_at"
    )
    if runtime_observed > window_started:
        raise ValueError(
            f"gate {expected_gate} runtime proof followed workload admission"
        )
    invariants = _evidence_matrix(
        raw.get("common_invariants"),
        field=f"gate_{expected_gate}.common_invariants",
        expected=COMMON_INVARIANTS,
    )
    exercises = _evidence_matrix(
        raw.get("gate_exercises"),
        field=f"gate_{expected_gate}.gate_exercises",
        expected=GATE_EXERCISES[expected_gate],
    )
    computed_failures = _computed_failures(
        gate=expected_gate,
        policy=policy,
        snapshot=snapshot,
        observation=observation,
        limits=cast(Mapping[str, int], manifest["operational_limits"]),
    )
    evidence_failures = sorted(
        [name for name, item in invariants.items() if item["passed"] is False]
        + [name for name, item in exercises.items() if item["passed"] is False]
    )
    outcome = _text(raw.get("outcome"), field=f"gate_{expected_gate}.outcome")
    stop_reasons = _string_list(
        raw.get("stop_reasons"), field=f"gate_{expected_gate}.stop_reasons"
    )
    if outcome == "PASS":
        if stop_reasons or computed_failures or evidence_failures:
            raise ValueError(f"gate {expected_gate} cannot PASS with failed evidence")
        expected_retained = expected_gate
    elif outcome == "FAIL":
        if not stop_reasons or not (computed_failures or evidence_failures):
            raise ValueError(
                f"gate {expected_gate} FAIL lacks a corroborated stop reason"
            )
        expected_retained = previous_proven_level
    else:
        raise ValueError(f"gate {expected_gate} outcome must be PASS or FAIL")
    if raw.get("retained_or_restored_level") != expected_retained:
        raise ValueError(f"gate {expected_gate} retained/restored level is incorrect")
    operator_identity = _text(
        raw.get("operator_identity"), field=f"gate_{expected_gate}.operator_identity"
    )
    if not operator_identity.startswith("human/operator"):
        raise ValueError(
            f"gate {expected_gate} operator identity is not human/operator"
        )
    recorded_at = _instant(
        raw.get("recorded_at"), field=f"gate_{expected_gate}.recorded_at"
    )
    if recorded_at < _instant(
        observation["finished_at"],
        field=f"gate_{expected_gate}.observation.finished_at",
    ):
        raise ValueError(f"gate {expected_gate} receipt predates its finished window")
    selected = {
        "branch": MILESTONE_BRANCH,
        "common_invariants": invariants,
        "computed_failures": computed_failures,
        "gate_exercises": exercises,
        "gate_level": expected_gate,
        "main_configuration": cast(dict[str, object], main),
        "merge_base_sha": current_merge_base,
        "milestone_commit_sha": milestone_commit,
        "observation": observation,
        "operator_identity": operator_identity,
        "origin_main_sha": current_origin,
        "outcome": outcome,
        "policy": policy,
        "previous_gate_receipt_id": expected_previous_id,
        "previous_proven_level": previous_proven_level,
        "receipt_id": receipt_id,
        "recorded_at": recorded_at.isoformat().replace("+00:00", "Z"),
        "retained_or_restored_level": expected_retained,
        "runtime_configuration": runtime,
        "schema_version": RECEIPT_SCHEMA,
        "snapshot": snapshot,
        "stop_reasons": stop_reasons,
        "workflow_blob_sha": workflow_blob,
        "workload_manifest_fingerprint": manifest["manifest_fingerprint"],
    }
    return {**selected, "receipt_fingerprint": _canonical_digest(selected)}


def evaluate_ramp(
    manifest_payload: Mapping[str, Any],
    receipt_payloads: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, object], bool]:
    """Evaluate a manifest and ordered receipt sequence without side effects."""

    manifest = validate_manifest(manifest_payload)
    if len(receipt_payloads) > len(GATE_LEVELS):
        raise ValueError("the ramp contains more than five gate receipts")
    receipts: list[dict[str, object]] = []
    previous_id: str | None = None
    previous_proven = 1
    last_validated_pass: int | None = None
    failed_gate: int | None = None
    for index, raw in enumerate(receipt_payloads):
        if failed_gate is not None:
            raise ValueError("a receipt appears after a failed gate")
        gate = GATE_LEVELS[index]
        receipt = _validate_receipt(
            raw,
            expected_gate=gate,
            expected_previous_id=previous_id,
            previous_proven_level=previous_proven,
            manifest=manifest,
        )
        receipts.append(receipt)
        previous_id = cast(str, receipt["receipt_id"])
        if receipt["outcome"] == "FAIL":
            failed_gate = gate
        else:
            previous_proven = gate
            last_validated_pass = gate

    if failed_gate is not None:
        decision = f"FAIL_GATE_{failed_gate}"
        closure_authorized = False
        passed = False
    elif len(receipts) < len(GATE_LEVELS):
        decision = f"PENDING_GATE_{GATE_LEVELS[len(receipts)]}"
        closure_authorized = False
        passed = False
    else:
        decision = "RECEIPT_SEQUENCE_VALIDATED"
        closure_authorized = False
        passed = True
    report: dict[str, object] = {
        "authority_spies": {
            "external_write_count": 0,
            "network_call_count": 0,
            "prohibited_action_count": 0,
            "repository_mutation_count": 0,
        },
        "closure_authorized": closure_authorized,
        "decision": decision,
        "gate_sequence": [receipt["gate_level"] for receipt in receipts],
        "last_validated_pass_receipt_level": last_validated_pass,
        "manifest_fingerprint": manifest["manifest_fingerprint"],
        "milestone_branch": MILESTONE_BRANCH,
        "next_gate": None
        if len(receipts) == len(GATE_LEVELS)
        else GATE_LEVELS[len(receipts)],
        "phase_15_5_entry_gate": manifest["phase_15_5_release"],
        "transition_authorized": False,
        "receipt_summaries": [
            {
                "gate_level": receipt["gate_level"],
                "milestone_commit_sha": receipt["milestone_commit_sha"],
                "outcome": receipt["outcome"],
                "receipt_fingerprint": receipt["receipt_fingerprint"],
                "receipt_id": receipt["receipt_id"],
                "retained_or_restored_level": receipt["retained_or_restored_level"],
                "runtime_proof_identity": cast(
                    Mapping[str, object], receipt["runtime_configuration"]
                )["proof_identity"],
                "stop_reasons": receipt["stop_reasons"],
            }
            for receipt in receipts
        ],
        "schema_version": "phase-15-ramp-report-v1",
        "validation_scope": VALIDATION_SCOPE,
        "workload_count": len(cast(Sequence[object], manifest["workloads"])),
        "workload_identities": [
            cast(Mapping[str, object], workload)["workload_identity"]
            for workload in cast(Sequence[object], manifest["workloads"])
        ],
    }
    encoded = json.dumps(
        report, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    if len(encoded.encode()) > MAX_REPORT_BYTES:
        raise ValueError("retained ramp report exceeds the bounded output limit")
    return report, passed


def run_ramp(
    manifest_path: Path, receipt_paths: Sequence[Path]
) -> tuple[dict[str, object], bool]:
    manifest = _read_json(
        manifest_path, maximum=MAX_MANIFEST_BYTES, label="workload manifest"
    )
    receipts = [
        _read_json(path, maximum=MAX_RECEIPT_BYTES, label=f"gate receipt {index + 1}")
        for index, path in enumerate(receipt_paths)
    ]
    return evaluate_ramp(manifest, receipts)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the read-only Phase 15 1->3->5->7->10 ramp receipts."
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--gate-receipt",
        action="append",
        default=[],
        type=Path,
        help="ordered operator receipt; repeat for Gate 1, 3, 5, 7 and 10",
    )
    parser.add_argument("--fingerprint-only", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.fingerprint_only:
            if args.gate_receipt:
                raise ValueError("--fingerprint-only does not accept gate receipts")
            payload = _read_json(
                args.manifest,
                maximum=MAX_MANIFEST_BYTES,
                label="workload manifest",
            )
            manifest = validate_manifest(payload)
            print(manifest["manifest_fingerprint"])
            return 0
        report, passed = run_ramp(args.manifest, args.gate_receipt)
    except (OSError, ValueError):
        print(
            json.dumps(
                {
                    "decision": "INVALID_INPUT",
                    "error": "bounded ramp input validation failed",
                    "schema_version": "phase-15-ramp-report-v1",
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(report, indent=2 if args.pretty else None, sort_keys=True))
    if passed:
        return 0
    return 1 if str(report["decision"]).startswith("FAIL") else 3


if __name__ == "__main__":
    raise SystemExit(main())

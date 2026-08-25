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
from uuid import UUID

from atlas.core.models.delivery_admission_policy import DeliveryAdmissionPolicySpec
from atlas.pm.protected_lanes import (
    ProtectedLaneClassification,
    ProtectedLaneClassifierInput,
    ProtectedLaneRegistry,
    classify_protected_lane_inputs,
    load_packaged_protected_lane_registry,
)

MILESTONE_BRANCH = "phase-15-atlas-253-ceiling-ramp"
GATE_LEVELS = (1, 3, 5, 7, 10)
MANIFEST_SCHEMA_V1 = "phase-15-ramp-workload-v1"
MANIFEST_SCHEMA_V2 = "phase-15-ramp-workload-v2"
MANIFEST_SCHEMA_V3 = "phase-15-ramp-workload-v3"
RECEIPT_SCHEMA_V1 = "phase-15-ramp-gate-receipt-v1"
RECEIPT_SCHEMA_V2 = "phase-15-ramp-gate-receipt-v2"
REPORT_SCHEMA = "phase-15-ramp-report-v3"
MAX_MANIFEST_BYTES = 60 * 1024
MAX_RECEIPT_BYTES = 60 * 1024
MAX_REPORT_BYTES = 128 * 1024
EXPECTED_WINDOW_SECONDS = 60 * 60
PHASE_15_5_HEAD = "a598798c1a6c5cabe4c80c0f04020c271f438de1"
VALIDATION_SCOPE = "offline-read-only"
FIXTURE_RUNTIME_PROCEDURE = "fixture-only-no-live-runtime-v1"
LIVE_RUNTIME_PROCEDURE = "vps-systemd-immutable-workflow-readback-v1"
LIVE_RUNTIME_SERVICE_UNIT = "atlas-symphony.service"
LIVE_SYMPHONY_COMMIT_SHA = "e5c5e48917e9e91ffb6709ab5a2a02c5af16bf02"
PROTECTED_LANE_REGISTRY_VERSION = "protected-integration-lanes/v1"
_PROTECTED_EXERCISE_BINDINGS = (
    (1, "protected_lane_ci_pending_hold", "owner"),
    (3, "protected_lane_contention", "blocked_candidate"),
    (3, "protected_lane_contention", "owner"),
    (5, "protected_lane_with_unrelated_parallelism", "owner"),
    (7, "ci_pending_lane_ownership", "owner"),
    (7, "risk_component_protected_lanes_under_load", "owner"),
)

_V3_EXERCISE_ROLES: Mapping[str, tuple[int, str, str, str]] = {
    "gate_1_protected_lane_ci_pending_hold_owner": (
        1,
        "protected_lane_ci_pending_hold",
        "owner",
        "workflow-configuration",
    ),
    "gate_3_protected_lane_contention_blocked_candidate": (
        3,
        "protected_lane_contention",
        "blocked_candidate",
        "operator-admission-hotspot",
    ),
    "gate_3_protected_lane_contention_owner": (
        3,
        "protected_lane_contention",
        "owner",
        "operator-admission-hotspot",
    ),
    "gate_5_protected_lane_with_unrelated_parallelism_owner": (
        5,
        "protected_lane_with_unrelated_parallelism",
        "owner",
        "planning-state",
    ),
    "gate_7_ci_pending_lane_ownership_owner": (
        7,
        "ci_pending_lane_ownership",
        "owner",
        "generated-contracts",
    ),
    "gate_7_risk_component_protected_lanes_under_load_owner": (
        7,
        "risk_component_protected_lanes_under_load",
        "owner",
        "database-migrations",
    ),
}

_RISK_LEVELS = {"low", "medium", "high", "critical"}

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
_REAL_TICKET_KEY_RE = re.compile(r"^ATLAS-[1-9][0-9]*$")
_LINEAR_IDENTIFIER_RE = re.compile(r"^ATL-[1-9][0-9]*$")
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


def _repository_path(value: object, *, field: str) -> str:
    path = _text(value, field=field)
    if path.startswith("/") or "\\" in path:
        raise ValueError(f"{field} must be a relative repository path")
    segments = path.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError(f"{field} must be a canonical repository path")
    if any(ord(character) < 32 or ord(character) == 127 for character in path):
        raise ValueError(f"{field} must be a canonical repository path")
    return path


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


def _atlas_key(value: object, *, field: str) -> str:
    key = _text(value, field=field)
    if _REAL_TICKET_KEY_RE.fullmatch(key) is None:
        raise ValueError(f"{field} must be a real Atlas ticket key")
    return key


def _linear_identifier(value: object, *, field: str) -> str:
    identifier = _text(value, field=field)
    if _LINEAR_IDENTIFIER_RE.fullmatch(identifier) is None:
        raise ValueError(f"{field} must be a Linear ATL-N identifier")
    return identifier


def _linear_uuid(value: object, *, field: str) -> str:
    raw = _text(value, field=field, maximum=36)
    try:
        parsed = UUID(raw)
    except ValueError as error:
        raise ValueError(f"{field} must be a canonical Linear UUID") from error
    canonical = str(parsed)
    if raw != canonical:
        raise ValueError(f"{field} must be a canonical Linear UUID")
    return canonical


def _dependency_identities(value: object, *, field: str) -> list[dict[str, str]]:
    identities: list[dict[str, str]] = []
    atlas_keys: set[str] = set()
    linear_identifiers: set[str] = set()
    linear_uuids: set[str] = set()
    for index, raw in enumerate(_list(value, field=field)):
        identity_field = f"{field}[{index}]"
        item = _mapping(raw, field=identity_field)
        _exact_fields(
            item,
            expected=("atlas_key", "linear_identifier", "linear_uuid"),
            field=identity_field,
        )
        atlas_key = _atlas_key(
            item.get("atlas_key"), field=f"{identity_field}.atlas_key"
        )
        linear_identifier = _linear_identifier(
            item.get("linear_identifier"),
            field=f"{identity_field}.linear_identifier",
        )
        linear_uuid = _linear_uuid(
            item.get("linear_uuid"), field=f"{identity_field}.linear_uuid"
        )
        if (
            atlas_key in atlas_keys
            or linear_identifier in linear_identifiers
            or linear_uuid in linear_uuids
        ):
            raise ValueError(f"{field} contains duplicate Atlas or Linear identities")
        atlas_keys.add(atlas_key)
        linear_identifiers.add(linear_identifier)
        linear_uuids.add(linear_uuid)
        identities.append(
            {
                "atlas_key": atlas_key,
                "linear_identifier": linear_identifier,
                "linear_uuid": linear_uuid,
            }
        )
    identities.sort(key=lambda identity: identity["atlas_key"])
    return identities


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


def _sorted_string_list(value: object, *, field: str) -> list[str]:
    return sorted(_string_list(value, field=field))


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _load_protected_lane_registry() -> ProtectedLaneRegistry:
    loaded = load_packaged_protected_lane_registry()
    if loaded.registry is None:
        raise ValueError(loaded.error or "protected lane registry is unavailable")
    return loaded.registry


def _classification_projection(
    classification: ProtectedLaneClassification,
) -> dict[str, object]:
    return {
        "issues": [
            {
                "code": issue.code.value,
                "declaration": issue.declaration,
                "source_kind": issue.source_kind,
            }
            for issue in classification.issues
        ],
        "matches": [
            {
                "declarations": [
                    list(declaration) for declaration in match.declarations
                ],
                "lane": match.lane,
            }
            for match in classification.matches
        ],
        "registry_fingerprint": classification.registry_fingerprint,
        "registry_version": classification.registry_version,
        "ticket_key": classification.ticket_key,
    }


def _validate_declared_classification(
    value: object, *, field: str
) -> dict[str, object]:
    payload = _mapping(value, field=field)
    _exact_fields(
        payload,
        expected=(
            "issues",
            "matches",
            "registry_fingerprint",
            "registry_version",
            "ticket_key",
        ),
        field=field,
    )
    issues: list[dict[str, str]] = []
    for index, raw in enumerate(_list(payload.get("issues"), field=f"{field}.issues")):
        issue_field = f"{field}.issues[{index}]"
        issue = _mapping(raw, field=issue_field)
        _exact_fields(
            issue,
            expected=("code", "declaration", "source_kind"),
            field=issue_field,
        )
        issues.append(
            {
                "code": _text(issue.get("code"), field=f"{issue_field}.code"),
                "declaration": _text(
                    issue.get("declaration"), field=f"{issue_field}.declaration"
                ),
                "source_kind": _text(
                    issue.get("source_kind"), field=f"{issue_field}.source_kind"
                ),
            }
        )
    if len({tuple(sorted(issue.items())) for issue in issues}) != len(issues):
        raise ValueError(f"{field}.issues contains duplicate identities")
    issues.sort(
        key=lambda issue: (issue["code"], issue["source_kind"], issue["declaration"])
    )

    matches: list[dict[str, object]] = []
    lanes: set[str] = set()
    for index, raw in enumerate(
        _list(payload.get("matches"), field=f"{field}.matches")
    ):
        match_field = f"{field}.matches[{index}]"
        match = _mapping(raw, field=match_field)
        _exact_fields(match, expected=("declarations", "lane"), field=match_field)
        lane = _text(match.get("lane"), field=f"{match_field}.lane", maximum=128)
        if lane in lanes:
            raise ValueError(f"{field}.matches contains duplicate lanes")
        lanes.add(lane)
        declarations: list[list[str]] = []
        seen_declarations: set[tuple[str, str, str]] = set()
        for declaration_index, raw_declaration in enumerate(
            _list(
                match.get("declarations"),
                field=f"{match_field}.declarations",
            )
        ):
            declaration_field = f"{match_field}.declarations[{declaration_index}]"
            parts = _list(raw_declaration, field=declaration_field)
            if len(parts) != 3:
                raise ValueError(
                    f"{declaration_field} must contain exactly three identities"
                )
            selected = cast(
                tuple[str, str, str],
                tuple(
                    _text(part, field=f"{declaration_field}[{part_index}]")
                    for part_index, part in enumerate(parts)
                ),
            )
            if selected in seen_declarations:
                raise ValueError(f"{match_field}.declarations contains duplicates")
            seen_declarations.add(selected)
            declarations.append(list(selected))
        declarations.sort()
        matches.append({"declarations": declarations, "lane": lane})
    matches.sort(key=lambda match: cast(str, match["lane"]))
    return {
        "issues": issues,
        "matches": matches,
        "registry_fingerprint": _sha(
            payload.get("registry_fingerprint"),
            field=f"{field}.registry_fingerprint",
            length=64,
        ),
        "registry_version": _text(
            payload.get("registry_version"),
            field=f"{field}.registry_version",
            maximum=128,
        ),
        "ticket_key": _text(payload.get("ticket_key"), field=f"{field}.ticket_key"),
    }


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


def _validate_workloads(
    value: object,
    *,
    registry: ProtectedLaneRegistry | None = None,
    canonical_order: bool = False,
) -> list[dict[str, object]]:
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
        if canonical_order:
            paths = sorted(
                _repository_path(path, field=f"{field}.touched_paths") for path in paths
            )
        if not paths or not all_paths.isdisjoint(paths):
            raise ValueError(
                "ordinary workload touched paths must be non-empty and disjoint"
            )
        all_paths.update(paths)
        lanes = _string_list(
            item.get("protected_lanes"), field=f"{field}.protected_lanes"
        )
        if canonical_order:
            lanes = sorted(lanes)
        if registry is not None:
            known_lanes = {lane.key for lane in registry.lanes}
            if any(lane not in known_lanes for lane in lanes):
                raise ValueError("ordinary workload uses an unknown protected lane")
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
    if canonical_order:
        workloads.sort(key=lambda workload: cast(str, workload["workload_id"]))
    return workloads


def _validate_exercise_catalog(
    value: object, *, canonical_order: bool = False
) -> list[dict[str, object]]:
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
    if canonical_order:
        exercises.sort(
            key=lambda exercise: (
                cast(int, exercise["gate_level"]),
                cast(str, exercise["exercise_id"]),
            )
        )
    return exercises


def _validate_manifest_v1(value: Mapping[str, Any]) -> dict[str, object]:
    """Replay the immutable v1 manifest projection without changing its digest."""

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
    if value.get("schema_version") != MANIFEST_SCHEMA_V1:
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
        "schema_version": MANIFEST_SCHEMA_V1,
        "validation_scope": VALIDATION_SCOPE,
        "workloads": workloads,
    }
    return {**selected, "manifest_fingerprint": _canonical_digest(selected)}


def _validate_exercise_workloads(
    value: object,
    *,
    registry: ProtectedLaneRegistry,
    ordinary_workloads: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    raw_workloads = _list(value, field="exercise_workloads")
    if not 1 <= len(raw_workloads) <= 8:
        raise ValueError("exercise_workloads must contain between one and eight items")
    ordinary_paths = {
        path
        for workload in ordinary_workloads
        for path in cast(Sequence[str], workload["touched_paths"])
    }
    identities: set[str] = set()
    ticket_keys: set[str] = set()
    families: set[str] = set()
    touched_paths = set(ordinary_paths)
    workloads: list[dict[str, object]] = []
    for index, raw in enumerate(raw_workloads):
        field = f"exercise_workloads[{index}]"
        item = _mapping(raw, field=field)
        _exact_fields(
            item,
            expected=(
                "exercise_workload_id",
                "ticket_key",
                "touched_path_family",
                "touched_paths",
                "component",
                "tags",
                "relevant_docs",
                "documentation_requirements",
                "excluded_from_throughput",
                "classification",
                "classification_fingerprint",
            ),
            field=field,
        )
        workload_id = _text(
            item.get("exercise_workload_id"),
            field=f"{field}.exercise_workload_id",
        )
        if workload_id in identities:
            raise ValueError("exercise_workloads contain duplicate identities")
        identities.add(workload_id)
        ticket_key = _text(item.get("ticket_key"), field=f"{field}.ticket_key")
        if _REAL_TICKET_KEY_RE.fullmatch(ticket_key) is None:
            raise ValueError(
                "exercise workload ticket identity must be a real ATLAS key"
            )
        if ticket_key in ticket_keys:
            raise ValueError("exercise_workloads contain duplicate ticket identities")
        ticket_keys.add(ticket_key)
        family = _text(
            item.get("touched_path_family"),
            field=f"{field}.touched_path_family",
        )
        if family in families:
            raise ValueError("exercise workload path families must be distinct")
        families.add(family)
        paths = sorted(
            _repository_path(path, field=f"{field}.touched_paths")
            for path in _string_list(
                item.get("touched_paths"), field=f"{field}.touched_paths"
            )
        )
        if not paths or not touched_paths.isdisjoint(paths):
            raise ValueError(
                "exercise workload touched paths must be non-empty and disjoint"
            )
        touched_paths.update(paths)
        component = _text(item.get("component"), field=f"{field}.component")
        tags = _sorted_string_list(item.get("tags"), field=f"{field}.tags")
        relevant_docs = sorted(
            _repository_path(path, field=f"{field}.relevant_docs")
            for path in _string_list(
                item.get("relevant_docs"), field=f"{field}.relevant_docs"
            )
        )
        documentation_requirements = sorted(
            _repository_path(path, field=f"{field}.documentation_requirements")
            for path in _string_list(
                item.get("documentation_requirements"),
                field=f"{field}.documentation_requirements",
            )
        )
        if item.get("excluded_from_throughput") is not True:
            raise ValueError(
                f"{field} must be explicitly excluded from ordinary throughput"
            )
        classification = classify_protected_lane_inputs(
            ProtectedLaneClassifierInput(
                ticket_key=ticket_key,
                component=component,
                tags=tuple(tags),
                relevant_docs=tuple(relevant_docs),
                documentation_requirements=tuple(documentation_requirements),
            ),
            registry,
        )
        if classification.issues or len(classification.lanes) != 1:
            raise ValueError(
                f"{field} must recompute into exactly one unambiguous protected lane"
            )
        recomputed = _classification_projection(classification)
        declared = _validate_declared_classification(
            item.get("classification"), field=f"{field}.classification"
        )
        if declared != recomputed:
            raise ValueError(
                f"{field} declared classification disagrees with recomputation"
            )
        declared_fingerprint = _sha(
            item.get("classification_fingerprint"),
            field=f"{field}.classification_fingerprint",
            length=64,
        )
        if declared_fingerprint != classification.fingerprint:
            raise ValueError(f"{field} classification fingerprint drifted")
        workloads.append(
            {
                "classification": recomputed,
                "classification_fingerprint": classification.fingerprint,
                "component": component,
                "documentation_requirements": documentation_requirements,
                "excluded_from_throughput": True,
                "exercise_workload_id": workload_id,
                "relevant_docs": relevant_docs,
                "tags": tags,
                "ticket_key": ticket_key,
                "touched_path_family": family,
                "touched_paths": paths,
            }
        )
    workloads.sort(key=lambda workload: cast(str, workload["exercise_workload_id"]))
    return workloads


def _exercise_workload_lane(workload: Mapping[str, object]) -> str:
    classification = cast(
        Mapping[str, object],
        workload.get("protected_lane_classification", workload.get("classification")),
    )
    matches = cast(Sequence[Mapping[str, object]], classification["matches"])
    return cast(str, matches[0]["lane"])


def _exercise_ticket_key(workload: Mapping[str, object]) -> str:
    return cast(str, workload.get("atlas_key", workload.get("ticket_key")))


def _validate_exercise_bindings(
    value: object,
    *,
    exercise_workloads: Sequence[Mapping[str, object]],
    exercise_catalog: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    raw_bindings = _list(value, field="exercise_bindings")
    workload_by_id = {
        cast(str, workload["exercise_workload_id"]): workload
        for workload in exercise_workloads
    }
    catalog = {
        (cast(int, exercise["gate_level"]), cast(str, exercise["exercise_id"]))
        for exercise in exercise_catalog
    }
    observed_roles: set[tuple[int, str, str]] = set()
    observed_workloads: set[str] = set()
    bindings: list[dict[str, object]] = []
    for index, raw in enumerate(raw_bindings):
        field = f"exercise_bindings[{index}]"
        item = _mapping(raw, field=field)
        _exact_fields(
            item,
            expected=("gate_level", "exercise_id", "exercise_workload_id", "role"),
            field=field,
        )
        gate = _integer(item.get("gate_level"), field=f"{field}.gate_level")
        exercise_id = _text(item.get("exercise_id"), field=f"{field}.exercise_id")
        workload_id = _text(
            item.get("exercise_workload_id"),
            field=f"{field}.exercise_workload_id",
        )
        role = _text(item.get("role"), field=f"{field}.role")
        if (gate, exercise_id) not in catalog:
            raise ValueError("exercise binding names an unknown gate exercise")
        if workload_id not in workload_by_id:
            raise ValueError("exercise binding names an undeclared workload")
        identity = (gate, exercise_id, role)
        if identity in observed_roles:
            raise ValueError("exercise_bindings contain duplicate bounded roles")
        if workload_id in observed_workloads:
            raise ValueError("exercise workload is bound more than once")
        observed_roles.add(identity)
        observed_workloads.add(workload_id)
        bindings.append(
            {
                "exercise_id": exercise_id,
                "exercise_workload_id": workload_id,
                "gate_level": gate,
                "role": role,
            }
        )
    if observed_roles != set(_PROTECTED_EXERCISE_BINDINGS):
        raise ValueError("exercise_bindings do not contain the exact protected roles")
    if observed_workloads != set(workload_by_id):
        raise ValueError("exercise_workloads contain orphaned identities")
    bindings.sort(
        key=lambda binding: (
            cast(int, binding["gate_level"]),
            cast(str, binding["exercise_id"]),
            cast(str, binding["role"]),
            cast(str, binding["exercise_workload_id"]),
        )
    )
    gate_three = [binding for binding in bindings if binding["gate_level"] == 3]
    gate_three_workloads = [
        workload_by_id[cast(str, binding["exercise_workload_id"])]
        for binding in gate_three
    ]
    if (
        len({_exercise_ticket_key(workload) for workload in gate_three_workloads}) != 2
        or len({_exercise_workload_lane(workload) for workload in gate_three_workloads})
        != 1
    ):
        raise ValueError(
            "Gate 3 contention requires distinct same-lane owner and blocked candidate"
        )
    bindings_by_workload = {
        cast(str, binding["exercise_workload_id"]): binding for binding in bindings
    }
    lane_groups: dict[str, list[str]] = {}
    for workload_id, workload in workload_by_id.items():
        lane_groups.setdefault(_exercise_workload_lane(workload), []).append(
            workload_id
        )
    for workload_ids in lane_groups.values():
        if len(workload_ids) <= 1:
            continue
        if {
            (
                bindings_by_workload[workload_id]["gate_level"],
                bindings_by_workload[workload_id]["exercise_id"],
            )
            for workload_id in workload_ids
        } != {(3, "protected_lane_contention")}:
            raise ValueError(
                "same-lane exercise workloads require explicit Gate 3 co-binding"
            )
    return bindings


def _validate_manifest_v2(value: Mapping[str, Any]) -> dict[str, object]:
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
            "protected_lane_registry_version",
            "protected_lane_registry_fingerprint",
            "workloads",
            "exercise_catalog",
            "exercise_workloads",
            "exercise_bindings",
        ),
        field="manifest",
    )
    if value.get("schema_version") != MANIFEST_SCHEMA_V2:
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
    registry = _load_protected_lane_registry()
    registry_version = _text(
        value.get("protected_lane_registry_version"),
        field="manifest.protected_lane_registry_version",
        maximum=128,
    )
    registry_fingerprint = _sha(
        value.get("protected_lane_registry_fingerprint"),
        field="manifest.protected_lane_registry_fingerprint",
        length=64,
    )
    if registry_version != registry.version:
        raise ValueError("manifest protected lane registry version drifted")
    if registry_fingerprint != registry.fingerprint:
        raise ValueError("manifest protected lane registry fingerprint drifted")
    ratified_at = _instant(value.get("ratified_at"), field="manifest.ratified_at")
    release = _validate_phase_15_5_release(value.get("phase_15_5_release"))
    limits = _validate_limits(value.get("operational_limits"))
    workloads = _validate_workloads(
        value.get("workloads"), registry=registry, canonical_order=True
    )
    exercises = _validate_exercise_catalog(
        value.get("exercise_catalog"), canonical_order=True
    )
    exercise_workloads = _validate_exercise_workloads(
        value.get("exercise_workloads"),
        registry=registry,
        ordinary_workloads=workloads,
    )
    exercise_bindings = _validate_exercise_bindings(
        value.get("exercise_bindings"),
        exercise_workloads=exercise_workloads,
        exercise_catalog=exercises,
    )
    selected = {
        "atlas_ticket": "ATLAS-253",
        "exercise_bindings": exercise_bindings,
        "exercise_catalog": exercises,
        "exercise_workloads": exercise_workloads,
        "milestone_branch": MILESTONE_BRANCH,
        "milestone_ticket": "ATL-410",
        "operational_limits": limits,
        "phase_15_5_release": release,
        "protected_lane_registry_fingerprint": registry.fingerprint,
        "protected_lane_registry_version": registry.version,
        "ratified_at": ratified_at.isoformat().replace("+00:00", "Z"),
        "schema_version": MANIFEST_SCHEMA_V2,
        "validation_scope": VALIDATION_SCOPE,
        "workloads": workloads,
    }
    return {**selected, "manifest_fingerprint": _canonical_digest(selected)}


def _validate_v3_workloads(
    value: object, *, registry: ProtectedLaneRegistry
) -> list[dict[str, object]]:
    raw_workloads = _list(value, field="workloads")
    if len(raw_workloads) <= 10:
        raise ValueError("the live ramp requires more than ten independent workloads")
    known_lanes = {lane.key for lane in registry.lanes}
    atlas_keys: set[str] = set()
    linear_identifiers: set[str] = set()
    linear_uuids: set[str] = set()
    workload_ids: set[str] = set()
    families: set[str] = set()
    touched_paths: set[str] = set()
    protected_lanes: set[str] = set()
    workloads: list[dict[str, object]] = []
    for index, raw in enumerate(raw_workloads):
        field = f"workloads[{index}]"
        item = _mapping(raw, field=field)
        _exact_fields(
            item,
            expected=(
                "workload_id",
                "atlas_key",
                "linear_identifier",
                "linear_uuid",
                "risk",
                "production_paths",
                "test_paths",
                "path_family",
                "independent",
                "dependency_identities",
                "protected_lane_classification",
                "workload_role",
                "earliest_permitted_gate",
                "excluded_from_throughput",
                "native_workload_fingerprint",
            ),
            field=field,
        )
        workload_id = _text(item.get("workload_id"), field=f"{field}.workload_id")
        atlas_key = _atlas_key(item.get("atlas_key"), field=f"{field}.atlas_key")
        linear_identifier = _linear_identifier(
            item.get("linear_identifier"), field=f"{field}.linear_identifier"
        )
        linear_uuid = _linear_uuid(
            item.get("linear_uuid"), field=f"{field}.linear_uuid"
        )
        if workload_id in workload_ids:
            raise ValueError("workloads contain duplicate identities")
        if (
            atlas_key in atlas_keys
            or linear_identifier in linear_identifiers
            or linear_uuid in linear_uuids
        ):
            raise ValueError("workloads contain duplicate Atlas or Linear identities")
        workload_ids.add(workload_id)
        atlas_keys.add(atlas_key)
        linear_identifiers.add(linear_identifier)
        linear_uuids.add(linear_uuid)
        risk = _text(item.get("risk"), field=f"{field}.risk")
        if risk not in _RISK_LEVELS:
            raise ValueError(f"{field}.risk is unsupported")
        production_paths = sorted(
            _repository_path(path, field=f"{field}.production_paths")
            for path in _string_list(
                item.get("production_paths"), field=f"{field}.production_paths"
            )
        )
        test_paths = sorted(
            _repository_path(path, field=f"{field}.test_paths")
            for path in _string_list(
                item.get("test_paths"), field=f"{field}.test_paths"
            )
        )
        workload_paths = set(production_paths) | set(test_paths)
        if (
            not workload_paths
            or len(workload_paths) != len(production_paths) + len(test_paths)
            or not touched_paths.isdisjoint(workload_paths)
        ):
            raise ValueError("ordinary workload paths must be non-empty and disjoint")
        touched_paths.update(workload_paths)
        family = _text(item.get("path_family"), field=f"{field}.path_family")
        if family in families:
            raise ValueError("ordinary workload path families must be distinct")
        families.add(family)
        dependencies = _dependency_identities(
            item.get("dependency_identities"),
            field=f"{field}.dependency_identities",
        )
        independent = _boolean(item.get("independent"), field=f"{field}.independent")
        if independent is not True:
            raise ValueError(f"{field} contradicts the required workload independence")
        if dependencies:
            raise ValueError(
                f"{field} contradicts its dependency-independent declaration"
            )
        lanes = sorted(
            _string_list(
                item.get("protected_lane_classification"),
                field=f"{field}.protected_lane_classification",
            )
        )
        if any(lane not in known_lanes for lane in lanes):
            raise ValueError("ordinary workload uses an unknown protected lane")
        if not protected_lanes.isdisjoint(lanes):
            raise ValueError("ordinary workload protected lanes must be disjoint")
        protected_lanes.update(lanes)
        if item.get("workload_role") != "ordinary":
            raise ValueError(f"{field} must declare the ordinary workload role")
        earliest_gate = _integer(
            item.get("earliest_permitted_gate"),
            field=f"{field}.earliest_permitted_gate",
        )
        if earliest_gate != 1:
            raise ValueError(
                f"{field} ordinary role requires earliest permitted Gate 1"
            )
        if item.get("excluded_from_throughput") is not False:
            raise ValueError(f"{field} ordinary workload must enter throughput")
        material = {
            "atlas_key": atlas_key,
            "dependency_identities": dependencies,
            "earliest_permitted_gate": earliest_gate,
            "excluded_from_throughput": False,
            "independent": True,
            "linear_identifier": linear_identifier,
            "linear_uuid": linear_uuid,
            "path_family": family,
            "production_paths": production_paths,
            "protected_lane_classification": lanes,
            "risk": risk,
            "test_paths": test_paths,
            "workload_id": workload_id,
            "workload_role": "ordinary",
        }
        declared_fingerprint = _sha(
            item.get("native_workload_fingerprint"),
            field=f"{field}.native_workload_fingerprint",
            length=64,
        )
        native_fingerprint = _canonical_digest(material)
        if declared_fingerprint != native_fingerprint:
            raise ValueError(f"{field} native workload fingerprint drifted")
        workloads.append(
            {
                **material,
                "native_workload_fingerprint": native_fingerprint,
            }
        )
    workloads.sort(key=lambda workload: cast(str, workload["atlas_key"]))
    return workloads


def _validate_v3_classifier_inputs(
    value: object, *, field: str, atlas_key: str
) -> dict[str, object]:
    inputs = _mapping(value, field=field)
    _exact_fields(
        inputs,
        expected=(
            "atlas_key",
            "component",
            "tags",
            "relevant_docs",
            "documentation_requirements",
        ),
        field=field,
    )
    if _atlas_key(inputs.get("atlas_key"), field=f"{field}.atlas_key") != atlas_key:
        raise ValueError(f"{field} contradicts the exercise Atlas identity")
    component = _text(inputs.get("component"), field=f"{field}.component")
    tags = _sorted_string_list(inputs.get("tags"), field=f"{field}.tags")
    relevant_docs = sorted(
        _repository_path(path, field=f"{field}.relevant_docs")
        for path in _string_list(
            inputs.get("relevant_docs"), field=f"{field}.relevant_docs"
        )
    )
    documentation_requirements = sorted(
        _repository_path(path, field=f"{field}.documentation_requirements")
        for path in _string_list(
            inputs.get("documentation_requirements"),
            field=f"{field}.documentation_requirements",
        )
    )
    return {
        "atlas_key": atlas_key,
        "component": component,
        "documentation_requirements": documentation_requirements,
        "relevant_docs": relevant_docs,
        "tags": tags,
    }


def _validate_v3_exercise_workloads(
    value: object,
    *,
    registry: ProtectedLaneRegistry,
    ordinary_workloads: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    raw_workloads = _list(value, field="exercise_workloads")
    if len(raw_workloads) != len(_V3_EXERCISE_ROLES):
        raise ValueError("exercise_workloads must contain the six required roles")
    ordinary_atlas = {cast(str, item["atlas_key"]) for item in ordinary_workloads}
    ordinary_identifiers = {
        cast(str, item["linear_identifier"]) for item in ordinary_workloads
    }
    ordinary_uuids = {cast(str, item["linear_uuid"]) for item in ordinary_workloads}
    touched_paths = {
        path
        for workload in ordinary_workloads
        for name in ("production_paths", "test_paths")
        for path in cast(Sequence[str], workload[name])
    }
    families = {cast(str, item["path_family"]) for item in ordinary_workloads}
    atlas_keys = set(ordinary_atlas)
    linear_identifiers = set(ordinary_identifiers)
    linear_uuids = set(ordinary_uuids)
    workload_ids: set[str] = set()
    observed_roles: set[str] = set()
    workloads: list[dict[str, object]] = []
    for index, raw in enumerate(raw_workloads):
        field = f"exercise_workloads[{index}]"
        item = _mapping(raw, field=field)
        _exact_fields(
            item,
            expected=(
                "exercise_workload_id",
                "atlas_key",
                "linear_identifier",
                "linear_uuid",
                "objective",
                "production_paths",
                "test_paths",
                "path_family",
                "classifier_inputs",
                "protected_lane_classification",
                "reconstructed_protected_lanes",
                "classifier_fingerprint",
                "dependency_identities",
                "exercise_role",
                "earliest_permitted_gate",
                "excluded_from_throughput",
            ),
            field=field,
        )
        workload_id = _text(
            item.get("exercise_workload_id"), field=f"{field}.exercise_workload_id"
        )
        atlas_key = _atlas_key(item.get("atlas_key"), field=f"{field}.atlas_key")
        linear_identifier = _linear_identifier(
            item.get("linear_identifier"), field=f"{field}.linear_identifier"
        )
        linear_uuid = _linear_uuid(
            item.get("linear_uuid"), field=f"{field}.linear_uuid"
        )
        if workload_id in workload_ids:
            raise ValueError("exercise_workloads contain duplicate identities")
        if (
            atlas_key in atlas_keys
            or linear_identifier in linear_identifiers
            or linear_uuid in linear_uuids
        ):
            raise ValueError(
                "ordinary and exercise workloads contain duplicate Atlas or "
                "Linear identities"
            )
        workload_ids.add(workload_id)
        atlas_keys.add(atlas_key)
        linear_identifiers.add(linear_identifier)
        linear_uuids.add(linear_uuid)
        objective = _text(
            item.get("objective"), field=f"{field}.objective", maximum=2000
        )
        production_paths = sorted(
            _repository_path(path, field=f"{field}.production_paths")
            for path in _string_list(
                item.get("production_paths"), field=f"{field}.production_paths"
            )
        )
        test_paths = sorted(
            _repository_path(path, field=f"{field}.test_paths")
            for path in _string_list(
                item.get("test_paths"), field=f"{field}.test_paths"
            )
        )
        workload_paths = set(production_paths) | set(test_paths)
        if (
            not workload_paths
            or len(workload_paths) != len(production_paths) + len(test_paths)
            or not touched_paths.isdisjoint(workload_paths)
        ):
            raise ValueError("exercise workload paths must be non-empty and disjoint")
        touched_paths.update(workload_paths)
        family = _text(item.get("path_family"), field=f"{field}.path_family")
        if family in families:
            raise ValueError("ordinary and exercise path families must be distinct")
        families.add(family)
        classifier_inputs = _validate_v3_classifier_inputs(
            item.get("classifier_inputs"),
            field=f"{field}.classifier_inputs",
            atlas_key=atlas_key,
        )
        classification = classify_protected_lane_inputs(
            ProtectedLaneClassifierInput(
                ticket_key=atlas_key,
                component=cast(str, classifier_inputs["component"]),
                tags=tuple(cast(Sequence[str], classifier_inputs["tags"])),
                relevant_docs=tuple(
                    cast(Sequence[str], classifier_inputs["relevant_docs"])
                ),
                documentation_requirements=tuple(
                    cast(
                        Sequence[str],
                        classifier_inputs["documentation_requirements"],
                    )
                ),
            ),
            registry,
        )
        if classification.issues or len(classification.lanes) != 1:
            raise ValueError(
                f"{field} must recompute into exactly one unambiguous protected lane"
            )
        recomputed = _classification_projection(classification)
        declared = _validate_declared_classification(
            item.get("protected_lane_classification"),
            field=f"{field}.protected_lane_classification",
        )
        if declared != recomputed:
            raise ValueError(f"{field} classifier reconstruction mismatch")
        reconstructed_lanes = sorted(
            _string_list(
                item.get("reconstructed_protected_lanes"),
                field=f"{field}.reconstructed_protected_lanes",
            )
        )
        if reconstructed_lanes != sorted(classification.lanes):
            raise ValueError(f"{field} reconstructed protected lanes drifted")
        classifier_fingerprint = _sha(
            item.get("classifier_fingerprint"),
            field=f"{field}.classifier_fingerprint",
            length=64,
        )
        if classifier_fingerprint != classification.fingerprint:
            raise ValueError(f"{field} classifier fingerprint drifted")
        dependencies = _dependency_identities(
            item.get("dependency_identities"),
            field=f"{field}.dependency_identities",
        )
        if any(dependency["atlas_key"] == atlas_key for dependency in dependencies):
            raise ValueError(f"{field} dependency identities contradict the workload")
        exercise_role = _text(item.get("exercise_role"), field=f"{field}.exercise_role")
        role_contract = _V3_EXERCISE_ROLES.get(exercise_role)
        if role_contract is None or exercise_role in observed_roles:
            raise ValueError("exercise_workloads do not contain the six required roles")
        observed_roles.add(exercise_role)
        earliest_gate = _integer(
            item.get("earliest_permitted_gate"),
            field=f"{field}.earliest_permitted_gate",
        )
        if earliest_gate != role_contract[0]:
            raise ValueError(f"{field} earliest gate contradicts its exercise role")
        lane = classification.lanes[0]
        if lane != role_contract[3]:
            raise ValueError(f"{field} exercise role binds the wrong protected lane")
        if item.get("excluded_from_throughput") is not True:
            raise ValueError(f"{field} must be explicitly excluded from throughput")
        workloads.append(
            {
                "atlas_key": atlas_key,
                "classifier_fingerprint": classification.fingerprint,
                "classifier_inputs": classifier_inputs,
                "dependency_identities": dependencies,
                "earliest_permitted_gate": earliest_gate,
                "excluded_from_throughput": True,
                "exercise_role": exercise_role,
                "exercise_workload_id": workload_id,
                "linear_identifier": linear_identifier,
                "linear_uuid": linear_uuid,
                "objective": objective,
                "path_family": family,
                "production_paths": production_paths,
                "protected_lane_classification": recomputed,
                "reconstructed_protected_lanes": reconstructed_lanes,
                "test_paths": test_paths,
            }
        )
    if observed_roles != set(_V3_EXERCISE_ROLES):
        raise ValueError("exercise_workloads do not contain the six required roles")
    workloads.sort(key=lambda workload: cast(str, workload["exercise_role"]))
    return workloads


def _validate_v3_exclusions(
    value: object, *, exercise_workloads: Sequence[Mapping[str, object]]
) -> list[dict[str, str]]:
    expected_keys = {cast(str, item["atlas_key"]) for item in exercise_workloads}
    exclusions: list[dict[str, str]] = []
    observed: set[str] = set()
    for index, raw in enumerate(_list(value, field="explicit_exclusions")):
        field = f"explicit_exclusions[{index}]"
        item = _mapping(raw, field=field)
        _exact_fields(item, expected=("atlas_key", "reason"), field=field)
        atlas_key = _atlas_key(item.get("atlas_key"), field=f"{field}.atlas_key")
        if atlas_key in observed:
            raise ValueError("explicit_exclusions contains duplicate identities")
        if item.get("reason") != "protected-lane-exercise":
            raise ValueError("explicit_exclusions contains an unsupported reason")
        observed.add(atlas_key)
        exclusions.append({"atlas_key": atlas_key, "reason": "protected-lane-exercise"})
    if observed != expected_keys:
        raise ValueError("explicit_exclusions must name every exercise workload")
    exclusions.sort(key=lambda exclusion: exclusion["atlas_key"])
    return exclusions


def _validate_v3_dependency_consistency(
    workloads: Sequence[Mapping[str, object]],
    exercise_workloads: Sequence[Mapping[str, object]],
) -> None:
    primary = {
        cast(str, item["atlas_key"]): (
            cast(str, item["linear_identifier"]),
            cast(str, item["linear_uuid"]),
            cast(int, item["earliest_permitted_gate"]),
        )
        for item in (*workloads, *exercise_workloads)
    }
    linear_identifier_owner = {
        identity[0]: atlas_key for atlas_key, identity in primary.items()
    }
    linear_uuid_owner = {
        identity[1]: atlas_key for atlas_key, identity in primary.items()
    }
    dependency_by_atlas: dict[str, tuple[str, str]] = {}
    for workload in (*workloads, *exercise_workloads):
        workload_key = cast(str, workload["atlas_key"])
        earliest_gate = cast(int, workload["earliest_permitted_gate"])
        for dependency in cast(
            Sequence[Mapping[str, str]], workload["dependency_identities"]
        ):
            dependency_key = dependency["atlas_key"]
            dependency_identity = (
                dependency["linear_identifier"],
                dependency["linear_uuid"],
            )
            prior = dependency_by_atlas.setdefault(dependency_key, dependency_identity)
            if prior != dependency_identity:
                raise ValueError("dependency identities contain a contradiction")
            identifier_owner = linear_identifier_owner.setdefault(
                dependency_identity[0], dependency_key
            )
            uuid_owner = linear_uuid_owner.setdefault(
                dependency_identity[1], dependency_key
            )
            if identifier_owner != dependency_key or uuid_owner != dependency_key:
                raise ValueError("dependency identities contradict workload authority")
            declared = primary.get(dependency_key)
            if declared is not None and (
                dependency_identity != declared[:2]
                or declared[2] > earliest_gate
                or dependency_key == workload_key
            ):
                raise ValueError("dependency identities contradict workload authority")


def _validate_manifest_v3(
    value: Mapping[str, Any], *, verify_fingerprint: bool = True
) -> dict[str, object]:
    _scan_for_secrets(value, field="manifest")
    _exact_fields(
        value,
        expected=(
            "schema_version",
            "validation_scope",
            "milestone_ticket",
            "atlas_ticket",
            "governing_origin_main_sha",
            "milestone_branch",
            "milestone_head_sha",
            "milestone_base_sha",
            "ratified_at",
            "phase_15_5_release",
            "operational_limits",
            "protected_lane_registry_version",
            "protected_lane_registry_fingerprint",
            "workloads",
            "exercise_catalog",
            "exercise_workloads",
            "exercise_bindings",
            "throughput_numerator",
            "explicit_exclusions",
            "manifest_fingerprint",
        ),
        field="manifest",
    )
    if value.get("schema_version") != MANIFEST_SCHEMA_V3:
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
    governing_origin = _sha(
        value.get("governing_origin_main_sha"),
        field="manifest.governing_origin_main_sha",
    )
    milestone_head = _sha(
        value.get("milestone_head_sha"), field="manifest.milestone_head_sha"
    )
    milestone_base = _sha(
        value.get("milestone_base_sha"), field="manifest.milestone_base_sha"
    )
    if milestone_base != governing_origin:
        raise ValueError("manifest milestone base contradicts governing origin/main")
    registry = _load_protected_lane_registry()
    registry_version = _text(
        value.get("protected_lane_registry_version"),
        field="manifest.protected_lane_registry_version",
        maximum=128,
    )
    registry_fingerprint = _sha(
        value.get("protected_lane_registry_fingerprint"),
        field="manifest.protected_lane_registry_fingerprint",
        length=64,
    )
    if registry_version != registry.version:
        raise ValueError("manifest protected lane registry version drifted")
    if registry_fingerprint != registry.fingerprint:
        raise ValueError("manifest protected lane registry fingerprint drifted")
    ratified_at = _instant(value.get("ratified_at"), field="manifest.ratified_at")
    release = _validate_phase_15_5_release(value.get("phase_15_5_release"))
    limits = _validate_limits(value.get("operational_limits"))
    workloads = _validate_v3_workloads(value.get("workloads"), registry=registry)
    exercises = _validate_exercise_catalog(
        value.get("exercise_catalog"), canonical_order=True
    )
    exercise_workloads = _validate_v3_exercise_workloads(
        value.get("exercise_workloads"),
        registry=registry,
        ordinary_workloads=workloads,
    )
    _validate_v3_dependency_consistency(workloads, exercise_workloads)
    exercise_bindings = _validate_exercise_bindings(
        value.get("exercise_bindings"),
        exercise_workloads=exercise_workloads,
        exercise_catalog=exercises,
    )
    exercise_by_id = {
        cast(str, item["exercise_workload_id"]): item for item in exercise_workloads
    }
    for binding in exercise_bindings:
        workload = exercise_by_id[cast(str, binding["exercise_workload_id"])]
        expected_gate, expected_exercise, expected_role, _expected_lane = (
            _V3_EXERCISE_ROLES[cast(str, workload["exercise_role"])]
        )
        if (
            binding["gate_level"] != expected_gate
            or binding["exercise_id"] != expected_exercise
            or binding["role"] != expected_role
        ):
            raise ValueError("exercise binding contradicts the declared exercise role")
    numerator = sorted(
        _atlas_key(item, field="throughput_numerator")
        for item in _string_list(
            value.get("throughput_numerator"), field="throughput_numerator"
        )
    )
    expected_numerator = sorted(cast(str, item["atlas_key"]) for item in workloads)
    if numerator != expected_numerator:
        raise ValueError(
            "throughput numerator includes an excluded exercise or omission"
        )
    exclusions = _validate_v3_exclusions(
        value.get("explicit_exclusions"), exercise_workloads=exercise_workloads
    )
    selected = {
        "atlas_ticket": "ATLAS-253",
        "exercise_bindings": exercise_bindings,
        "exercise_catalog": exercises,
        "exercise_workloads": exercise_workloads,
        "explicit_exclusions": exclusions,
        "governing_origin_main_sha": governing_origin,
        "milestone_base_sha": milestone_base,
        "milestone_branch": MILESTONE_BRANCH,
        "milestone_head_sha": milestone_head,
        "milestone_ticket": "ATL-410",
        "operational_limits": limits,
        "phase_15_5_release": release,
        "protected_lane_registry_fingerprint": registry.fingerprint,
        "protected_lane_registry_version": registry.version,
        "ratified_at": ratified_at.isoformat().replace("+00:00", "Z"),
        "schema_version": MANIFEST_SCHEMA_V3,
        "throughput_numerator": numerator,
        "validation_scope": VALIDATION_SCOPE,
        "workloads": workloads,
    }
    declared_fingerprint = _sha(
        value.get("manifest_fingerprint"),
        field="manifest.manifest_fingerprint",
        length=64,
    )
    canonical_fingerprint = _canonical_digest(selected)
    if verify_fingerprint and declared_fingerprint != canonical_fingerprint:
        raise ValueError("manifest canonical fingerprint drifted")
    return {**selected, "manifest_fingerprint": canonical_fingerprint}


def calculate_v3_manifest_fingerprint(value: Mapping[str, Any]) -> str:
    """Calculate v3's canonical projection digest without accepting it as valid."""

    selected = _validate_manifest_v3(value, verify_fingerprint=False)
    return cast(str, selected["manifest_fingerprint"])


def validate_manifest(value: Mapping[str, Any]) -> dict[str, object]:
    """Validate one historical v1/v2 or live-freeze-authoritative v3 manifest."""

    schema = value.get("schema_version")
    if schema == MANIFEST_SCHEMA_V1:
        return _validate_manifest_v1(value)
    if schema == MANIFEST_SCHEMA_V2:
        return _validate_manifest_v2(value)
    if schema == MANIFEST_SCHEMA_V3:
        return _validate_manifest_v3(value)
    raise ValueError("manifest schema_version is unsupported")


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
    procedure = _text(
        runtime.get("supported_procedure_id"),
        field=f"gate_{gate}.runtime_configuration.supported_procedure_id",
    )
    base_fields = (
        "instance_id",
        "supported_procedure_id",
        "loaded_commit_sha",
        "workflow_blob_sha",
        "configured_ceiling",
        "max_turns",
        "loaded_at",
        "proof_observed_at",
        "proof_identity",
    )
    expected_fields: Sequence[str]
    if procedure == FIXTURE_RUNTIME_PROCEDURE:
        expected_fields = base_fields
    elif procedure == LIVE_RUNTIME_PROCEDURE:
        expected_fields = (
            *base_fields,
            "service_unit",
            "symphony_commit_sha",
            "workflow_content_sha256",
        )
    else:
        raise ValueError(f"gate {gate} runtime procedure is unsupported")
    _exact_fields(
        runtime,
        expected=expected_fields,
        field=f"gate_{gate}.runtime_configuration",
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
    selected: dict[str, object] = {
        "configured_ceiling": gate,
        "instance_id": _text(
            runtime.get("instance_id"),
            field=f"gate_{gate}.runtime_configuration.instance_id",
        ),
        "loaded_at": loaded_at.isoformat().replace("+00:00", "Z"),
        "loaded_commit_sha": loaded_commit,
        "max_turns": 10,
        "proof_observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "supported_procedure_id": procedure,
        "workflow_blob_sha": blob,
    }
    proof_identity = _sha(
        runtime.get("proof_identity"),
        field=f"gate_{gate}.runtime_configuration.proof_identity",
        length=64,
    )
    if procedure == LIVE_RUNTIME_PROCEDURE:
        service_unit = _text(
            runtime.get("service_unit"),
            field=f"gate_{gate}.runtime_configuration.service_unit",
        )
        if service_unit != LIVE_RUNTIME_SERVICE_UNIT:
            raise ValueError(f"gate {gate} runtime service unit is unsupported")
        symphony_commit = _sha(
            runtime.get("symphony_commit_sha"),
            field=f"gate_{gate}.runtime_configuration.symphony_commit_sha",
        )
        if symphony_commit != LIVE_SYMPHONY_COMMIT_SHA:
            raise ValueError(f"gate {gate} Symphony release identity is unsupported")
        selected.update(
            {
                "service_unit": service_unit,
                "symphony_commit_sha": symphony_commit,
                "workflow_content_sha256": _sha(
                    runtime.get("workflow_content_sha256"),
                    field=(
                        f"gate_{gate}.runtime_configuration.workflow_content_sha256"
                    ),
                    length=64,
                ),
            }
        )
        if proof_identity != _canonical_digest(selected):
            raise ValueError(f"gate {gate} runtime proof identity is incoherent")
    return {**selected, "proof_identity": proof_identity}


def _validate_protected_lane_occupancy(
    value: object,
    *,
    gate: int,
    registry: ProtectedLaneRegistry | None = None,
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
        count = _integer(item.get("count"), field=f"{field}.count")
        limit = _integer(item.get("limit"), field=f"{field}.limit", minimum=1)
        operator_declared = _boolean(
            item.get("operator_declared"),
            field=f"{field}.operator_declared",
        )
        ticket_keys = _string_list(
            item.get("ticket_keys"), field=f"{field}.ticket_keys"
        )
        if registry is not None:
            try:
                definition = registry.lane(lane)
            except StopIteration as error:
                raise ValueError(
                    f"gate {gate} protected lane state uses an unknown lane"
                ) from error
            if (
                limit != definition.capacity
                or operator_declared != definition.operator_declared
            ):
                raise ValueError(
                    f"gate {gate} protected lane state disagrees with the registry"
                )
            if count != len(ticket_keys):
                raise ValueError(
                    f"gate {gate} protected lane occupancy count is incoherent"
                )
            if any(_REAL_TICKET_KEY_RE.fullmatch(key) is None for key in ticket_keys):
                raise ValueError(
                    f"gate {gate} protected lane occupancy uses a non-ticket identity"
                )
            ticket_keys.sort()
        result.append(
            {
                "count": count,
                "lane": lane,
                "limit": limit,
                "operator_declared": operator_declared,
                "ticket_keys": ticket_keys,
            }
        )
    if registry is not None:
        result.sort(key=lambda occupancy: cast(str, occupancy["lane"]))
    return result


def _validate_snapshot(
    value: object, *, gate: int, manifest: Mapping[str, object]
) -> dict[str, object]:
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
    registry: ProtectedLaneRegistry | None = None
    if manifest["schema_version"] in {MANIFEST_SCHEMA_V2, MANIFEST_SCHEMA_V3}:
        registry = _load_protected_lane_registry()
        if registry_version != manifest["protected_lane_registry_version"]:
            raise ValueError(
                f"gate {gate} protected lane registry version drifted from manifest"
            )
        if snapshot.get("protected_lane_registry_fingerprint") != manifest.get(
            "protected_lane_registry_fingerprint"
        ):
            raise ValueError(
                f"gate {gate} protected lane registry fingerprint drifted from manifest"
            )
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
            snapshot.get("protected_lane_occupancy"), gate=gate, registry=registry
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


def _validate_protected_lane_exercise_evidence(
    value: object,
    *,
    gate: int,
    manifest: Mapping[str, object],
    snapshot: Mapping[str, object],
    exercises: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    raw_evidence = _list(value, field=f"gate_{gate}.protected_lane_exercise_evidence")
    bindings = [
        cast(Mapping[str, object], binding)
        for binding in cast(Sequence[object], manifest["exercise_bindings"])
        if cast(Mapping[str, object], binding)["gate_level"] == gate
    ]
    workloads = {
        cast(str, cast(Mapping[str, object], workload)["exercise_workload_id"]): cast(
            Mapping[str, object], workload
        )
        for workload in cast(Sequence[object], manifest["exercise_workloads"])
    }
    bindings_by_workload = {
        cast(str, binding["exercise_workload_id"]): binding for binding in bindings
    }
    if len(raw_evidence) != len(bindings):
        raise ValueError(
            f"gate {gate} lacks exact workload-bound protected lane evidence"
        )
    selected_evidence: list[dict[str, object]] = []
    observed_workloads: set[str] = set()
    observed_tickets: set[str] = set()
    evidence_identities: set[str] = set()
    for index, raw in enumerate(raw_evidence):
        field = f"gate_{gate}.protected_lane_exercise_evidence[{index}]"
        item = _mapping(raw, field=field)
        _exact_fields(
            item,
            expected=(
                "gate_level",
                "exercise_id",
                "exercise_workload_id",
                "ticket_key",
                "role",
                "protected_lane",
                "observed_status",
                "evidence_identity",
                "workload_manifest_fingerprint",
            ),
            field=field,
        )
        workload_id = _text(
            item.get("exercise_workload_id"),
            field=f"{field}.exercise_workload_id",
        )
        if workload_id in observed_workloads:
            raise ValueError(
                f"gate {gate} protected lane evidence duplicates a workload"
            )
        observed_workloads.add(workload_id)
        binding = bindings_by_workload.get(workload_id)
        workload = workloads.get(workload_id)
        if binding is None or workload is None:
            raise ValueError(
                f"gate {gate} protected lane evidence names an undeclared workload"
            )
        exercise_id = _text(item.get("exercise_id"), field=f"{field}.exercise_id")
        role = _text(item.get("role"), field=f"{field}.role")
        if (
            item.get("gate_level") != gate
            or binding["gate_level"] != gate
            or exercise_id != binding["exercise_id"]
            or role != binding["role"]
        ):
            raise ValueError(
                f"gate {gate} protected lane evidence uses the wrong "
                "gate, exercise or role"
            )
        ticket_key = _text(item.get("ticket_key"), field=f"{field}.ticket_key")
        if ticket_key in observed_tickets:
            raise ValueError(
                f"gate {gate} protected lane evidence duplicates a ticket identity"
            )
        observed_tickets.add(ticket_key)
        if _REAL_TICKET_KEY_RE.fullmatch(
            ticket_key
        ) is None or ticket_key != _exercise_ticket_key(workload):
            raise ValueError(
                f"gate {gate} protected lane evidence substitutes a ticket identity"
            )
        lane = _text(
            item.get("protected_lane"),
            field=f"{field}.protected_lane",
            maximum=128,
        )
        if lane != _exercise_workload_lane(workload):
            raise ValueError(
                f"gate {gate} protected lane evidence disagrees with classification"
            )
        expected_status = "CI Pending" if role == "owner" else "Ready for Agent"
        observed_status = _text(
            item.get("observed_status"), field=f"{field}.observed_status"
        )
        if observed_status != expected_status:
            raise ValueError(
                f"gate {gate} protected lane evidence has incoherent occupancy status"
            )
        evidence_identity = _sha(
            item.get("evidence_identity"),
            field=f"{field}.evidence_identity",
            length=64,
        )
        if evidence_identity in evidence_identities:
            raise ValueError(
                f"gate {gate} protected lane evidence duplicates an evidence identity"
            )
        evidence_identities.add(evidence_identity)
        if item.get("workload_manifest_fingerprint") != manifest.get(
            "manifest_fingerprint"
        ):
            raise ValueError(
                f"gate {gate} protected lane evidence uses a stale manifest fingerprint"
            )
        selected_evidence.append(
            {
                "evidence_identity": evidence_identity,
                "exercise_id": exercise_id,
                "exercise_workload_id": workload_id,
                "gate_level": gate,
                "observed_status": observed_status,
                "protected_lane": lane,
                "role": role,
                "ticket_key": ticket_key,
                "workload_manifest_fingerprint": manifest["manifest_fingerprint"],
            }
        )
    if observed_workloads != set(bindings_by_workload):
        raise ValueError(f"gate {gate} protected lane evidence is orphaned or missing")
    selected_evidence.sort(
        key=lambda evidence: (
            cast(str, evidence["exercise_id"]),
            cast(str, evidence["role"]),
            cast(str, evidence["exercise_workload_id"]),
        )
    )
    occupancy = {
        cast(str, cast(Mapping[str, object], item)["lane"]): cast(
            Mapping[str, object], item
        )
        for item in cast(Sequence[object], snapshot["protected_lane_occupancy"])
    }
    occupied_ticket_keys = {
        ticket_key
        for item in occupancy.values()
        for ticket_key in cast(Sequence[str], item["ticket_keys"])
    }
    for exercise_id in sorted(
        {cast(str, binding["exercise_id"]) for binding in bindings}
    ):
        exercise_evidence = [
            evidence
            for evidence in selected_evidence
            if evidence["exercise_id"] == exercise_id
        ]
        if exercises[exercise_id]["evidence_identity"] != _canonical_digest(
            exercise_evidence
        ):
            raise ValueError(
                f"gate {gate} protected exercise evidence identity is unbound"
            )
        owners = [
            evidence for evidence in exercise_evidence if evidence["role"] == "owner"
        ]
        if len(owners) != 1:
            raise ValueError(
                f"gate {gate} protected exercise {exercise_id} requires one owner"
            )
        owner = owners[0]
        lane_state = occupancy.get(cast(str, owner["protected_lane"]))
        if lane_state is None or lane_state["ticket_keys"] != [owner["ticket_key"]]:
            raise ValueError(
                f"gate {gate} protected exercise owner mismatches lane occupancy"
            )
        blocked = [
            evidence
            for evidence in exercise_evidence
            if evidence["role"] == "blocked_candidate"
        ]
        if any(
            evidence["protected_lane"] != owner["protected_lane"]
            or evidence["ticket_key"] in occupied_ticket_keys
            for evidence in blocked
        ):
            raise ValueError(
                f"gate {gate} protected blocked candidate mismatches lane occupancy"
            )
    return selected_evidence


def _validate_receipt(
    raw: Mapping[str, Any],
    *,
    expected_gate: int,
    expected_previous_id: str | None,
    previous_proven_level: int,
    manifest: Mapping[str, object],
) -> dict[str, object]:
    _scan_for_secrets(raw, field=f"gate_{expected_gate}_receipt")
    is_v2 = manifest["schema_version"] in {
        MANIFEST_SCHEMA_V2,
        MANIFEST_SCHEMA_V3,
    }
    receipt_schema = RECEIPT_SCHEMA_V2 if is_v2 else RECEIPT_SCHEMA_V1
    receipt_fields = (
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
    )
    _exact_fields(
        raw,
        expected=(*receipt_fields, "protected_lane_exercise_evidence")
        if is_v2
        else receipt_fields,
        field=f"gate_{expected_gate}_receipt",
    )
    if raw.get("schema_version") != receipt_schema:
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
    if (
        expected_gate == 1
        and manifest["schema_version"] == MANIFEST_SCHEMA_V3
        and (
            current_origin != manifest["governing_origin_main_sha"]
            or current_merge_base != manifest["milestone_base_sha"]
            or milestone_commit != manifest["milestone_head_sha"]
        )
    ):
        raise ValueError(
            "Gate 1 receipt contradicts the repository-authoritative v3 freeze"
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
    if is_v2:
        policy["risk_lane_limits"] = sorted(
            cast(Sequence[Mapping[str, object]], policy["risk_lane_limits"]),
            key=lambda item: cast(str, item["risk_level"]),
        )
        policy["component_lane_limits"] = sorted(
            cast(Sequence[Mapping[str, object]], policy["component_lane_limits"]),
            key=lambda item: cast(str, item["component"]),
        )
    snapshot = _validate_snapshot(
        raw.get("snapshot"), gate=expected_gate, manifest=manifest
    )
    observation = _validate_observation(raw.get("observation"), gate=expected_gate)
    if _instant(
        observation["started_at"], field=f"gate_{expected_gate}.observation.started_at"
    ) <= _instant(manifest["ratified_at"], field="manifest.ratified_at"):
        raise ValueError(f"gate {expected_gate} began before workload ratification")
    runtime_observed = _instant(
        runtime["proof_observed_at"],
        field=f"gate_{expected_gate}.runtime_configuration.proof_observed_at",
    )
    runtime_loaded = _instant(
        runtime["loaded_at"],
        field=f"gate_{expected_gate}.runtime_configuration.loaded_at",
    )
    window_started = _instant(
        observation["started_at"], field=f"gate_{expected_gate}.observation.started_at"
    )
    manifest_ratified = _instant(manifest["ratified_at"], field="manifest.ratified_at")
    if runtime_loaded < manifest_ratified or runtime_observed < manifest_ratified:
        raise ValueError(f"gate {expected_gate} runtime proof is stale")
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
    protected_lane_exercise_evidence: list[dict[str, object]] | None = None
    if is_v2:
        protected_lane_exercise_evidence = _validate_protected_lane_exercise_evidence(
            raw.get("protected_lane_exercise_evidence"),
            gate=expected_gate,
            manifest=manifest,
            snapshot=snapshot,
            exercises=exercises,
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
    if is_v2:
        stop_reasons.sort()
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
        "schema_version": receipt_schema,
        "snapshot": snapshot,
        "stop_reasons": stop_reasons,
        "workflow_blob_sha": workflow_blob,
        "workload_manifest_fingerprint": manifest["manifest_fingerprint"],
    }
    if protected_lane_exercise_evidence is not None:
        selected["protected_lane_exercise_evidence"] = protected_lane_exercise_evidence
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
        evaluated_decision = f"FAIL_GATE_{failed_gate}"
        passed = False
    elif len(receipts) < len(GATE_LEVELS):
        evaluated_decision = f"PENDING_GATE_{GATE_LEVELS[len(receipts)]}"
        passed = False
    else:
        evaluated_decision = "RECEIPT_SEQUENCE_VALIDATED"
        passed = True
    schema = manifest["schema_version"]
    historical_only = schema in {MANIFEST_SCHEMA_V1, MANIFEST_SCHEMA_V2}
    manifest_authority = {
        MANIFEST_SCHEMA_V1: "historical-receipt-replay-v1",
        MANIFEST_SCHEMA_V2: "historical-schema-valid-v2",
        MANIFEST_SCHEMA_V3: "live-freeze-authoritative-v3",
    }[cast(str, schema)]
    if schema == MANIFEST_SCHEMA_V1:
        decision = "HISTORICAL_RECEIPT_REPLAY"
    elif schema == MANIFEST_SCHEMA_V2:
        decision = "HISTORICAL_SCHEMA_VALIDATION"
    else:
        decision = evaluated_decision
    if historical_only:
        passed = False
    report: dict[str, object] = {
        "authority_spies": {
            "external_write_count": 0,
            "network_call_count": 0,
            "prohibited_action_count": 0,
            "repository_mutation_count": 0,
        },
        "closure_authorized": False,
        "decision": decision,
        "gate_sequence": [receipt["gate_level"] for receipt in receipts],
        "last_validated_pass_receipt_level": last_validated_pass,
        "manifest_fingerprint": manifest["manifest_fingerprint"],
        "manifest_authority": manifest_authority,
        "manifest_schema_version": manifest["schema_version"],
        "milestone_branch": MILESTONE_BRANCH,
        "next_gate": None
        if len(receipts) == len(GATE_LEVELS)
        else GATE_LEVELS[len(receipts)],
        "phase_15_5_entry_gate": manifest["phase_15_5_release"],
        "historical_only": historical_only,
        "historical_result": evaluated_decision if historical_only else None,
        "live_freeze_authoritative": schema == MANIFEST_SCHEMA_V3,
        "transition_authorized": False,
        "receipt_summaries": [
            {
                "gate_level": receipt["gate_level"],
                "milestone_commit_sha": receipt["milestone_commit_sha"],
                "outcome": receipt["outcome"],
                "receipt_fingerprint": receipt["receipt_fingerprint"],
                "receipt_id": receipt["receipt_id"],
                "retained_or_restored_level": receipt["retained_or_restored_level"],
                "runtime_identity": receipt["runtime_configuration"],
                "runtime_proof_identity": cast(
                    Mapping[str, object], receipt["runtime_configuration"]
                )["proof_identity"],
                "stop_reasons": receipt["stop_reasons"],
            }
            for receipt in receipts
        ],
        "schema_version": REPORT_SCHEMA,
        "validation_scope": VALIDATION_SCOPE,
        "exercise_workload_count": len(
            cast(Sequence[object], manifest.get("exercise_workloads", []))
        ),
        "classification_fingerprints": [
            cast(Mapping[str, object], workload).get(
                "classifier_fingerprint",
                cast(Mapping[str, object], workload).get("classification_fingerprint"),
            )
            for workload in cast(
                Sequence[object], manifest.get("exercise_workloads", [])
            )
        ],
        "workload_count": len(cast(Sequence[object], manifest["workloads"])),
        "workload_identities": [
            cast(Mapping[str, object], workload).get(
                "native_workload_fingerprint",
                cast(Mapping[str, object], workload).get("workload_identity"),
            )
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
                    "closure_authorized": False,
                    "decision": "INVALID_INPUT",
                    "error": "bounded ramp input validation failed",
                    "schema_version": REPORT_SCHEMA,
                    "transition_authorized": False,
                    "validation_scope": VALIDATION_SCOPE,
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

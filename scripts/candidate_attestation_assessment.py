"""ATLAS-260 system-tier synthetic-candidate attestation assessment.

This disposable harness models the bounded output of an Atlas-owned Sigstore
verification boundary plus bounded GitHub REST reads.  It is an assessment
artifact, not a production acceptance path.  It has no network client or
provider writer and creates Git objects only in a disposable repository.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from scripts.exact_base_candidate_spike import (
    GitRunner,
    _run_git,
    exercise_disposable_repository,
)

SCHEMA_VERSION = 1
MAX_FIXTURE_BYTES = 256 * 1024
MAX_CASES = 32
MAX_REQUIRED_RESULTS = 64
MAX_TEXT = 200
MAX_RETAINED_BYTES = 16 * 1024
_SHA1_LENGTH = 40
_SHA256_LENGTH = 64


class Decision(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class Mergeability(StrEnum):
    MERGEABLE = "mergeable"
    CONFLICTED = "conflicted"
    INDETERMINATE = "indeterminate"


class ReasonCode(StrEnum):
    MALFORMED_OBSERVATION = "malformed_observation"
    UNBOUNDED_EVIDENCE = "unbounded_evidence"
    PROVIDER_AMBIGUITY = "provider_ambiguity"
    HEAD_MOVED = "head_moved"
    BASE_MOVED = "base_moved"
    CANDIDATE_MISSING = "candidate_missing"
    CANDIDATE_CONFLICTED = "candidate_conflicted"
    MERGEABILITY_INDETERMINATE = "mergeability_indeterminate"
    CANDIDATE_PARENT_MISMATCH = "candidate_parent_mismatch"
    CANDIDATE_MOVED = "candidate_moved"
    REQUIRED_CHECK_SET_FINGERPRINT_INVALID = "required_check_set_fingerprint_invalid"
    REQUIRED_CHECK_SET_MOVED = "required_check_set_moved"
    WORKFLOW_IDENTITY_MOVED = "workflow_identity_moved"
    RUN_REPLACED = "run_replaced"
    RUN_ATTEMPT_REPLACED = "run_attempt_replaced"
    UNTRUSTED_PRODUCER = "untrusted_producer"
    ATTESTATION_UNVERIFIED = "attestation_unverified"
    ATTESTATION_SUBJECT_MISMATCH = "attestation_subject_mismatch"
    ATTESTATION_IDENTITY_MISMATCH = "attestation_identity_mismatch"
    REQUIRED_RESULT_MISSING = "required_result_missing"
    REQUIRED_RESULT_AMBIGUOUS = "required_result_ambiguous"
    REQUIRED_RESULT_NOT_PASSED = "required_result_not_passed"
    REQUIRED_RESULT_CANDIDATE_MISMATCH = "required_result_candidate_mismatch"
    REQUIRED_RESULT_LIFECYCLE_MISMATCH = "required_result_lifecycle_mismatch"


class UnboundedEvidenceError(ValueError):
    """A provider collection crossed the assessment retention boundary."""


@dataclass(frozen=True)
class Assessment:
    decision: Decision
    reasons: tuple[ReasonCode, ...]
    authoritative_identity: Mapping[str, object] | None

    def payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "authoritative_identity": self.authoritative_identity,
            "decision": self.decision.value,
            "reasons": [reason.value for reason in self.reasons],
        }
        if len(_canonical_bytes(payload)) > MAX_RETAINED_BYTES:
            return {
                "authoritative_identity": None,
                "decision": Decision.FAIL.value,
                "reasons": [ReasonCode.UNBOUNDED_EVIDENCE.value],
            }
        return payload


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _sha256_payload(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _mapping(value: object, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return cast(Mapping[str, Any], value)


def _list(value: object, *, field: str, maximum: int) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    if len(value) > maximum:
        raise UnboundedEvidenceError(f"{field} exceeds its bounded limit")
    return cast(list[object], value)


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT:
        raise ValueError(f"{field} must be bounded non-empty text")
    return value


def _positive_int(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _bool(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _hex(value: object, *, field: str, length: int) -> str:
    if (
        not isinstance(value, str)
        or len(value) != length
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field} must be a lowercase {length}-digit hex value")
    return value


def _sha1(value: object, *, field: str) -> str:
    return _hex(value, field=field, length=_SHA1_LENGTH)


def _sha256(value: object, *, field: str) -> str:
    return _hex(value, field=field, length=_SHA256_LENGTH)


def _optional_sha1(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    return _sha1(value, field=field)


def _normalise_member(value: object, *, field: str) -> dict[str, object]:
    item = _mapping(value, field=field)
    return {
        "app_id": _positive_int(item.get("app_id"), field=f"{field}.app_id"),
        "name": _text(item.get("name"), field=f"{field}.name"),
    }


def _member_key(value: Mapping[str, object]) -> tuple[str, int]:
    return cast(str, value["name"]), cast(int, value["app_id"])


def _normalise_policy(value: object, *, field: str) -> dict[str, object]:
    item = _mapping(value, field=field)
    return {
        "blob_sha": _sha1(item.get("blob_sha"), field=f"{field}.blob_sha"),
        "path": _text(item.get("path"), field=f"{field}.path"),
        "repository": _text(item.get("repository"), field=f"{field}.repository"),
        "sha": _sha1(item.get("sha"), field=f"{field}.sha"),
    }


def _normalise_required_set(value: object) -> dict[str, object]:
    item = _mapping(value, field="required_check_set")
    raw_members = _list(
        item.get("members"),
        field="required_check_set.members",
        maximum=MAX_REQUIRED_RESULTS,
    )
    if not raw_members:
        raise ValueError("required_check_set.members must not be empty")
    members = sorted(
        (
            _normalise_member(member, field=f"required_check_set.members[{index}]")
            for index, member in enumerate(raw_members)
        ),
        key=_member_key,
    )
    if len({_member_key(member) for member in members}) != len(members):
        raise ValueError("required_check_set.members contains a duplicate")
    fingerprint_input = {
        "members": members,
        "policy": _normalise_policy(
            item.get("policy"), field="required_check_set.policy"
        ),
        "ruleset_id": _positive_int(
            item.get("ruleset_id"), field="required_check_set.ruleset_id"
        ),
    }
    return {
        **fingerprint_input,
        "fingerprint": _sha256(
            item.get("fingerprint"), field="required_check_set.fingerprint"
        ),
    }


def required_check_set_fingerprint(value: object) -> str:
    """Return the canonical fingerprint without trusting the supplied digest."""
    item = _mapping(value, field="required_check_set")
    raw_members = _list(
        item.get("members"),
        field="required_check_set.members",
        maximum=MAX_REQUIRED_RESULTS,
    )
    members = sorted(
        (
            _normalise_member(member, field=f"required_check_set.members[{index}]")
            for index, member in enumerate(raw_members)
        ),
        key=_member_key,
    )
    return _sha256_payload(
        {
            "members": members,
            "policy": _normalise_policy(
                item.get("policy"), field="required_check_set.policy"
            ),
            "ruleset_id": _positive_int(
                item.get("ruleset_id"), field="required_check_set.ruleset_id"
            ),
        }
    )


def _normalise_execution(value: object) -> dict[str, object]:
    item = _mapping(value, field="execution")
    return {
        "candidate_permissions": _text(
            item.get("candidate_permissions"),
            field="execution.candidate_permissions",
        ),
        "conclusion": _text(item.get("conclusion"), field="execution.conclusion"),
        "event": _text(item.get("event"), field="execution.event"),
        "pinned_actions": _bool(
            item.get("pinned_actions"), field="execution.pinned_actions"
        ),
        "producer_control": _text(
            item.get("producer_control"), field="execution.producer_control"
        ),
        "producer_repository": _text(
            item.get("producer_repository"),
            field="execution.producer_repository",
        ),
        "pull_request_target": _bool(
            item.get("pull_request_target"),
            field="execution.pull_request_target",
        ),
        "run_attempt": _positive_int(
            item.get("run_attempt"), field="execution.run_attempt"
        ),
        "run_id": _positive_int(item.get("run_id"), field="execution.run_id"),
        "run_number": _positive_int(
            item.get("run_number"), field="execution.run_number"
        ),
        "signer_isolated": _bool(
            item.get("signer_isolated"), field="execution.signer_isolated"
        ),
        "signer_uses_candidate_outputs": _bool(
            item.get("signer_uses_candidate_outputs"),
            field="execution.signer_uses_candidate_outputs",
        ),
        "status": _text(item.get("status"), field="execution.status"),
        "workflow_blob_sha": _sha1(
            item.get("workflow_blob_sha"), field="execution.workflow_blob_sha"
        ),
        "workflow_path": _text(
            item.get("workflow_path"), field="execution.workflow_path"
        ),
        "workflow_ref": _text(item.get("workflow_ref"), field="execution.workflow_ref"),
        "workflow_sha": _sha1(item.get("workflow_sha"), field="execution.workflow_sha"),
    }


def _normalise_provider_job(value: object, *, field: str) -> dict[str, object]:
    item = _mapping(value, field=field)
    return {
        "app_id": _positive_int(item.get("app_id"), field=f"{field}.app_id"),
        "check_run_id": _positive_int(
            item.get("check_run_id"), field=f"{field}.check_run_id"
        ),
        "conclusion": _text(item.get("conclusion"), field=f"{field}.conclusion"),
        "job_id": _positive_int(item.get("job_id"), field=f"{field}.job_id"),
        "name": _text(item.get("name"), field=f"{field}.name"),
        "run_attempt": _positive_int(
            item.get("run_attempt"), field=f"{field}.run_attempt"
        ),
        "run_id": _positive_int(item.get("run_id"), field=f"{field}.run_id"),
        "status": _text(item.get("status"), field=f"{field}.status"),
    }


def _normalise_manifest_result(value: object, *, field: str) -> dict[str, object]:
    item = _normalise_provider_job(value, field=field)
    raw = _mapping(value, field=field)
    item["candidate_sha"] = _sha1(
        raw.get("candidate_sha"), field=f"{field}.candidate_sha"
    )
    return item


def _result_sort_key(value: Mapping[str, object]) -> tuple[object, ...]:
    return (*_member_key(value), value["job_id"], value["check_run_id"])


def _normalise_manifest(value: object) -> dict[str, object]:
    item = _mapping(value, field="attestation.manifest")
    candidate = _mapping(item.get("candidate"), field="attestation.manifest.candidate")
    base = _mapping(item.get("live_base"), field="attestation.manifest.live_base")
    raw_parents = _list(
        candidate.get("parents"),
        field="attestation.manifest.candidate.parents",
        maximum=2,
    )
    raw_members = _list(
        item.get("required_checks"),
        field="attestation.manifest.required_checks",
        maximum=MAX_REQUIRED_RESULTS,
    )
    raw_results = _list(
        item.get("required_results"),
        field="attestation.manifest.required_results",
        maximum=MAX_REQUIRED_RESULTS,
    )
    execution = _mapping(item.get("execution"), field="attestation.manifest.execution")
    members = sorted(
        (
            _normalise_member(
                member, field=f"attestation.manifest.required_checks[{index}]"
            )
            for index, member in enumerate(raw_members)
        ),
        key=_member_key,
    )
    results = sorted(
        (
            _normalise_manifest_result(
                result, field=f"attestation.manifest.required_results[{index}]"
            )
            for index, result in enumerate(raw_results)
        ),
        key=_result_sort_key,
    )
    return {
        "candidate": {
            "parents": [
                _sha1(parent, field=f"attestation.manifest.candidate.parents[{index}]")
                for index, parent in enumerate(raw_parents)
            ],
            "sha": _sha1(
                candidate.get("sha"), field="attestation.manifest.candidate.sha"
            ),
            "tree_sha": _sha1(
                candidate.get("tree_sha"),
                field="attestation.manifest.candidate.tree_sha",
            ),
        },
        "contributor_head_sha": _sha1(
            item.get("contributor_head_sha"),
            field="attestation.manifest.contributor_head_sha",
        ),
        "execution": {
            "run_attempt": _positive_int(
                execution.get("run_attempt"),
                field="attestation.manifest.execution.run_attempt",
            ),
            "run_id": _positive_int(
                execution.get("run_id"), field="attestation.manifest.execution.run_id"
            ),
            "workflow_blob_sha": _sha1(
                execution.get("workflow_blob_sha"),
                field="attestation.manifest.execution.workflow_blob_sha",
            ),
            "workflow_path": _text(
                execution.get("workflow_path"),
                field="attestation.manifest.execution.workflow_path",
            ),
            "workflow_repository": _text(
                execution.get("workflow_repository"),
                field="attestation.manifest.execution.workflow_repository",
            ),
            "workflow_sha": _sha1(
                execution.get("workflow_sha"),
                field="attestation.manifest.execution.workflow_sha",
            ),
        },
        "live_base": {
            "ref": _text(base.get("ref"), field="attestation.manifest.live_base.ref"),
            "sha": _sha1(base.get("sha"), field="attestation.manifest.live_base.sha"),
        },
        "pr_number": _positive_int(
            item.get("pr_number"), field="attestation.manifest.pr_number"
        ),
        "repository": _text(
            item.get("repository"), field="attestation.manifest.repository"
        ),
        "required_check_set_fingerprint": _sha256(
            item.get("required_check_set_fingerprint"),
            field="attestation.manifest.required_check_set_fingerprint",
        ),
        "required_checks": members,
        "required_results": results,
        "schema_version": _positive_int(
            item.get("schema_version"), field="attestation.manifest.schema_version"
        ),
    }


def _normalise_provenance(value: object) -> dict[str, object]:
    item = _mapping(value, field="attestation.provenance")
    return {
        "cryptographically_verified": _bool(
            item.get("cryptographically_verified"),
            field="attestation.provenance.cryptographically_verified",
        ),
        "issuer": _text(item.get("issuer"), field="attestation.provenance.issuer"),
        "predicate_type": _text(
            item.get("predicate_type"),
            field="attestation.provenance.predicate_type",
        ),
        "run_attempt": _positive_int(
            item.get("run_attempt"), field="attestation.provenance.run_attempt"
        ),
        "run_id": _positive_int(
            item.get("run_id"), field="attestation.provenance.run_id"
        ),
        "runner_environment": _text(
            item.get("runner_environment"),
            field="attestation.provenance.runner_environment",
        ),
        "signer_workflow_path": _text(
            item.get("signer_workflow_path"),
            field="attestation.provenance.signer_workflow_path",
        ),
        "signer_workflow_repository": _text(
            item.get("signer_workflow_repository"),
            field="attestation.provenance.signer_workflow_repository",
        ),
        "signer_workflow_sha": _sha1(
            item.get("signer_workflow_sha"),
            field="attestation.provenance.signer_workflow_sha",
        ),
        "subject_digest": _sha256(
            item.get("subject_digest"),
            field="attestation.provenance.subject_digest",
        ),
        "subject_name": _text(
            item.get("subject_name"), field="attestation.provenance.subject_name"
        ),
        "verification_source": _text(
            item.get("verification_source"),
            field="attestation.provenance.verification_source",
        ),
    }


def _normalise_observation(value: object) -> dict[str, object]:
    item = _mapping(value, field="observation")
    base = _mapping(item.get("live_base"), field="live_base")
    candidate = _mapping(item.get("candidate"), field="candidate")
    raw_parents = _list(candidate.get("parents"), field="candidate.parents", maximum=2)
    raw_jobs = _list(
        item.get("provider_jobs"),
        field="provider_jobs",
        maximum=MAX_REQUIRED_RESULTS,
    )
    attestation = _mapping(item.get("attestation"), field="attestation")
    try:
        mergeability = Mergeability(
            _text(item.get("mergeability"), field="mergeability")
        )
    except ValueError as error:
        raise ValueError("mergeability is unsupported") from error
    return {
        "attestation": {
            "manifest": _normalise_manifest(attestation.get("manifest")),
            "provenance": _normalise_provenance(attestation.get("provenance")),
        },
        "candidate": {
            "parents": [
                _sha1(parent, field=f"candidate.parents[{index}]")
                for index, parent in enumerate(raw_parents)
            ],
            "sha": _optional_sha1(candidate.get("sha"), field="candidate.sha"),
            "tree_sha": _optional_sha1(
                candidate.get("tree_sha"), field="candidate.tree_sha"
            ),
        },
        "contributor_head_sha": _sha1(
            item.get("contributor_head_sha"), field="contributor_head_sha"
        ),
        "execution": _normalise_execution(item.get("execution")),
        "live_base": {
            "ref": _text(base.get("ref"), field="live_base.ref"),
            "sha": _sha1(base.get("sha"), field="live_base.sha"),
        },
        "mergeability": mergeability.value,
        "pr_number": _positive_int(item.get("pr_number"), field="pr_number"),
        "provider_jobs": sorted(
            (
                _normalise_provider_job(job, field=f"provider_jobs[{index}]")
                for index, job in enumerate(raw_jobs)
            ),
            key=_result_sort_key,
        ),
        "repository": _text(item.get("repository"), field="repository"),
        "required_check_set": _normalise_required_set(item.get("required_check_set")),
    }


def _normalise_trust_policy(value: object) -> dict[str, object]:
    item = _mapping(value, field="trust_policy")
    return {
        "candidate_permissions": _text(
            item.get("candidate_permissions"),
            field="trust_policy.candidate_permissions",
        ),
        "event": _text(item.get("event"), field="trust_policy.event"),
        "issuer": _text(item.get("issuer"), field="trust_policy.issuer"),
        "predicate_type": _text(
            item.get("predicate_type"), field="trust_policy.predicate_type"
        ),
        "producer_control": _text(
            item.get("producer_control"), field="trust_policy.producer_control"
        ),
        "producer_repository": _text(
            item.get("producer_repository"),
            field="trust_policy.producer_repository",
        ),
        "required_policy": _normalise_policy(
            item.get("required_policy"), field="trust_policy.required_policy"
        ),
        "runner_environment": _text(
            item.get("runner_environment"),
            field="trust_policy.runner_environment",
        ),
        "subject_name": _text(
            item.get("subject_name"), field="trust_policy.subject_name"
        ),
        "verification_source": _text(
            item.get("verification_source"),
            field="trust_policy.verification_source",
        ),
        "workflow_blob_sha": _sha1(
            item.get("workflow_blob_sha"),
            field="trust_policy.workflow_blob_sha",
        ),
        "workflow_path": _text(
            item.get("workflow_path"), field="trust_policy.workflow_path"
        ),
        "workflow_ref": _text(
            item.get("workflow_ref"), field="trust_policy.workflow_ref"
        ),
        "workflow_sha": _sha1(
            item.get("workflow_sha"), field="trust_policy.workflow_sha"
        ),
    }


def _expected_manifest(observation: Mapping[str, object]) -> dict[str, object]:
    required = cast(Mapping[str, object], observation["required_check_set"])
    execution = cast(Mapping[str, object], observation["execution"])
    candidate = cast(Mapping[str, object], observation["candidate"])
    base = cast(Mapping[str, object], observation["live_base"])
    members = cast(list[Mapping[str, object]], required["members"])
    member_keys = {_member_key(member) for member in members}
    jobs = cast(list[Mapping[str, object]], observation["provider_jobs"])
    return {
        "candidate": candidate,
        "contributor_head_sha": observation["contributor_head_sha"],
        "execution": {
            "run_attempt": execution["run_attempt"],
            "run_id": execution["run_id"],
            "workflow_blob_sha": execution["workflow_blob_sha"],
            "workflow_path": execution["workflow_path"],
            "workflow_repository": execution["producer_repository"],
            "workflow_sha": execution["workflow_sha"],
        },
        "live_base": base,
        "pr_number": observation["pr_number"],
        "repository": observation["repository"],
        "required_check_set_fingerprint": required["fingerprint"],
        "required_checks": members,
        "required_results": [
            {**job, "candidate_sha": candidate["sha"]}
            for job in jobs
            if _member_key(job) in member_keys
        ],
        "schema_version": SCHEMA_VERSION,
    }


def _workflow_identity(observation: Mapping[str, object]) -> tuple[object, ...]:
    execution = cast(Mapping[str, object], observation["execution"])
    return (
        execution["producer_repository"],
        execution["workflow_path"],
        execution["workflow_ref"],
        execution["workflow_sha"],
        execution["workflow_blob_sha"],
    )


def _candidate_identity(observation: Mapping[str, object]) -> tuple[object, ...]:
    candidate = cast(Mapping[str, object], observation["candidate"])
    parents = cast(list[object], candidate["parents"])
    return candidate["sha"], candidate["tree_sha"], tuple(parents)


def _append(reasons: list[ReasonCode], reason: ReasonCode) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _validate_producer(
    observation: Mapping[str, object],
    trust: Mapping[str, object],
    reasons: list[ReasonCode],
) -> None:
    execution = cast(Mapping[str, object], observation["execution"])
    required = cast(Mapping[str, object], observation["required_check_set"])
    trusted_execution = (
        execution["producer_repository"] == trust["producer_repository"]
        and execution["workflow_path"] == trust["workflow_path"]
        and execution["workflow_ref"] == trust["workflow_ref"]
        and execution["workflow_sha"] == trust["workflow_sha"]
        and execution["workflow_blob_sha"] == trust["workflow_blob_sha"]
        and execution["event"] == trust["event"]
        and execution["producer_control"] == trust["producer_control"]
        and execution["candidate_permissions"] == trust["candidate_permissions"]
        and execution["pinned_actions"] is True
        and execution["pull_request_target"] is False
        and execution["signer_isolated"] is True
        and execution["signer_uses_candidate_outputs"] is False
        and required["policy"] == trust["required_policy"]
    )
    if not trusted_execution:
        _append(reasons, ReasonCode.UNTRUSTED_PRODUCER)


def _validate_attestation(
    observation: Mapping[str, object],
    trust: Mapping[str, object],
    reasons: list[ReasonCode],
) -> None:
    attestation = cast(Mapping[str, object], observation["attestation"])
    manifest = cast(Mapping[str, object], attestation["manifest"])
    provenance = cast(Mapping[str, object], attestation["provenance"])
    execution = cast(Mapping[str, object], observation["execution"])
    if (
        provenance["cryptographically_verified"] is not True
        or provenance["verification_source"] != trust["verification_source"]
        or provenance["issuer"] != trust["issuer"]
        or provenance["predicate_type"] != trust["predicate_type"]
        or provenance["runner_environment"] != trust["runner_environment"]
    ):
        _append(reasons, ReasonCode.ATTESTATION_UNVERIFIED)

    if (
        provenance["signer_workflow_repository"] != execution["producer_repository"]
        or provenance["signer_workflow_path"] != execution["workflow_path"]
        or provenance["signer_workflow_sha"] != execution["workflow_sha"]
    ):
        _append(reasons, ReasonCode.UNTRUSTED_PRODUCER)
    if (
        provenance["run_id"] != execution["run_id"]
        or provenance["run_attempt"] != execution["run_attempt"]
    ):
        _append(reasons, ReasonCode.REQUIRED_RESULT_LIFECYCLE_MISMATCH)
    if provenance["subject_name"] != trust["subject_name"] or provenance[
        "subject_digest"
    ] != _sha256_payload(manifest):
        _append(reasons, ReasonCode.ATTESTATION_SUBJECT_MISMATCH)
    if manifest != _expected_manifest(observation):
        _append(reasons, ReasonCode.ATTESTATION_IDENTITY_MISMATCH)


def _validate_results(
    observation: Mapping[str, object], reasons: list[ReasonCode]
) -> None:
    candidate = cast(Mapping[str, object], observation["candidate"])
    execution = cast(Mapping[str, object], observation["execution"])
    required = cast(Mapping[str, object], observation["required_check_set"])
    members = cast(list[Mapping[str, object]], required["members"])
    jobs = cast(list[Mapping[str, object]], observation["provider_jobs"])
    attestation = cast(Mapping[str, object], observation["attestation"])
    manifest = cast(Mapping[str, object], attestation["manifest"])
    manifest_results = cast(list[Mapping[str, object]], manifest["required_results"])
    for required_member in members:
        matching = [
            job for job in jobs if _member_key(job) == _member_key(required_member)
        ]
        if not matching:
            _append(reasons, ReasonCode.REQUIRED_RESULT_MISSING)
            continue
        if len(matching) != 1:
            _append(reasons, ReasonCode.REQUIRED_RESULT_AMBIGUOUS)
            continue
        job = matching[0]
        signed = [
            result
            for result in manifest_results
            if _member_key(result) == _member_key(required_member)
        ]
        if not signed:
            _append(reasons, ReasonCode.REQUIRED_RESULT_MISSING)
            continue
        if len(signed) != 1:
            _append(reasons, ReasonCode.REQUIRED_RESULT_AMBIGUOUS)
            continue
        signed_result = signed[0]
        if signed_result["candidate_sha"] != candidate["sha"]:
            _append(reasons, ReasonCode.REQUIRED_RESULT_CANDIDATE_MISMATCH)
        if (
            job["run_id"] != execution["run_id"]
            or job["run_attempt"] != execution["run_attempt"]
            or signed_result["run_id"] != execution["run_id"]
            or signed_result["run_attempt"] != execution["run_attempt"]
        ):
            _append(reasons, ReasonCode.REQUIRED_RESULT_LIFECYCLE_MISMATCH)
        if job["status"] != "completed" or job["conclusion"] != "success":
            _append(reasons, ReasonCode.REQUIRED_RESULT_NOT_PASSED)


def assess_observations(
    first_payload: object,
    second_payload: object,
    trust_policy_payload: object,
) -> Assessment:
    """Fail closed unless two reads and one attestation reconstruct one tuple."""
    try:
        trust = _normalise_trust_policy(trust_policy_payload)
        first = _normalise_observation(first_payload)
        second = _normalise_observation(second_payload)
    except UnboundedEvidenceError:
        return Assessment(
            decision=Decision.FAIL,
            reasons=(ReasonCode.UNBOUNDED_EVIDENCE,),
            authoritative_identity=None,
        )
    except (TypeError, ValueError):
        return Assessment(
            decision=Decision.FAIL,
            reasons=(ReasonCode.MALFORMED_OBSERVATION,),
            authoritative_identity=None,
        )

    reasons: list[ReasonCode] = []
    if (first["repository"], first["pr_number"]) != (
        second["repository"],
        second["pr_number"],
    ):
        _append(reasons, ReasonCode.PROVIDER_AMBIGUITY)
    if first["contributor_head_sha"] != second["contributor_head_sha"]:
        _append(reasons, ReasonCode.HEAD_MOVED)
    if first["live_base"] != second["live_base"]:
        _append(reasons, ReasonCode.BASE_MOVED)
    if _candidate_identity(first) != _candidate_identity(second):
        _append(reasons, ReasonCode.CANDIDATE_MOVED)
    if first["required_check_set"] != second["required_check_set"]:
        _append(reasons, ReasonCode.REQUIRED_CHECK_SET_MOVED)
    if _workflow_identity(first) != _workflow_identity(second):
        _append(reasons, ReasonCode.WORKFLOW_IDENTITY_MOVED)

    first_execution = cast(Mapping[str, object], first["execution"])
    second_execution = cast(Mapping[str, object], second["execution"])
    if first_execution["run_id"] != second_execution["run_id"]:
        _append(reasons, ReasonCode.RUN_REPLACED)
    if first_execution["run_attempt"] != second_execution["run_attempt"]:
        _append(reasons, ReasonCode.RUN_ATTEMPT_REPLACED)

    for observation in (first, second):
        candidate = cast(Mapping[str, object], observation["candidate"])
        base = cast(Mapping[str, object], observation["live_base"])
        execution = cast(Mapping[str, object], observation["execution"])
        required = cast(Mapping[str, object], observation["required_check_set"])
        if observation["mergeability"] == Mergeability.CONFLICTED.value:
            _append(reasons, ReasonCode.CANDIDATE_CONFLICTED)
        elif observation["mergeability"] == Mergeability.INDETERMINATE.value:
            _append(reasons, ReasonCode.MERGEABILITY_INDETERMINATE)
        if candidate["sha"] is None or candidate["tree_sha"] is None:
            _append(reasons, ReasonCode.CANDIDATE_MISSING)
        elif candidate["parents"] != [
            base["sha"],
            observation["contributor_head_sha"],
        ]:
            _append(reasons, ReasonCode.CANDIDATE_PARENT_MISMATCH)
        if required["fingerprint"] != required_check_set_fingerprint(required):
            _append(reasons, ReasonCode.REQUIRED_CHECK_SET_FINGERPRINT_INVALID)
        if execution["status"] != "completed" or execution["conclusion"] != "success":
            _append(reasons, ReasonCode.REQUIRED_RESULT_NOT_PASSED)
        _validate_producer(observation, trust, reasons)
        _validate_attestation(observation, trust, reasons)
        _validate_results(observation, reasons)

    identity: Mapping[str, object] | None = None
    if not reasons:
        identity = cast(
            Mapping[str, object],
            cast(Mapping[str, object], second["attestation"])["manifest"],
        )
    return Assessment(
        decision=Decision.FAIL if reasons else Decision.PASS,
        reasons=tuple(reasons),
        authoritative_identity=identity,
    )


def exact_commit_authority(candidate_sha: str, compared_sha: str) -> bool:
    """Tree equality never transfers authority between commit identities."""
    return candidate_sha == compared_sha


def _deep_merge(target: dict[str, object], patch: Mapping[str, object]) -> None:
    for key, value in patch.items():
        current = target.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            _deep_merge(current, cast(Mapping[str, object], value))
        else:
            target[key] = copy.deepcopy(value)


def _fixture_manifest(observation: Mapping[str, object]) -> dict[str, object]:
    """Build a fixture manifest; production must never self-assert provenance."""
    selected = copy.deepcopy(dict(observation))
    selected.pop("attestation", None)
    normalised = _normalise_observation(
        {
            **selected,
            "attestation": {
                "manifest": {
                    "candidate": selected["candidate"],
                    "contributor_head_sha": selected["contributor_head_sha"],
                    "execution": {
                        "run_attempt": cast(dict[str, object], selected["execution"])[
                            "run_attempt"
                        ],
                        "run_id": cast(dict[str, object], selected["execution"])[
                            "run_id"
                        ],
                        "workflow_blob_sha": cast(
                            dict[str, object], selected["execution"]
                        )["workflow_blob_sha"],
                        "workflow_path": cast(dict[str, object], selected["execution"])[
                            "workflow_path"
                        ],
                        "workflow_repository": cast(
                            dict[str, object], selected["execution"]
                        )["producer_repository"],
                        "workflow_sha": cast(dict[str, object], selected["execution"])[
                            "workflow_sha"
                        ],
                    },
                    "live_base": selected["live_base"],
                    "pr_number": selected["pr_number"],
                    "repository": selected["repository"],
                    "required_check_set_fingerprint": cast(
                        dict[str, object], selected["required_check_set"]
                    )["fingerprint"],
                    "required_checks": cast(
                        dict[str, object], selected["required_check_set"]
                    )["members"],
                    "required_results": selected["provider_jobs"],
                    "schema_version": SCHEMA_VERSION,
                },
                "provenance": {
                    "cryptographically_verified": True,
                    "issuer": "https://token.actions.githubusercontent.com",
                    "predicate_type": "https://atlas.example/candidate-ci/v1",
                    "run_attempt": cast(dict[str, object], selected["execution"])[
                        "run_attempt"
                    ],
                    "run_id": cast(dict[str, object], selected["execution"])["run_id"],
                    "runner_environment": "github-hosted",
                    "signer_workflow_path": cast(
                        dict[str, object], selected["execution"]
                    )["workflow_path"],
                    "signer_workflow_repository": cast(
                        dict[str, object], selected["execution"]
                    )["producer_repository"],
                    "signer_workflow_sha": cast(
                        dict[str, object], selected["execution"]
                    )["workflow_sha"],
                    "subject_digest": "0" * _SHA256_LENGTH,
                    "subject_name": "atlas-candidate-attestation.json",
                    "verification_source": "atlas_sigstore",
                },
            },
        }
    )
    return _expected_manifest(normalised)


def refresh_fixture_attestation(observation: dict[str, object]) -> None:
    """Simulate the bounded output of the external verifier for fixtures only."""
    manifest = _fixture_manifest(observation)
    execution = cast(dict[str, object], observation["execution"])
    observation["attestation"] = {
        "manifest": manifest,
        "provenance": {
            "cryptographically_verified": True,
            "issuer": "https://token.actions.githubusercontent.com",
            "predicate_type": "https://atlas.example/candidate-ci/v1",
            "run_attempt": execution["run_attempt"],
            "run_id": execution["run_id"],
            "runner_environment": "github-hosted",
            "signer_workflow_path": execution["workflow_path"],
            "signer_workflow_repository": execution["producer_repository"],
            "signer_workflow_sha": execution["workflow_sha"],
            "subject_digest": _sha256_payload(manifest),
            "subject_name": "atlas-candidate-attestation.json",
            "verification_source": "atlas_sigstore",
        },
    }


def _load_fixture(path: Path) -> Mapping[str, Any]:
    if path.stat().st_size > MAX_FIXTURE_BYTES:
        raise UnboundedEvidenceError("fixture exceeds the bounded input limit")
    return _mapping(json.loads(path.read_text(encoding="utf-8")), field="fixture")


def _expanded_jobs(observation: dict[str, object], count: int) -> None:
    jobs = cast(list[dict[str, object]], observation["provider_jobs"])
    template = jobs[0]
    observation["provider_jobs"] = [
        {
            **template,
            "check_run_id": 900_000 + index,
            "job_id": 800_000 + index,
        }
        for index in range(count)
    ]


def run_fixture(
    path: Path,
    repository_root: Path,
    *,
    git_runner: GitRunner = _run_git,
) -> tuple[dict[str, object], bool]:
    fixture = _load_fixture(path)
    if fixture.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("fixture schema_version is unsupported")
    trust = _mapping(fixture.get("trust_policy"), field="trust_policy")
    base = copy.deepcopy(
        dict(_mapping(fixture.get("base_observation"), field="base_observation"))
    )
    required = cast(dict[str, object], base["required_check_set"])
    required["fingerprint"] = required_check_set_fingerprint(required)
    refresh_fixture_attestation(base)
    raw_cases = _list(fixture.get("cases"), field="cases", maximum=MAX_CASES)
    if not raw_cases:
        raise ValueError("cases must be a bounded non-empty list")

    summaries: list[dict[str, object]] = []
    matched = True
    governed: Assessment | None = None
    for index, raw_case in enumerate(raw_cases):
        case = _mapping(raw_case, field=f"cases[{index}]")
        name = _text(case.get("name"), field=f"cases[{index}].name")
        first = copy.deepcopy(base)
        second = copy.deepcopy(base)
        _deep_merge(
            first,
            _mapping(case.get("first_patch", {}), field=f"cases[{index}].first_patch"),
        )
        _deep_merge(
            second,
            _mapping(
                case.get("second_patch", {}), field=f"cases[{index}].second_patch"
            ),
        )
        if case.get("expand_first_jobs") is not None:
            _expanded_jobs(
                first,
                _positive_int(
                    case.get("expand_first_jobs"),
                    field=f"cases[{index}].expand_first_jobs",
                ),
            )
        if case.get("expand_second_jobs") is not None:
            _expanded_jobs(
                second,
                _positive_int(
                    case.get("expand_second_jobs"),
                    field=f"cases[{index}].expand_second_jobs",
                ),
            )
        if case.get("refresh_first") is True:
            required = cast(dict[str, object], first["required_check_set"])
            required["fingerprint"] = required_check_set_fingerprint(required)
            refresh_fixture_attestation(first)
        if case.get("refresh_second") is True:
            required = cast(dict[str, object], second["required_check_set"])
            required["fingerprint"] = required_check_set_fingerprint(required)
            refresh_fixture_attestation(second)
        assessment = assess_observations(first, second, trust)
        expected_decision = Decision(
            _text(
                case.get("expected_decision"),
                field=f"cases[{index}].expected_decision",
            )
        )
        raw_expected_reasons = _list(
            case.get("expected_reasons"),
            field=f"cases[{index}].expected_reasons",
            maximum=len(ReasonCode),
        )
        expected_reasons = tuple(
            ReasonCode(
                _text(reason, field=f"cases[{index}].expected_reasons[{reason_index}]")
            )
            for reason_index, reason in enumerate(raw_expected_reasons)
        )
        case_matched = assessment.decision is expected_decision and all(
            reason in assessment.reasons for reason in expected_reasons
        )
        matched = matched and case_matched
        summaries.append(
            {
                "decision": assessment.decision.value,
                "expected_matched": case_matched,
                "name": name,
                "reasons": [reason.value for reason in assessment.reasons],
            }
        )
        if name == fixture.get("governed_case"):
            governed = assessment

    if governed is None:
        raise ValueError("governed_case did not match a fixture case")
    repository_evidence = exercise_disposable_repository(
        repository_root, git_runner=git_runner
    )
    clean = cast(Mapping[str, object], repository_evidence["clean_candidate"])
    moved = cast(Mapping[str, object], repository_evidence["base_move"])
    conflict = cast(Mapping[str, object], repository_evidence["conflict"])
    final_merge = cast(Mapping[str, object], repository_evidence["merge_commit"])
    squash = cast(Mapping[str, object], repository_evidence["squash_merge"])
    repository_passed = bool(
        clean["stable"]
        and moved["candidate_changed"]
        and not conflict["candidate_available"]
        and final_merge["same_tree_as_candidate"]
        and not exact_commit_authority(
            cast(str, clean["sha"]), cast(str, final_merge["sha"])
        )
        and squash["same_tree_as_candidate"]
        and not exact_commit_authority(
            cast(str, clean["sha"]), cast(str, squash["sha"])
        )
    )
    expected_governed = Decision(
        _text(
            fixture.get("expected_governed_decision"),
            field="expected_governed_decision",
        )
    )
    matched = (
        matched
        and repository_passed
        and governed.decision is expected_governed
        and governed.authoritative_identity is not None
    )
    report = {
        "case_count": len(summaries),
        "cases": summaries,
        "fixture_contract_passed": matched,
        "governed_case": fixture.get("governed_case"),
        "governed_decision": governed.decision.value,
        "governed_identity": governed.authoritative_identity,
        "mutation_inventory": {
            "automatic_acceptance": False,
            "github_merge_or_update": False,
            "git_rebase_or_push": False,
            "linear_transition": False,
            "symphony_control": False,
        },
        "repository_evidence": repository_evidence,
        "schema_version": SCHEMA_VERSION,
    }
    if len(_canonical_bytes(report)) > MAX_RETAINED_BYTES:
        raise UnboundedEvidenceError("report exceeds the bounded retention limit")
    return report, matched


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path, help="bounded selected-field fixture")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="atlas-260-") as temp_dir:
        report, passed = run_fixture(args.fixture, Path(temp_dir) / "repository")
    print(json.dumps(report, separators=(",", ":"), sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

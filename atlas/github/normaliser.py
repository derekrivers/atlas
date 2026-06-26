"""Transport-agnostic CI-run normaliser (ATLAS-62), per
evidence-pipeline.md "Status normalisation" and ADR-0008.

Turns a raw GitHub workflow-run or check-run payload into a frozen
``NormalisedCheck`` -- the *webhook-swap contract*: a future HMAC webhook
receiver produces this same shape, so polling can be replaced with no schema
change (ADR-0008). The shape carries enough for a later mapper (ATLAS-63/64)
to build an ``Evidence``: the job/check name (the mappers' type-lookup key),
the already-normalised ``EvidenceStatus``, ``external_run_id``,
``commit_sha`` (the PR head SHA), the deterministic ``payload_hash``, a
``source_uri``, and the verbatim ``raw_payload``.

It deliberately does NOT carry an ``EvidenceType`` (that mapping is the
job-name contract owned by ATLAS-63/64) and never persists anything: it only
*computes* the dedup key ``(external_run_id, payload_hash)``; the "skip if
already stored" check belongs to the persisting layer (ADR-0008).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from atlas.core.enums import EvidenceStatus

# GitHub conclusion -> EvidenceStatus, verbatim from evidence-pipeline.md
# "Status normalisation". A conclusion absent from this table maps to
# WARNING (never silently dropped, never a crash) -- see ``normalise_status``.
_CONCLUSION_STATUS: dict[str, EvidenceStatus] = {
    "success": EvidenceStatus.PASSED,
    "failure": EvidenceStatus.FAILED,
    "timed_out": EvidenceStatus.FAILED,
    "cancelled": EvidenceStatus.WARNING,
    "stale": EvidenceStatus.WARNING,
    "skipped": EvidenceStatus.NOT_APPLICABLE,
    "neutral": EvidenceStatus.NOT_APPLICABLE,
}


@dataclass(frozen=True)
class NormalisedCheck:
    """One normalised CI run -- the webhook-swap contract (ADR-0008).

    Identical whether produced by the poller (ATLAS-62) or a future webhook
    receiver. Carries no ``EvidenceType``: typing from the job/check ``name``
    is the job-name contract owned by the mappers (ATLAS-63/64).
    """

    name: str
    status: EvidenceStatus
    external_run_id: str
    commit_sha: str
    payload_hash: str
    source_uri: str | None
    raw_payload: dict[str, Any]

    @property
    def dedup_key(self) -> tuple[str, str]:
        """The append-only dedup key (evidence-pipeline.md "Poller").

        Re-polling an unchanged run yields the same key; a re-run of the same
        workflow yields a new ``payload_hash`` and so a new key. 62 only
        *computes* it -- the persisting layer owns the skip-if-present check.
        """
        return (self.external_run_id, self.payload_hash)


def payload_hash(payload: Mapping[str, Any]) -> str:
    """SHA-256 over the canonicalised payload.

    Canonicalisation is ``json.dumps`` with sorted keys, so the hash is
    deterministic regardless of key order in the raw response; any change to
    the payload changes the hash (the dedup property ADR-0008 relies on).
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalise_status(status: str | None, conclusion: str | None) -> EvidenceStatus:
    """Map a GitHub (status, conclusion) pair to an ``EvidenceStatus``.

    A run that has not completed carries no ``conclusion`` (its ``status`` is
    e.g. ``queued``/``in_progress``) and is PENDING. A completed run maps by
    its conclusion per the evidence-pipeline.md table; an unrecognised
    conclusion maps to WARNING -- surfaced, never silently dropped.
    """
    if not conclusion:
        # No conclusion yet => the run is still in progress (or queued).
        return EvidenceStatus.PENDING
    return _CONCLUSION_STATUS.get(conclusion, EvidenceStatus.WARNING)


def normalise_workflow_run(run: Mapping[str, Any], *, head_sha: str) -> NormalisedCheck:
    """Normalise one ``actions/runs`` workflow-run payload.

    ``commit_sha`` is pinned to the polled ``head_sha`` (the exact code state
    attested; ADR-0008), not re-read from the payload, so the record is
    pinned to what Atlas asked about even if GitHub echoes a differing field.
    """
    return NormalisedCheck(
        name=str(run["name"]),
        status=normalise_status(run.get("status"), run.get("conclusion")),
        external_run_id=str(run["id"]),
        commit_sha=head_sha,
        payload_hash=payload_hash(run),
        source_uri=run.get("html_url"),
        raw_payload=dict(run),
    )


def normalise_check_run(check: Mapping[str, Any], *, head_sha: str) -> NormalisedCheck:
    """Normalise one ``commits/{sha}/check-runs`` check-run payload.

    Check runs expose a browser link as ``html_url`` (``details_url`` points
    at the external provider); the same (status, conclusion) normalisation and
    head-SHA pinning as workflow runs applies.
    """
    return NormalisedCheck(
        name=str(check["name"]),
        status=normalise_status(check.get("status"), check.get("conclusion")),
        external_run_id=str(check["id"]),
        commit_sha=head_sha,
        payload_hash=payload_hash(check),
        source_uri=check.get("html_url"),
        raw_payload=dict(check),
    )


def normalise_workflow_runs(
    runs: Sequence[Mapping[str, Any]], *, head_sha: str
) -> list[NormalisedCheck]:
    """Normalise a list of workflow-run payloads (a 304/ETag hit feeds the
    empty list, so it yields no normalised events)."""
    return [normalise_workflow_run(run, head_sha=head_sha) for run in runs]


def normalise_check_runs(
    checks: Sequence[Mapping[str, Any]], *, head_sha: str
) -> list[NormalisedCheck]:
    """Normalise a list of check-run payloads (empty list -> no events)."""
    return [normalise_check_run(check, head_sha=head_sha) for check in checks]

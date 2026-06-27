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

PR reviews (ATLAS-65) ship their own ``NormalisedReview`` shape and state
table beside the check normaliser: a review is not a check (it carries a
reviewer, no job name, and always becomes ``PR_REVIEW`` evidence), so it gets
a distinct frozen shape rather than overloading ``NormalisedCheck``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from atlas.core.enums import EvidenceStatus

logger = logging.getLogger("atlas.github.normaliser")

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


# --- PR reviews (ATLAS-65) --------------------------------------------------

# GitHub review state -> EvidenceStatus, per evidence-pipeline.md "Status
# normalisation". States arrive UPPERCASE from GitHub. A state absent from this
# table maps to WARNING (e.g. PENDING, or any future state) -- surfaced, never
# silently dropped, mirroring ``_CONCLUSION_STATUS``.
_REVIEW_STATE_STATUS: dict[str, EvidenceStatus] = {
    "APPROVED": EvidenceStatus.PASSED,
    "CHANGES_REQUESTED": EvidenceStatus.FAILED,
    "COMMENTED": EvidenceStatus.WARNING,
    "DISMISSED": EvidenceStatus.NOT_APPLICABLE,
}


@dataclass(frozen=True)
class NormalisedReview:
    """One normalised PR review -- the webhook-swap contract (ADR-0008).

    The review analogue of :class:`NormalisedCheck`, identical whether produced
    by the poller (ATLAS-65) or a future ``pull_request_review`` webhook. A
    review is NOT a check: it carries the ``reviewer`` (login), no job ``name``,
    and no ``EvidenceType`` -- a review always becomes ``PR_REVIEW`` evidence,
    so there is no type-lookup key to carry (the mapper hard-codes the type).
    ``commit_sha`` is the review's ``commit_id`` (the exact code state the
    reviewer attested), so the pin triple satisfies ATLAS-61's system-tier guard.
    """

    reviewer: str
    status: EvidenceStatus
    external_run_id: str
    commit_sha: str
    payload_hash: str
    source_uri: str | None
    raw_payload: dict[str, Any]

    @property
    def dedup_key(self) -> tuple[str, str]:
        """The append-only dedup key (evidence-pipeline.md "Poller").

        Same contract as :attr:`NormalisedCheck.dedup_key`: re-polling an
        unchanged review yields the same key; an edited review (changed payload)
        yields a new ``payload_hash`` and so a new key.
        """
        return (self.external_run_id, self.payload_hash)


def normalise_review_state(state: str) -> EvidenceStatus:
    """Map a GitHub review ``state`` to an ``EvidenceStatus``.

    ``APPROVED`` -> PASSED, ``CHANGES_REQUESTED`` -> FAILED, ``COMMENTED`` ->
    WARNING, ``DISMISSED`` -> NOT_APPLICABLE. Any other state (e.g. ``PENDING``)
    maps to WARNING -- surfaced, never silently dropped, never a crash.
    """
    return _REVIEW_STATE_STATUS.get(state, EvidenceStatus.WARNING)


def normalise_review(review: Mapping[str, Any]) -> NormalisedReview | None:
    """Normalise one ``pulls/{n}/reviews`` payload, or ``None`` if unpinnable.

    ``commit_sha`` is the review's own ``commit_id`` -- the exact code state the
    reviewer attested. A review whose ``commit_id`` is missing or null cannot be
    commit-pinned, so it cannot be valid system-tier evidence (ATLAS-61): it is
    SKIPPED (returns ``None``) with a warning rather than coerced into a record
    with a ``"None"`` pin. Every ``NormalisedReview`` that exists is therefore
    genuinely commit-pinned.
    """
    commit_id = review.get("commit_id")
    if not commit_id:
        logger.warning(
            "PR review %r has no commit_id; skipping (cannot be commit-pinned, "
            "ATLAS-65)",
            review.get("id"),
        )
        return None
    return NormalisedReview(
        reviewer=review["user"]["login"],
        status=normalise_review_state(review["state"]),
        external_run_id=str(review["id"]),
        commit_sha=str(commit_id),
        payload_hash=payload_hash(review),
        source_uri=review.get("html_url"),
        raw_payload=dict(review),
    )


def normalise_reviews(
    reviews: Sequence[Mapping[str, Any]],
) -> list[NormalisedReview]:
    """Normalise a list of review payloads, dropping the unpinnable ones.

    Reviews with no ``commit_id`` are filtered out (each warned by
    :func:`normalise_review`), so every returned ``NormalisedReview`` is
    genuinely commit-pinned. A 304/ETag hit feeds the empty list, so it yields
    no normalised events.
    """
    normalised = [normalise_review(review) for review in reviews]
    return [review for review in normalised if review is not None]


# --- documentation evidence (ATLAS-66) --------------------------------------

# The PR-file predicate (D3): a changed file counts as a documentation change
# when its path is under the ``docs/`` tree. Nothing fancier this ticket -- no
# extension or sub-tree filtering. Kept as a named constant so the seeded-defect
# (``"docs/"`` -> ``"doc/"``) and the absence-based guarantee both have one site.
_DOCS_PREFIX = "docs/"


@dataclass(frozen=True)
class NormalisedDocs:
    """One normalised per-PR documentation change -- the webhook-swap contract.

    The docs analogue of :class:`NormalisedReview`, identical whether produced by
    the poller (ATLAS-66) or a future ``pull_request`` webhook. A docs change is
    NOT a check or a review: it is one record per PR (not per item), it carries
    the touched ``docs_paths`` rather than a job ``name`` or ``reviewer``, and it
    is ALWAYS ``EvidenceStatus.PASSED`` -- the presence of a ``docs/`` change is
    the signal, so there is no status to derive and no ``EvidenceType`` to carry
    (the mapper hard-codes ``DOCUMENTATION_UPDATE``). ``commit_sha`` is the polled
    head SHA (the files endpoint omits it), so the synthesised pin triple still
    satisfies ATLAS-61's system-tier guard.
    """

    status: EvidenceStatus
    docs_paths: tuple[str, ...]
    external_run_id: str
    commit_sha: str
    payload_hash: str
    source_uri: str | None
    raw_payload: dict[str, Any]

    @property
    def dedup_key(self) -> tuple[str, str]:
        """The append-only dedup key (evidence-pipeline.md "Poller").

        Same contract as :attr:`NormalisedCheck.dedup_key`: re-polling the same
        head SHA's unchanged file list yields the same ``(external_run_id,
        payload_hash)``; a new commit (new ``docs:<sha>`` id) or an edited file
        set (new ``payload_hash``) yields a new key.
        """
        return (self.external_run_id, self.payload_hash)


def normalise_pr_files(
    files: Sequence[Mapping[str, Any]], *, head_sha: str
) -> NormalisedDocs | None:
    """Normalise a PR file list into a per-PR ``NormalisedDocs``, or ``None``.

    Filters the file list to those whose ``filename`` is under ``docs/``. When
    NONE are, returns ``None`` -- no ``docs/`` change means no record, because
    the *absence* is the signal (evidence-pipeline.md "Documentation evidence");
    a record is never manufactured (and never a FAILED one) where there is no
    documentation change. When some are, builds a single PASSED ``NormalisedDocs``
    over the ``docs/`` subset:

    * ``docs_paths`` -- the touched ``docs/`` filenames, sorted (deterministic);
    * ``commit_sha`` -- the polled ``head_sha`` (the files endpoint omits it);
    * ``external_run_id`` -- the synthesised ``f"docs:{head_sha}"`` (there is no
      GitHub run id for a file list; this pins the record to the head SHA);
    * ``payload_hash`` -- the deterministic hash of the ``docs/`` subset only, so
      an unrelated non-docs file changing does not churn the docs record;
    * ``source_uri`` -- ``None`` (a file list has no single browser URL);
    * ``raw_payload`` -- ``{"files": <the docs/ subset>}``.
    """
    docs_files = [
        dict(file)
        for file in files
        if str(file.get("filename", "")).startswith(_DOCS_PREFIX)
    ]
    if not docs_files:
        return None
    subset = {"files": docs_files}
    return NormalisedDocs(
        status=EvidenceStatus.PASSED,
        docs_paths=tuple(sorted(str(file["filename"]) for file in docs_files)),
        external_run_id=f"docs:{head_sha}",
        commit_sha=head_sha,
        payload_hash=payload_hash(subset),
        source_uri=None,
        raw_payload=subset,
    )

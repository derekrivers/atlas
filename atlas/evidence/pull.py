"""Evidence-pull orchestration driver."""

from __future__ import annotations

from datetime import datetime
from typing import NamedTuple
from uuid import UUID

from atlas.core.models.evidence import Evidence
from atlas.evidence.ingest import ingest_checks, ingest_docs, ingest_reviews
from atlas.github import (
    GitHubAPIError,
    GitHubClient,
    normalise_check_runs,
    normalise_pr_files,
    normalise_reviews,
    normalise_workflow_runs,
)
from atlas.storage import EvidenceRepo


class PullResult(NamedTuple):
    """The per-source records one `evidence pull` persisted (D1). Returned by
    the Protocol-typed driver so the command can print a per-source count and
    tests can assert the persisted rows."""

    checks: list[Evidence]
    reviews: list[Evidence]
    docs: list[Evidence]
    head_sha: str | None = None
    observed: tuple[Evidence, ...] = ()


class EvidencePullMalformedSourceError(ValueError):
    """A source payload could not satisfy the canonical evidence contract."""


def drive_evidence_pull(
    client: GitHubClient,
    owner: str,
    repo: str,
    pr_number: int,
    *,
    evidence_repo: EvidenceRepo,
    product_id: UUID,
    now: datetime,
) -> PullResult:
    """Run the canonical pull and type malformed source/pin failures."""

    try:
        return _drive_evidence_pull_unchecked(
            client,
            owner,
            repo,
            pr_number,
            evidence_repo=evidence_repo,
            product_id=product_id,
            now=now,
        )
    except GitHubAPIError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise EvidencePullMalformedSourceError(
            "GitHub evidence source did not satisfy the canonical contract"
        ) from error


def _drive_evidence_pull_unchecked(
    client: GitHubClient,
    owner: str,
    repo: str,
    pr_number: int,
    *,
    evidence_repo: EvidenceRepo,
    product_id: UUID,
    now: datetime,
) -> PullResult:
    """Fetch -> normalise -> ingest all three evidence sources for one PR (D1/D3).

    Protocol-typed in ``client`` (any ``GitHubClient``) so it runs fully offline
    under the fake -- no network, no concrete client wired in. Resolves the head
    SHA ONCE from the pull-request object (``["head"]["sha"]``), requires the
    returned PR number and base repository to match the exact request, and
    threads the validated full lowercase head into the CI/docs normalisers,
    while reviews and files are fetched by ``pr_number`` (D3). ``now`` is
    captured once by the caller (D6) and passed to every ``ingest_*`` so the
    run's records share a creation time. Persistence is the append-only
    ``EvidenceRepo``; the ATLAS-61 system-tier pinning guard runs inside its
    ``add``.

    A 404 (unknown PR) or any transport failure surfaces as ``GitHubAPIError``
    for the caller to map to a clean precondition -- never a traceback.
    """
    pull_request = client.fetch_pull_request(owner, repo, pr_number)
    response_number = pull_request["number"]
    head_sha = pull_request["head"]["sha"]
    base_repository = pull_request["base"]["repo"]["full_name"]
    expected_repository = f"{owner}/{repo}"
    if (
        isinstance(response_number, bool)
        or response_number != pr_number
        or not isinstance(base_repository, str)
        or base_repository.casefold() != expected_repository.casefold()
        or not isinstance(head_sha, str)
        or len(head_sha) != 40
        or any(character not in "0123456789abcdefABCDEF" for character in head_sha)
    ):
        raise ValueError("GitHub pull-request identity was contradictory or malformed")
    head_sha = head_sha.lower()

    checks = [
        *normalise_workflow_runs(
            client.fetch_workflow_runs(owner, repo, head_sha), head_sha=head_sha
        ),
        *normalise_check_runs(
            client.fetch_check_runs(owner, repo, head_sha), head_sha=head_sha
        ),
    ]
    reviews = normalise_reviews(client.fetch_pr_reviews(owner, repo, pr_number))
    docs = normalise_pr_files(
        client.fetch_pr_files(owner, repo, pr_number), head_sha=head_sha
    )

    persisted_checks = ingest_checks(
        checks, repo=evidence_repo, product_id=product_id, now=now
    )
    persisted_reviews = ingest_reviews(
        reviews, repo=evidence_repo, product_id=product_id, now=now
    )
    persisted_docs = ingest_docs(
        docs, repo=evidence_repo, product_id=product_id, now=now
    )

    # The persisted lists intentionally contain only newly appended rows so the
    # CLI's dedup counts remain stable. The production CI-handoff adapter also
    # needs the complete exact pull attribution, including a prior immutable
    # row found by dedup. Resolve every source identity back through the same
    # canonical repository boundary and carry those records separately.
    observed: list[Evidence] = []
    source_items = [
        *((item.external_run_id, item.payload_hash, True) for item in checks),
        *((item.external_run_id, item.payload_hash, False) for item in reviews),
    ]
    if docs is not None:
        source_items.append((docs.external_run_id, docs.payload_hash, False))
    for external_run_id, source_hash, require_job_metadata in source_items:
        record = evidence_repo.get_by_dedup_key(
            external_run_id,
            source_hash,
            require_job_metadata=require_job_metadata,
        )
        if record is None:
            raise ValueError("persisted evidence source could not be reloaded")
        observed.append(record)

    return PullResult(
        checks=persisted_checks,
        reviews=persisted_reviews,
        docs=persisted_docs,
        head_sha=head_sha,
        observed=tuple(observed),
    )

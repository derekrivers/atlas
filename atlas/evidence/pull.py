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
    SHA ONCE from the pull-request object (``["head"]["sha"]``) and threads it
    into the CI/docs normalisers, while reviews and files are fetched by
    ``pr_number`` (D3). ``now`` is captured once by the caller (D6) and passed to
    every ``ingest_*`` so the run's records share a creation time. Persistence is
    the append-only ``EvidenceRepo``; the ATLAS-61 system-tier pinning guard runs
    inside its ``add``.

    A 404 (unknown PR) or any transport failure surfaces as ``GitHubAPIError``
    for the caller to map to a clean precondition -- never a traceback.
    """
    pull_request = client.fetch_pull_request(owner, repo, pr_number)
    head_sha = str(pull_request["head"]["sha"])

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

    return PullResult(
        checks=ingest_checks(
            checks, repo=evidence_repo, product_id=product_id, now=now
        ),
        reviews=ingest_reviews(
            reviews, repo=evidence_repo, product_id=product_id, now=now
        ),
        docs=ingest_docs(docs, repo=evidence_repo, product_id=product_id, now=now),
    )

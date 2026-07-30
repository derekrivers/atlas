"""GitHub evidence boundary (Phase 6, ATLAS-62).

The Phase-6 CI-evidence boundary: a ``GitHubClient`` protocol and its REST
implementation (the pull-first transport of ADR-0008), plus a
transport-agnostic normaliser that turns raw workflow-run / check-run payloads
into the frozen ``NormalisedCheck`` webhook-swap shape, raw PR-review payloads
into the ``NormalisedReview`` shape (ATLAS-65), and PR file lists into the
per-PR ``NormalisedDocs`` shape (ATLAS-66). A layer above
``atlas.core`` only -- it imports no other Atlas layer (the import-linter spine
forbids ``github -> linear``). Persistence, EvidenceType typing, and the tick
loop are deliberately elsewhere (ATLAS-63/64/65, Phase 8).
"""

from atlas.github.client import (
    API_ROOT,
    API_VERSION,
    MAX_RATE_LIMIT_RETRIES,
    PER_PAGE,
    TOKEN_ENV,
    GitHubAPIError,
    GitHubClient,
    GitHubClientError,
    GitHubCompare,
    GitHubCompareStatus,
    GitHubRESTClient,
    MissingGitHubTokenError,
)
from atlas.github.normaliser import (
    NormalisedCheck,
    NormalisedDocs,
    NormalisedReview,
    normalise_check_run,
    normalise_check_runs,
    normalise_pr_files,
    normalise_review,
    normalise_review_state,
    normalise_reviews,
    normalise_status,
    normalise_workflow_run,
    normalise_workflow_runs,
    payload_hash,
)

__all__ = [
    "API_ROOT",
    "API_VERSION",
    "MAX_RATE_LIMIT_RETRIES",
    "PER_PAGE",
    "TOKEN_ENV",
    "GitHubAPIError",
    "GitHubClient",
    "GitHubClientError",
    "GitHubCompare",
    "GitHubCompareStatus",
    "GitHubRESTClient",
    "MissingGitHubTokenError",
    "NormalisedCheck",
    "NormalisedDocs",
    "NormalisedReview",
    "normalise_check_run",
    "normalise_check_runs",
    "normalise_pr_files",
    "normalise_review",
    "normalise_review_state",
    "normalise_reviews",
    "normalise_status",
    "normalise_workflow_run",
    "normalise_workflow_runs",
    "payload_hash",
]

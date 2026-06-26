"""GitHub evidence boundary (Phase 6, ATLAS-62).

The Phase-6 CI-evidence boundary: a ``GitHubClient`` protocol and its REST
implementation (the pull-first transport of ADR-0008), plus a
transport-agnostic normaliser that turns raw workflow-run / check-run
payloads into the frozen ``NormalisedCheck`` webhook-swap shape. A layer
above ``atlas.core`` only -- it imports no other Atlas layer (the
import-linter spine forbids ``github -> linear``). Persistence, EvidenceType
typing, and the tick loop are deliberately elsewhere (ATLAS-63/64, Phase 8).
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
    GitHubRESTClient,
    MissingGitHubTokenError,
)
from atlas.github.normaliser import (
    NormalisedCheck,
    normalise_check_run,
    normalise_check_runs,
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
    "GitHubRESTClient",
    "MissingGitHubTokenError",
    "NormalisedCheck",
    "normalise_check_run",
    "normalise_check_runs",
    "normalise_status",
    "normalise_workflow_run",
    "normalise_workflow_runs",
    "payload_hash",
]

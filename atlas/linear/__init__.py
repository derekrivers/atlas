"""Linear integration boundary (Phase 4, ATLAS-41).

The Phase-4 provider boundary: a ``LinearClient`` protocol and its GraphQL
implementation, plus the field-ownership allow-list (definitions
Atlas -> Linear, status Linear -> Atlas, nothing else). A layer above
``atlas.core``, which imports nothing Linear-specific (ADR-0006). The sync
loop that drives this boundary on a cadence is ATLAS-42.
"""

from atlas.linear.ci_handoff import CI_HANDOFF_TARGETS, LinearCIHandoffWriter
from atlas.linear.client import (
    API_KEY_ENV,
    API_URL,
    LINEAR_ERROR_BODY_MAX_LEN,
    LINEAR_HTTP_TIMEOUT_SECONDS,
    PROJECT_ID_ENV,
    TEAM_ID_ENV,
    LinearAPIError,
    LinearClient,
    LinearClientError,
    LinearComment,
    LinearGitHubPublication,
    LinearGraphQLClient,
    LinearIssue,
    LinearMergedGitHubPublication,
    LinearProjectIssues,
    LinearRateLimitError,
    MissingLinearTokenError,
    UnownedFieldError,
    WorkflowState,
    reject_unowned_keys,
)
from atlas.linear.ownership import (
    OWNED_DEFINITION_FIELDS,
    OWNED_LINEAR_INPUT_KEYS,
    STATE_MAP_ENV,
    LinearStatusMap,
    LinearStatusMapError,
    definition_payload,
    status_from_issue,
)
from atlas.linear.retrospective_completion import (
    LinearRetrospectiveCompletionWriter,
)

__all__ = [
    "API_KEY_ENV",
    "API_URL",
    "CI_HANDOFF_TARGETS",
    "LINEAR_ERROR_BODY_MAX_LEN",
    "LINEAR_HTTP_TIMEOUT_SECONDS",
    "OWNED_DEFINITION_FIELDS",
    "OWNED_LINEAR_INPUT_KEYS",
    "PROJECT_ID_ENV",
    "STATE_MAP_ENV",
    "TEAM_ID_ENV",
    "LinearAPIError",
    "LinearCIHandoffWriter",
    "LinearClient",
    "LinearClientError",
    "LinearComment",
    "LinearGitHubPublication",
    "LinearGraphQLClient",
    "LinearIssue",
    "LinearMergedGitHubPublication",
    "LinearProjectIssues",
    "LinearRateLimitError",
    "LinearRetrospectiveCompletionWriter",
    "LinearStatusMap",
    "LinearStatusMapError",
    "MissingLinearTokenError",
    "UnownedFieldError",
    "WorkflowState",
    "definition_payload",
    "reject_unowned_keys",
    "status_from_issue",
]

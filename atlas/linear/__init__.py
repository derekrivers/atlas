"""Linear integration boundary (Phase 4, ATLAS-41).

The Phase-4 provider boundary: a ``LinearClient`` protocol and its GraphQL
implementation, plus the field-ownership allow-list (definitions
Atlas -> Linear, status Linear -> Atlas, nothing else). A layer above
``atlas.core``, which imports nothing Linear-specific (ADR-0006). The sync
loop that drives this boundary on a cadence is ATLAS-42.
"""

from atlas.linear.client import (
    API_KEY_ENV,
    API_URL,
    TEAM_ID_ENV,
    LinearAPIError,
    LinearClient,
    LinearClientError,
    LinearGraphQLClient,
    LinearIssue,
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

__all__ = [
    "API_KEY_ENV",
    "API_URL",
    "OWNED_DEFINITION_FIELDS",
    "OWNED_LINEAR_INPUT_KEYS",
    "STATE_MAP_ENV",
    "TEAM_ID_ENV",
    "LinearAPIError",
    "LinearClient",
    "LinearClientError",
    "LinearGraphQLClient",
    "LinearIssue",
    "LinearStatusMap",
    "LinearStatusMapError",
    "MissingLinearTokenError",
    "UnownedFieldError",
    "WorkflowState",
    "definition_payload",
    "reject_unowned_keys",
    "status_from_issue",
]

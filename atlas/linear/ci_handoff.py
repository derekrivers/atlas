"""The exact Linear writer for Atlas-owned CI-pending exits."""

from __future__ import annotations

from atlas.core.models.ticket import TicketStatus
from atlas.linear.client import LinearClient, LinearIssue
from atlas.linear.ownership import LinearStatusMap

CI_HANDOFF_TARGETS: frozenset[TicketStatus] = frozenset(
    {TicketStatus.REVIEW_REQUIRED, TicketStatus.CHANGES_REQUESTED}
)


class LinearCIHandoffWriter:
    """Mechanically restrict a state mutation to the two Atlas-owned edges."""

    def __init__(self, client: LinearClient, status_map: LinearStatusMap) -> None:
        self._client = client
        self._status_map = status_map

    def target_state_id(self, target: TicketStatus) -> str:
        if target not in CI_HANDOFF_TARGETS:
            raise ValueError("CI handoff target must be review or rework")
        return self._status_map.state_id_for(target)

    def transition(
        self,
        issue_id: str,
        *,
        observed_source: TicketStatus,
        target: TicketStatus,
    ) -> LinearIssue:
        """Write exactly ``ci_pending`` to review or changes requested."""

        if observed_source is not TicketStatus.CI_PENDING:
            raise ValueError("CI handoff source must be ci_pending")
        target_state_id = self.target_state_id(target)
        return self._client.set_state(issue_id, target_state_id)

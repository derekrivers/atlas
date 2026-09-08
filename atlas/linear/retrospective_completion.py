"""The sole provider writer for retrospective historical completion."""

from __future__ import annotations

from atlas.core.models import TicketStatus
from atlas.linear.client import LinearClient, LinearIssue
from atlas.linear.ownership import LinearStatusMap


class LinearRetrospectiveCompletionWriter:
    """Mechanically restrict provider mutation to ci_pending -> done."""

    def __init__(self, client: LinearClient, status_map: LinearStatusMap) -> None:
        self._client = client
        self._status_map = status_map

    def target_state_id(self) -> str:
        return self._status_map.state_id_for(TicketStatus.DONE)

    def transition(
        self, issue_id: str, *, observed_source: TicketStatus
    ) -> LinearIssue:
        if observed_source is not TicketStatus.CI_PENDING:
            raise ValueError("retrospective completion source must be ci_pending")
        return self._client.set_state(issue_id, self.target_state_id())

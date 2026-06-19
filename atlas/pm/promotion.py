"""PM-Engine readiness promotion (ATLAS-43): step 3 of the
``pm-engine-and-linear-sync.md`` "Sync loop".

Detects tickets that have become dependency-ready (the Phase 3 ``is_ready``
predicate, ATLAS-34) and promotes them to ``Ready for Agent``. The PM Engine
is the SOLE writer of this transition, and it is the ONE sanctioned outbound
status write Atlas -> Linear: it goes through :meth:`LinearClient.set_state`
(a dedicated method that cannot carry a definition field), never the
definition-push path -- so ``OWNED_LINEAR_INPUT_KEYS`` stays sealed and every
other Atlas -> Linear status write remains mechanically impossible.

Linear-only write (the chosen side): this writes ONLY the Linear ``Ready for
Agent`` state. ATLAS-42's next pull reads it back as ``ready_for_agent`` and
writes the Atlas side via ``apply_linear_status`` (which never bumps
``updated_at``), keeping the pull the single writer of Atlas status -- at the
cost of one tick of latency. The write is idempotent (``set_state`` to the
already-set state is a no-op), so an interrupted or repeated promotion is safe.

Round-trip consistency (D4): the target state is resolved by inverting the
configured ``LinearStatusMap`` (:meth:`LinearStatusMap.state_id_for`), so the
id written is exactly the one the next pull maps back to ``ready_for_agent``.
The resolution requires a unique Ready-for-Agent state and raises otherwise,
up front, before any write -- the load-time guard.

Idempotency (D6): ``is_ready`` returns ready only for ``planned``/``backlog``,
so a ticket already in ``ready_for_agent`` is never re-promoted. No definition
is re-pushed here (D5: step 2 already mirrors it). Reuses the Phase 3 predicate
verbatim -- it does NOT reimplement readiness, and ``is_ready`` already gates on
criteria-present and ADR-accepted, not dependencies alone.

Dispatch readiness (criteria-present + pack-rendered) is NOT gated here: only
the ``pack-rendered`` conjunct is deferred (Phase 8); promoting without a pack
in Phase 4-7 is harmless because nothing dispatches off ``Ready for Agent``
until then (see the design doc step 3).
"""

from __future__ import annotations

import logging

import networkx as nx

from atlas.core.models.ticket import TicketStatus
from atlas.dependencies.readiness import ready_tickets
from atlas.linear.client import LinearClient
from atlas.linear.ownership import LinearStatusMap
from atlas.storage.repositories import TicketRepo

logger = logging.getLogger("atlas.pm.promotion")


def promote_ready(
    *,
    tickets: TicketRepo,
    graph: nx.DiGraph[str],
    client: LinearClient,
    status_map: LinearStatusMap,
) -> int:
    """Promote every dependency-ready ticket to ``Ready for Agent`` in Linear.

    Resolves the unique Ready-for-Agent Linear state up front (raising
    :class:`LinearStatusMapError` if zero or ambiguous -- the load-time guard,
    fired even when nothing is ready), then for each :func:`ready_tickets`
    result writes that state via the sanctioned :meth:`LinearClient.set_state`.
    Returns the count promoted.

    A ready ticket with no ``external_linear_id`` is skipped with a log: step 2's
    push creates the Linear issue for any pushable ticket before this runs, so
    in the live loop the id is always present; the skip only guards a
    promote-in-isolation call. Reads the graph and storage only and performs no
    Atlas-side status write -- the next pull reconciles Atlas.

    The graph is used unvalidated by design: a dangling or otherwise invalid
    target makes ``is_ready`` report not-ready (never crashes), so such a ticket
    is safely skipped rather than aborting the whole tick.
    """

    target_state_id = status_map.state_id_for(TicketStatus.READY_FOR_AGENT)
    promoted = 0
    for result in ready_tickets(graph):
        ticket = tickets.get_by_key(result.key)
        if ticket is None:
            continue  # graph and storage momentarily disagree; nothing to write
        if ticket.external_linear_id is None:
            logger.info(
                "linear-sync: %s is ready but has no Linear issue yet; promotion "
                "deferred until step 2 creates it",
                ticket.key,
            )
            continue
        client.set_state(ticket.external_linear_id, target_state_id)
        promoted += 1
        logger.info(
            "linear-sync: promoted %s to Ready for Agent (Linear state %s)",
            ticket.key,
            target_state_id,
        )
    return promoted

"""PM-Engine verified-completion (ATLAS-131): step 3b of the
``pm-engine-and-linear-sync.md`` "Sync loop", a sibling of :mod:`atlas.pm.promotion`.

The Verification Engine computes and persists a per-ticket verdict (append-only
``VerificationCheck`` rows written by ``atlas verify``) but deliberately never
transitions a ticket -- "the PM Engine acts on its verdict". This is that consumer:
on each tick it moves every ``review_required`` ticket whose PERSISTED verdict is
PASSED to ``Done`` in Linear, through the same sanctioned, Linear-only
:meth:`LinearClient.set_state` path the PM Engine already uses for ``Ready for
Agent`` (ATLAS-43) and ``Needs Human`` (ATLAS-120).

Verdict from the persisted rows (D3): the verdict is composed from the
``VerificationCheck`` rows ``atlas verify`` already wrote, via verification's pure
:func:`ticket_verdict_from_checks` (which folds them through the single-source-of-truth
``fold_statuses``) -- never by re-running the evaluators. So the PM Engine never couples
to evidence or GitHub and never redoes the evaluation work. A required check with no
persisted row composes to PENDING, so a missing required check holds the verdict and
never yields a false Done.

Eligibility (D6): only tickets in Atlas status ``review_required`` and only a PASSED
verdict. A FAILED verdict is the operator's ``changes_requested`` path, not this step's;
a PENDING verdict waits. Until operator-confirmation capture lands (OP-3), acceptance /
scope / human checks report PENDING, so real tickets compose to PENDING and this step
correctly does nothing on them.

Linear-only write (D7), mirroring :func:`promote_ready` exactly: this writes ONLY the
Linear ``Done`` state via :meth:`LinearClient.set_state` (never the definition-push
path, never an Atlas-side status write), so ``OWNED_LINEAR_INPUT_KEYS`` stays sealed and
the next tick's pull (ATLAS-42) reconciles the Atlas side -- keeping the pull the single
writer of Atlas status, at the cost of one tick of latency. The write is idempotent
(``set_state`` to the already-set state is a no-op), so a re-run before the pull
reconciles is safe (it re-counts the attempt, exactly as ``promote_ready`` /
``_detect_review_cycle`` do). A ticket with no ``external_linear_id`` is skipped with a
log, like :func:`promote_ready`.

Round-trip consistency (D2/D7): the Done state is resolved up front by inverting the
configured ``LinearStatusMap`` (:meth:`LinearStatusMap.state_id_for`), so the id written
is exactly the one the next pull maps back to ``done``. The resolution requires a unique
``done`` state and raises :class:`LinearStatusMapError` otherwise, up front, before any
write and even when nothing is completable -- the load-time guard, exactly like
``promote_ready``'s Ready-for-Agent resolution.

Layering: this is ``atlas.pm`` (above ``atlas.verification``/``atlas.storage``/
``atlas.linear``/``atlas.core`` in the import spine), so it may read the persisted rows
(storage) and hand them to the pure verification function -- the function itself stays
below and imports neither. Tests drive it with the in-memory fakes, so CI runs with no
network and no secrets.
"""

from __future__ import annotations

import logging

from atlas.core.enums import EvidenceStatus
from atlas.core.models.ticket import TicketStatus
from atlas.linear.client import LinearClient
from atlas.linear.ownership import LinearStatusMap
from atlas.storage.db import Database
from atlas.storage.repositories import EvidenceRepo, TicketRepo, VerificationCheckRepo
from atlas.verification.completion import (
    merge_confirmed,
    proof_evidence_ids,
    ticket_verdict_from_checks,
)

logger = logging.getLogger("atlas.pm.completion")


def complete_verified(
    *,
    tickets: TicketRepo,
    db: Database,
    client: LinearClient,
    status_map: LinearStatusMap,
) -> int:
    """Move every PASSED-verdict ``review_required`` ticket to ``Done`` in Linear.

    Resolves the unique ``Done`` Linear state up front (raising
    :class:`LinearStatusMapError` if zero or ambiguous -- the load-time guard, fired
    even when nothing is completable), then for each ``review_required`` ticket reads
    its persisted ``VerificationCheck`` rows, composes the verdict via the pure
    :func:`ticket_verdict_from_checks`, and -- when the verdict is PASSED -- writes that
    state via the sanctioned :meth:`LinearClient.set_state`. Returns the count moved.

    Eligibility is Atlas-status only (``review_required``); a FAILED or PENDING verdict
    is left untouched (the changes-requested path and the unproven case respectively).

    Done requires BOTH a PASSED verdict AND a merged PR (ATLAS-134; the operator's
    "definition of done is a merged PR" ruling, Option A -- a SEPARATE condition that
    leaves the verdict's meaning untouched). The merge is read locally from the
    system-tier ``PR_MERGED`` evidence ``atlas verify`` records (this never calls
    GitHub): the verdict's commit is resolved from the proof backing the PASSED checks
    (:func:`proof_evidence_ids`), and :func:`merge_confirmed` requires a system-tier
    ``PR_MERGED`` at that commit. An unmerged PR, no merge record, or a merge at a
    different commit (a re-push / second PR) defers completion, exactly as a PENDING
    verdict does.

    A completable ticket with no ``external_linear_id`` is skipped with a log (mirrors
    :func:`promote_ready`'s skip). Reads storage only and performs no Atlas-side status
    write -- the next pull reconciles Atlas. Idempotent: ``set_state`` to the
    already-set state is a no-op, so a re-run before the pull reconciles re-counts the
    attempt without a duplicate transition.
    """

    done_state_id = status_map.state_id_for(TicketStatus.DONE)
    checks = VerificationCheckRepo(db)
    # ATLAS-134: the merge gate reads persisted evidence locally (verify is the only
    # GitHub-touching writer). Load once and index by id so the proof commit can be
    # resolved from the PASSED checks' evidence_ids without a per-ticket round trip.
    all_evidence = EvidenceRepo(db).list()
    evidence_by_id = {e.id: e for e in all_evidence}
    completed = 0
    for ticket in tickets.list():
        if ticket.status is not TicketStatus.REVIEW_REQUIRED:
            continue  # only a ticket awaiting review can be completed by this step
        ticket_checks = checks.list_for_ticket(ticket.id)
        verdict = ticket_verdict_from_checks(ticket, ticket_checks)
        if verdict is not EvidenceStatus.PASSED:
            continue  # FAILED is the changes-requested path; PENDING waits
        # ATLAS-134: Done also requires the PR to be MERGED (operator ruling, Option A
        # -- a separate condition; the verdict still means "acceptable"). The verdict
        # carries no commit, so its commit is read from its proof: the evidence backing
        # the PASSED checks. A PR_MERGED at that commit completes; no record, or one at
        # a different commit (a re-push / second PR), waits -- exactly like a PENDING
        # verdict does today.
        proof_ids = proof_evidence_ids(ticket, ticket_checks)
        commit_shas: set[str] = set()
        for proof_id in proof_ids:
            record = evidence_by_id.get(proof_id)
            if record is not None and record.commit_sha is not None:
                commit_shas.add(record.commit_sha)
        ticket_evidence = [e for e in all_evidence if e.ticket_id == ticket.id]
        if not merge_confirmed(ticket_evidence, commit_shas=commit_shas):
            logger.info(
                "linear-sync: %s verified PASSED but its PR is not merged at the "
                "verdict commit; completion deferred",
                ticket.key,
            )
            continue
        if ticket.external_linear_id is None:
            logger.info(
                "linear-sync: %s verified PASSED but has no Linear issue; completion "
                "deferred until it is joined",
                ticket.key,
            )
            continue
        client.set_state(ticket.external_linear_id, done_state_id)
        completed += 1
        logger.info(
            "linear-sync: completed %s -> Done (verdict PASSED; Linear state %s)",
            ticket.key,
            done_state_id,
        )
    return completed

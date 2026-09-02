"""Canonical acceptance-criteria snapshots and stable fingerprints."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence

from atlas.core.models.acceptance_session import AcceptanceCriterionSnapshot
from atlas.core.models.ticket import Ticket


def acceptance_criteria_snapshot(
    close_set: Sequence[str], tickets: Iterable[Ticket]
) -> tuple[AcceptanceCriterionSnapshot, ...]:
    """Build the canonical key/index/text snapshot from live ticket models."""

    canonical_keys = tuple(sorted(set(close_set)))
    tickets_by_key: dict[str, Ticket] = {}
    for ticket in tickets:
        if ticket.key in tickets_by_key:
            raise ValueError("criteria snapshot received a duplicate ticket key")
        tickets_by_key[ticket.key] = ticket
    if set(tickets_by_key) != set(canonical_keys):
        raise ValueError("criteria snapshot tickets must equal the close-set")
    return tuple(
        AcceptanceCriterionSnapshot(
            ticket_key=key,
            criterion_index=index,
            text=criterion,
        )
        for key in canonical_keys
        for index, criterion in enumerate(tickets_by_key[key].acceptance_criteria)
    )


def acceptance_criteria_fingerprint(
    snapshot: Sequence[AcceptanceCriterionSnapshot],
) -> str:
    """Hash one ordered criteria snapshot as canonical UTF-8 JSON."""

    payload = [criterion.model_dump(mode="json") for criterion in snapshot]
    rendered = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return "sha256:" + hashlib.sha256(rendered.encode("utf-8")).hexdigest()

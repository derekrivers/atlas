"""Shared backlog-seed renderer (ATLAS-144).

One YAML seed grammar, parameterised by projection, serves both generation
paths so there is a single backlog-rendering primitive (never two that can
drift):

- ``projection="full"`` — the single-call full-state dump (the relocated
  ``pipeline._backlog_yaml``). Byte-identical to preserve planner-v1.2.0's
  ``current_backlog_yaml`` and thus the single-call ``prompt_hash``: same
  section order, the same per-entry full ``model_dump``, the same
  skip-empty/join, and NEVER re-sorted (the single-call path is out of scope).
- ``projection="epics"`` / ``projection="tickets"`` — the staged path's slim
  per-stage projections that let the model restate the backlog within each
  bounded stage (ADR-0010; planning-large-corpora.md §4). Stage 1 seeds the
  existing epics (key + title + source_anchor); stage 2 seeds one epic's
  existing tickets (+ status). These are rendered in natural-key order (the one
  ``core.keys.natural_key`` primitive) so the same store renders a
  byte-identical seed (planning-large-corpora.md §6, D-4).

The staged template variable is named ``current_backlog_yaml`` to match
planner-v1.2.0's grammar. The reconciler remains the sole authority on
create/update/archive: seeding only lets the model restate the full backlog
within per-stage budgets (D-2).
"""

from __future__ import annotations

from collections.abc import Sequence

import yaml

from atlas.core.keys import natural_key
from atlas.core.models import Epic, Ticket, TicketDependency


def render_backlog_yaml(
    *,
    epics: Sequence[Epic] = (),
    tickets: Sequence[Ticket] = (),
    dependencies: Sequence[TicketDependency] = (),
    projection: str = "full",
) -> str | None:
    """Render the current backlog as the ``current_backlog_yaml`` seed.

    ``projection`` selects the field subset and ordering:

    - ``"full"``: epics, tickets, and dependencies as full ``model_dump``s in
      the order supplied (the single-call dump; byte-identical, never sorted).
    - ``"epics"``: the epics slice, key + title + source_anchor, natural-key
      sorted.
    - ``"tickets"``: the tickets slice, key + title + source_anchor + status,
      natural-key sorted (the caller passes one epic's tickets).

    Returns ``None`` when the selected projection has no entries (a first run,
    or a new epic with no existing tickets), so the template renders its
    empty-backlog branch — first-run behaviour is unchanged (D-3).
    """
    if projection == "full":
        sections: list[tuple[str, list[dict[str, object]]]] = [
            ("epics", [epic.model_dump(mode="json") for epic in epics]),
            ("tickets", [ticket.model_dump(mode="json") for ticket in tickets]),
            (
                "dependencies",
                [dependency.model_dump(mode="json") for dependency in dependencies],
            ),
        ]
    elif projection == "epics":
        sections = [
            (
                "epics",
                [
                    {
                        "key": epic.key,
                        "title": epic.title,
                        "source_anchor": epic.source_anchor,
                    }
                    for epic in sorted(epics, key=lambda item: natural_key(item.key))
                ],
            )
        ]
    elif projection == "tickets":
        sections = [
            (
                "tickets",
                [
                    {
                        "key": ticket.key,
                        "title": ticket.title,
                        "source_anchor": ticket.source_anchor,
                        "status": ticket.status.value,
                    }
                    for ticket in sorted(
                        tickets, key=lambda item: natural_key(item.key)
                    )
                ],
            )
        ]
    else:  # pragma: no cover - guarded by the callers, kept total and typed
        raise ValueError(f"unknown seed projection {projection!r}")

    if not any(entries for _, entries in sections):
        return None
    rendered = [
        yaml.safe_dump({plural: entries}, sort_keys=False)
        for plural, entries in sections
        if entries
    ]
    return "\n".join(rendered)

"""roadmap.mmd renderer (ATLAS-27, gap 2).

A deterministic Mermaid `graph TD` of the applied backlog: ticket nodes
grouped into epic subgraphs, `depends_on` edges, and status-based classes.
Like the YAML renders it is byte-stable — identical backlogs produce
byte-identical output — and written only by `atlas apply`.

Edge direction: `SOURCE --> TARGET` reads "SOURCE depends on TARGET"
(knowledge-core "Planning render format"). Ordering is fixed: epic
subgraphs by natural epic key, nodes by natural ticket key, edges by
`(source, target)`. The header is emitted as Mermaid `%%` comments (not
the YAML `#` header) carrying the same fields. Archived items do not
appear — the renderer only ever sees the applied backlog.
"""

from __future__ import annotations

from collections.abc import Sequence

from atlas.core.keys import natural_key
from atlas.core.mermaid import escape_label
from atlas.core.models import Epic, Ticket, TicketDependency
from atlas.core.yaml_io import RenderHeader


def _mermaid_header(header: RenderHeader) -> list[str]:
    """The same fields as the YAML header, as Mermaid `%%` comments."""
    return [
        "%% Render written only by `atlas apply` (ADR-0006/0007); do not hand-edit.",
        f"%% plan_run_id: {header.plan_run_id}",
        f"%% prompt_version: {header.prompt_version}",
        f"%% ticket_key_high_water: {header.ticket_key_high_water}",
        f"%% epic_key_high_water: {header.epic_key_high_water}",
    ]


def render_roadmap(
    epics: Sequence[Epic],
    tickets: Sequence[Ticket],
    dependencies: Sequence[TicketDependency],
    header: RenderHeader,
) -> str:
    """Render the dependency DAG as deterministic Mermaid `graph TD`."""
    ticket_by_id = {ticket.id: ticket for ticket in tickets}
    epic_key_by_id = {epic.id: epic.key for epic in epics}

    # Tickets grouped by epic key (None -> ungrouped), each group sorted.
    grouped: dict[str | None, list[Ticket]] = {}
    for ticket in tickets:
        epic_key = epic_key_by_id.get(ticket.epic_id) if ticket.epic_id else None
        grouped.setdefault(epic_key, []).append(ticket)
    for group in grouped.values():
        group.sort(key=lambda ticket: natural_key(ticket.key))

    lines = [*_mermaid_header(header), "graph TD"]

    # Epic subgraphs in natural key order, then ungrouped tickets.
    for epic in sorted(epics, key=lambda epic: natural_key(epic.key)):
        members = grouped.get(epic.key, [])
        lines.append(f'  subgraph {epic.key}["{escape_label(epic.title)}"]')
        for ticket in members:
            label = escape_label(ticket.title)
            lines.append(f'    {ticket.key}["{ticket.key} {label}"]')
        lines.append("  end")
    for ticket in grouped.get(None, []):
        label = escape_label(ticket.title)
        lines.append(f'  {ticket.key}["{ticket.key} {label}"]')

    # depends_on edges: SOURCE --> TARGET, sorted by (source, target).
    edges = []
    for dependency in dependencies:
        if dependency.target_entity_type != "ticket":
            continue  # M1 roadmap is ticket-to-ticket (spec §4)
        source = ticket_by_id.get(dependency.source_ticket_id)
        target = ticket_by_id.get(dependency.target_entity_id)
        if source is not None and target is not None:
            edges.append((source.key, target.key))
    for source_key, target_key in sorted(
        edges, key=lambda edge: (natural_key(edge[0]), natural_key(edge[1]))
    ):
        lines.append(f"  {source_key} --> {target_key}")

    # Status-based classes: one classDef per status present, deterministic.
    statuses = sorted({ticket.status.value for ticket in tickets})
    for status in statuses:
        lines.append(f"  classDef {status} stroke-width:1px;")
    for status in statuses:
        member_keys = sorted(
            (ticket.key for ticket in tickets if ticket.status.value == status),
            key=natural_key,
        )
        if member_keys:
            lines.append(f"  class {','.join(member_keys)} {status};")

    return "\n".join(lines) + "\n"

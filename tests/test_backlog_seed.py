"""ATLAS-144: the one shared backlog-seed renderer (atlas.planning.seed).

`render_backlog_yaml` is the single backlog-rendering primitive for both
generation paths (A-1): the single-call full dump — pinned byte-identical to
the algorithm it replaced so the single-call prompt_hash is unchanged (the
single-call path is out of scope) — and the staged path's slim per-stage
projections, rendered in natural-key order so the same store renders a
byte-identical seed (AC-2, D-4).
"""

from __future__ import annotations

from typing import Any

import yaml
from test_models_validation import dependency_kwargs, epic_kwargs, ticket_kwargs

from atlas.core.models import Epic, Ticket, TicketDependency
from atlas.planning.seed import render_backlog_yaml


def _legacy_full_backlog_yaml(
    epics: list[Epic], tickets: list[Ticket], dependencies: list[TicketDependency]
) -> str | None:
    """The exact pre-ATLAS-144 `pipeline._backlog_yaml` algorithm, inlined here
    as the regression oracle: the `projection="full"` path must reproduce it
    byte-for-byte (no re-sort, full model_dump, same skip-empty/join)."""
    if not (epics or tickets or dependencies):
        return None
    sections = []
    for plural, entries in (
        ("epics", epics),
        ("tickets", tickets),
        ("dependencies", dependencies),
    ):
        if entries:
            payload = {plural: [entry.model_dump(mode="json") for entry in entries]}
            sections.append(yaml.safe_dump(payload, sort_keys=False))
    return "\n".join(sections)


def _epic(key: str, **overrides: Any) -> Epic:
    return Epic(**epic_kwargs() | {"key": key} | overrides)


def _ticket(key: str, **overrides: Any) -> Ticket:
    return Ticket(**ticket_kwargs() | {"key": key, "status": "backlog"} | overrides)


# --- full projection: byte-identical to the algorithm it replaced -----------


def test_full_projection_is_byte_identical_to_the_legacy_dump() -> None:
    epics = [_epic("ATLAS-E2"), _epic("ATLAS-E1")]  # deliberately unsorted
    tickets = [_ticket("ATLAS-10"), _ticket("ATLAS-2")]
    dependencies = [TicketDependency(**dependency_kwargs())]
    assert render_backlog_yaml(
        epics=epics, tickets=tickets, dependencies=dependencies, projection="full"
    ) == _legacy_full_backlog_yaml(epics, tickets, dependencies)


def test_full_projection_preserves_input_order_never_resorts() -> None:
    # The single-call dump must NOT be re-sorted (out of scope): E2 before E1.
    out = render_backlog_yaml(
        epics=[_epic("ATLAS-E2"), _epic("ATLAS-E1")], projection="full"
    )
    assert out is not None
    assert out.index("ATLAS-E2") < out.index("ATLAS-E1")


def test_full_projection_is_none_when_all_empty() -> None:
    assert render_backlog_yaml(projection="full") is None


def test_full_projection_skips_empty_sections() -> None:
    out = render_backlog_yaml(epics=[_epic("ATLAS-E1")], projection="full")
    assert out is not None
    assert "epics:" in out
    assert "tickets:" not in out
    assert "dependencies:" not in out


# --- slim staged projections: natural-key order, field subset ----------------


def test_epics_projection_is_slim_and_natural_key_sorted() -> None:
    out = render_backlog_yaml(
        epics=[_epic("ATLAS-E10"), _epic("ATLAS-E2")], projection="epics"
    )
    assert out is not None
    parsed = yaml.safe_load(out)
    # Field subset: key + title + source_anchor only (no status/priority/…).
    assert set(parsed["epics"][0]) == {"key", "title", "source_anchor"}
    # Natural-key order: E2 before E10 (not lexical E10 < E2).
    assert [e["key"] for e in parsed["epics"]] == ["ATLAS-E2", "ATLAS-E10"]


def test_tickets_projection_is_slim_with_status_natural_key_sorted() -> None:
    out = render_backlog_yaml(
        tickets=[
            _ticket("ATLAS-10", status="ready_for_agent"),
            _ticket("ATLAS-2", status="backlog"),
        ],
        projection="tickets",
    )
    assert out is not None
    parsed = yaml.safe_load(out)
    assert set(parsed["tickets"][0]) == {"key", "title", "source_anchor", "status"}
    assert [t["key"] for t in parsed["tickets"]] == ["ATLAS-2", "ATLAS-10"]
    # status is carried (it is what tells the model an item is in flight).
    assert parsed["tickets"][0]["status"] == "backlog"


def test_slim_projections_are_none_when_empty() -> None:
    assert render_backlog_yaml(projection="epics") is None
    assert render_backlog_yaml(projection="tickets") is None


# --- AC-2: deterministic — the same store renders a byte-identical seed -------


def test_seed_is_byte_identical_across_renders() -> None:
    epics = [_epic("ATLAS-E3"), _epic("ATLAS-E1"), _epic("ATLAS-E2")]
    first = render_backlog_yaml(epics=epics, projection="epics")
    second = render_backlog_yaml(epics=epics, projection="epics")
    assert first == second
    tickets = [_ticket("ATLAS-5"), _ticket("ATLAS-1")]
    assert render_backlog_yaml(
        tickets=tickets, projection="tickets"
    ) == render_backlog_yaml(tickets=tickets, projection="tickets")

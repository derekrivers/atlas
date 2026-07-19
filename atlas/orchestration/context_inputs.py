"""Cross-layer assembly of the inputs consumed by a context-pack build."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import networkx as nx

from atlas.context import retrieve_lessons
from atlas.core.anchors import SourceDocument
from atlas.core.models.adr import ADRStatus, ArchitectureDecisionRecord
from atlas.core.models.lesson import Lesson
from atlas.core.models.ticket import Ticket
from atlas.dependencies import build_dependency_graph
from atlas.planning.ingestion import (
    collect_input_documents,
    collect_processed_documents,
)
from atlas.planning.pipeline import DEFAULT_INBOX_DIR
from atlas.storage import ADRRepo, Database, LessonRepo, TicketRepo


class ContextNotFoundError(Exception):
    """A bare ``<KEY>`` that resolves to no stored ticket (D2/D6).

    A clean CLI precondition — the loader raises it instead of returning
    ``None``, so the command surfaces a one-line message and exits
    ``EXIT_PRECONDITION`` rather than dereferencing ``None`` into a traceback.
    """


class ContextInputs(NamedTuple):
    """The five already-loaded inputs the pure ``atlas.context`` builder and
    validator take (D2). Loaded once per invocation by ``load_context_inputs``;
    the three commands are thin wrappers over this tuple. ``lessons`` is the
    renderer's ACTIVE-only retrieval result; ``validation_lessons`` is the full
    catalogue so a tampered pack can identify a DRAFT/ARCHIVED lesson by status
    instead of treating it as dangling."""

    ticket: Ticket
    graph: nx.DiGraph[str]
    documents: list[SourceDocument]
    accepted_adrs: list[ArchitectureDecisionRecord]
    lessons: list[Lesson]
    validation_lessons: list[Lesson]


def load_context_inputs(key: str, repo_root: Path, db: Database) -> ContextInputs:
    """Turn a bare ``<KEY>`` into the five inputs ``build_context_pack`` /
    ``validate_context_pack`` consume (D2). This is the substance of ATLAS-58;
    the commands are thin wrappers.

    - ``ticket`` from ``TicketRepo.get_by_key``; a missing key raises
      ``ContextNotFoundError`` (never a ``None`` dereference).
    - ``graph`` is the GLOBAL dependency projection over the full backlog
      (``build_dependency_graph`` = ``project_graph`` over the four repos); the
      retrievers select from it.
    - ``documents`` are re-ingested from HEAD every invocation: the §2.1 corpus
      (``collect_input_documents``) plus the committed retired stubs
      (``collect_processed_documents``, ATLAS-162) so a stub-minted ticket's
      durable ``inbox/processed/`` anchor resolves for the pack exactly as it
      does at gate 4; live re-ingestion keeps staleness real, and a
      dirty/untracked file in either set raises ``DirtyInputError``.
    - ``accepted_adrs`` is ``ADRRepo.list()`` filtered to ACCEPTED.
    - ``lessons`` is ``retrieve_lessons(ticket, db)``: the DB-backed retriever
      filters to ACTIVE in SQL, then applies the v1 tag/ticket_type match.
    - ``validation_lessons`` is the full lesson catalogue for the defensive
      validator's no-DRAFT check against externally/tampered packs.
    """
    ticket = TicketRepo(db).get_by_key(key)
    if ticket is None:
        raise ContextNotFoundError(f"no ticket with key {key!r}")
    graph = build_dependency_graph(db)
    documents = collect_input_documents(repo_root) + collect_processed_documents(
        repo_root, DEFAULT_INBOX_DIR
    )
    accepted_adrs = [
        adr for adr in ADRRepo(db).list() if adr.status == ADRStatus.ACCEPTED
    ]
    lessons = retrieve_lessons(ticket, db)
    validation_lessons = LessonRepo(db).list()
    return ContextInputs(
        ticket, graph, documents, accepted_adrs, lessons, validation_lessons
    )

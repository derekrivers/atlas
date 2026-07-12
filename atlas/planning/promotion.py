"""Deterministic inbox-stub promotion (ATLAS-146).

A committed follow-up inbox stub (``docs/planning/inbox/<name>.md``) carries a
YAML front-matter block that *is* a ticket definition. Promotion parses that
block into a :class:`ProposalTicket` and injects it into the parsed proposal —
pure code, zero model involvement (ADR-0005: no inferred quantitative values,
no LLM judgement). Same stub + same backlog ⇒ byte-identical injected ADD.

Where the model-generated proposal already carries the semantic backlog, this
runs post-parse / pre-gates in the plan pipeline: the injected tickets flow
through the ordinary gates → reconcile → apply path and are materialised with
monotonic keys like any other ADD.

Contract (planning-engine-specification.md §2.1):

- Every committed inbox stub MUST carry a valid front-matter block. A stub with
  a missing or malformed block is a typed :class:`StubPromotionError` at plan
  time — fail-closed, mirroring ``DirtyInputError``; never a silent skip, never
  a malformed ticket.
- Required semantic fields come from the front-matter; ``source_anchor``,
  ``relevant_docs``, ``tags``, ``component``, ``implementation_notes`` and
  ``documentation_requirements`` default to mechanical values; ``priority`` /
  ``risk_level`` come from the front-matter or a single pinned constant each
  (never inferred per-stub).
- ``epic_ref`` names the epic the ticket belongs to. When it is an existing
  epic key that the parsed proposal omitted (or emitted keyless), the epic is
  re-stated verbatim from the backlog into the proposal so the ADD is anchored
  independently of what the model emitted (A-1). A ``new_epic:<n>`` ref is
  model-owned and left for the gates to validate.
- ``depends_on`` (optional, ATLAS-153) declares the promoted ticket's edges
  deterministically — the channel the stubs-only path needs, since it has no
  model to emit edges, honoured identically on the generative path so a stub
  means the same thing at both doors. Each entry is a string: an existing
  ticket key becomes a new->existing edge (an unknown key fails gate 3,
  typed); a sibling stub FILENAME in the same batch (matched on basename,
  ``.md`` suffix) becomes a new->new edge; an ``.md`` entry naming no sibling
  is a fail-closed :class:`StubPromotionError`. The edge reason is a single
  pinned constant (ADR-0005: never inferred per-stub).

Retire/dedup is the existing ``processed/`` lifecycle: ``collect_inbox_documents``
reads only top-level ``inbox/*.md`` and ``atlas apply`` retires consumed stubs to
``inbox/processed/`` — a promoted, applied stub is never re-read, so it never
yields a second ADD.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

import yaml
from pydantic import ValidationError

from atlas.core.anchors import (
    AnchorIndex,
    IngestionError,
    SourceDocument,
    UnknownDocumentError,
)
from atlas.core.enums import RiskLevel
from atlas.core.models.dependency import DependencyType
from atlas.planning.ingestion import processed_path_for
from atlas.planning.proposal import (
    Proposal,
    ProposalDependency,
    ProposalEpic,
    ProposalTicket,
)
from atlas.planning.reconciler import Backlog

# D-3: pinned mechanical defaults. Never inferred per-stub (ADR-0005). Taken
# from the front-matter when present, else these constants.
_DEFAULT_PRIORITY = 50
_DEFAULT_RISK_LEVEL = RiskLevel.LOW

# The one edge reason a depends_on entry yields (ATLAS-153): a pinned
# mechanical constant, never inferred per-stub (ADR-0005). An operator who
# wants a richer reason edits the minted edge, not the stub.
_DEPENDS_ON_REASON = "declared by the stub's depends_on front-matter (ATLAS-153)"

# Front-matter is a YAML block delimited by --- at the very start of the stub,
# same shape the template renderer parses (renderer.py).
_FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)

# Fields the front-matter MUST carry (the semantic ticket definition, D-2).
_REQUIRED_FIELDS = (
    "title",
    "objective",
    "context",
    "ticket_type",
    "epic_ref",
    "acceptance_criteria",
    "non_goals",
    "test_requirements",
    "definition_of_done",
)


class StubPromotionError(IngestionError):
    """A committed inbox stub cannot be promoted: missing/invalid front-matter,
    a bad field, or an ``epic_ref`` naming an epic absent from the backlog.

    Fail-closed at plan time, mirroring ``DirtyInputError``; carries the stub
    ``path`` and the offending ``field`` so the operator can fix the stub.
    """

    def __init__(self, path: str, field: str, detail: str) -> None:
        super().__init__(
            f"inbox stub {path!r} cannot be promoted: {detail} (field {field!r})"
        )
        self.path = path
        self.field = field


def _is_existing_key_ref(ref: str) -> bool:
    """An ``epic_ref`` naming an existing backlog epic key, as opposed to a
    model-owned ``new_epic:<n>`` / ``new:<n>`` placeholder the gates validate."""
    return not (ref.startswith("new_epic:") or ref.startswith("new:"))


def _parse_front_matter(document: SourceDocument) -> dict[str, object]:
    match = _FRONT_MATTER_RE.match(document.content)
    if match is None:
        raise StubPromotionError(
            document.path,
            "<front-matter>",
            "no YAML front-matter block at the top of the stub",
        )
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as error:
        raise StubPromotionError(
            document.path, "<front-matter>", f"front-matter is not valid YAML: {error}"
        ) from error
    if not isinstance(data, dict):
        raise StubPromotionError(
            document.path, "<front-matter>", "front-matter is not a mapping"
        )
    return data


def _stub_anchor(document: SourceDocument, anchor_index: AnchorIndex) -> str:
    """The stub's own first heading at its durable ``processed/`` path — its
    default ``source_anchor`` (D-2, ATLAS-159).

    Apply retires the stub to ``inbox/processed/`` the moment its plan lands,
    so an anchor minted against the active-inbox path dangles from its own
    apply onward. The durable path is known at promotion time (retirement is a
    pure move; slugs are byte-identical at both addresses) and the pipeline
    indexes each active stub under it too, so gate 4 resolves this anchor in
    the minting run and in every run after retirement.
    """
    durable = processed_path_for(document.path)
    try:
        slugs = anchor_index.slugs_for(durable)
    except UnknownDocumentError as error:  # pragma: no cover - defensive
        raise StubPromotionError(
            document.path,
            "source_anchor",
            f"stub's durable path is not in the indexed input set: {error}",
        ) from error
    if not slugs:
        raise StubPromotionError(
            document.path,
            "source_anchor",
            "stub has no heading to anchor to; add a heading or declare source_anchor",
        )
    return f"{durable}#{slugs[0]}"


def _build_ticket(
    document: SourceDocument, data: dict[str, object], anchor_index: AnchorIndex
) -> ProposalTicket:
    for field in _REQUIRED_FIELDS:
        if field not in data or data[field] in (None, "", []):
            raise StubPromotionError(
                document.path, field, "missing required front-matter field"
            )
    fields: dict[str, object] = {
        "key": None,  # an ADD: the reconciler mints the key at apply (ADR-0007)
        "epic_ref": data["epic_ref"],
        "title": data["title"],
        "objective": data["objective"],
        "context": data["context"],
        "ticket_type": data["ticket_type"],
        "acceptance_criteria": data["acceptance_criteria"],
        "non_goals": data["non_goals"],
        "test_requirements": data["test_requirements"],
        "definition_of_done": data["definition_of_done"],
        # Mechanical defaults (D-2/D-3): front-matter overrides, else pinned.
        "risk_level": data.get("risk_level", _DEFAULT_RISK_LEVEL),
        "priority": data.get("priority", _DEFAULT_PRIORITY),
        "source_anchor": data.get("source_anchor")
        or _stub_anchor(document, anchor_index),
        "relevant_docs": data.get("relevant_docs") or [document.path],
        "tags": data.get("tags") or [],
        "component": data.get("component"),
        "implementation_notes": data.get("implementation_notes") or [],
        "documentation_requirements": data.get("documentation_requirements") or [],
    }
    try:
        return ProposalTicket(**fields)  # type: ignore[arg-type]
    except ValidationError as error:
        first = error.errors()[0]
        loc = first.get("loc") or ("<unknown>",)
        raise StubPromotionError(
            document.path, str(loc[0]), f"invalid ticket field: {first.get('msg')}"
        ) from error


def _depends_on_refs(document: SourceDocument, data: dict[str, object]) -> list[str]:
    """The stub's optional ``depends_on`` list (ATLAS-153), validated: a list
    of non-empty strings, or absent/empty for no declared edges. Anything else
    is fail-closed, like every other front-matter defect."""
    raw = data.get("depends_on")
    if raw is None or raw == []:
        return []
    if not isinstance(raw, list) or not all(
        isinstance(entry, str) and entry for entry in raw
    ):
        raise StubPromotionError(
            document.path,
            "depends_on",
            "depends_on must be a list of non-empty strings (existing ticket "
            "keys or sibling stub filenames)",
        )
    return raw


def _promoted_dependencies(
    inbox_documents: list[SourceDocument],
    front_matter: list[dict[str, object]],
    base_index: int,
) -> list[ProposalDependency]:
    """Build the depends_on edges the batch's stubs declare (ATLAS-153).

    ``base_index`` is where the promoted tickets start in the final proposal
    (they are appended at the tail), so stub ``position`` promotes to proposal
    ticket ``new:<base_index + position>``. An ``.md`` entry must name a
    sibling stub in this batch (basename match) — a new->new edge; naming no
    sibling, or the stub itself, is fail-closed. Any other entry is emitted
    verbatim as the target: an existing backlog key resolves, and an unknown
    key is gate 3's typed ``GATE3_UNRESOLVED_TARGET`` failure — promotion
    never guesses what a bad reference meant.
    """
    index_by_basename = {
        PurePosixPath(document.path).name: base_index + position
        for position, document in enumerate(inbox_documents)
    }
    edges: list[ProposalDependency] = []
    for position, (document, data) in enumerate(
        zip(inbox_documents, front_matter, strict=True)
    ):
        source_index = base_index + position
        for entry in _depends_on_refs(document, data):
            if entry.endswith(".md"):
                sibling = index_by_basename.get(entry)
                if sibling is None:
                    raise StubPromotionError(
                        document.path,
                        "depends_on",
                        f"names sibling stub {entry!r}, which is not in this "
                        "inbox batch",
                    )
                if sibling == source_index:
                    raise StubPromotionError(
                        document.path,
                        "depends_on",
                        f"names the stub itself ({entry!r}); a ticket cannot "
                        "depend on itself",
                    )
                target = f"new:{sibling}"
            else:
                target = entry
            edges.append(
                ProposalDependency(
                    source=f"new:{source_index}",
                    target=target,
                    dependency_type=DependencyType.DEPENDS_ON,
                    reason=_DEPENDS_ON_REASON,
                )
            )
    return edges


def _restate_epic(epic: object) -> ProposalEpic:
    """Re-state a backlog epic verbatim as a keyed ProposalEpic (A-1).

    Every field is echoed from the backlog so the reconciler sees a no-op match
    on its key (not an apply-blocking MODIFY): the epic is present purely to
    anchor the promoted ticket independently of the model's output.
    """
    return ProposalEpic(
        key=epic.key,  # type: ignore[attr-defined]
        title=epic.title,  # type: ignore[attr-defined]
        description=epic.description,  # type: ignore[attr-defined]
        objective=epic.objective,  # type: ignore[attr-defined]
        priority=epic.priority,  # type: ignore[attr-defined]
        risk_level=epic.risk_level,  # type: ignore[attr-defined]
        source_anchor=epic.source_anchor,  # type: ignore[attr-defined]
    )


def promote_inbox_stubs(
    proposal: Proposal,
    inbox_documents: list[SourceDocument],
    backlog: Backlog,
    anchor_index: AnchorIndex,
) -> Proposal:
    """Return ``proposal`` with one deterministically-built ADD ticket injected
    per committed inbox stub, plus any backlog epic those tickets need re-stated
    (A-1) and any depends_on edges the stubs declare (ATLAS-153). An empty
    inbox is a no-op — the proposal is returned unchanged.
    """
    if not inbox_documents:
        return proposal

    front_matter = [_parse_front_matter(document) for document in inbox_documents]
    promoted = [
        _build_ticket(document, data, anchor_index)
        for document, data in zip(inbox_documents, front_matter, strict=True)
    ]
    # depends_on edges (ATLAS-153): the promoted tickets are appended at the
    # tail below, so the batch starts at today's ticket count.
    added_dependencies = _promoted_dependencies(
        inbox_documents, front_matter, base_index=len(proposal.tickets)
    )

    # A-1: guarantee each existing-key epic_ref is present-and-keyed in the
    # proposal, re-stating from the backlog when the model omitted it, so the
    # promotion is self-contained rather than dependent on the model re-emitting
    # the epic with its key.
    backlog_epic_by_key = {epic.key: epic for epic in backlog.epics}
    present_keys = {epic.key for epic in proposal.epics if epic.key is not None}
    added_epics: list[ProposalEpic] = []
    added_keys: set[str] = set()
    for ticket, document in zip(promoted, inbox_documents, strict=True):
        ref = ticket.epic_ref
        if ref is None or not _is_existing_key_ref(ref):
            continue
        if ref in present_keys or ref in added_keys:
            continue
        backlog_epic = backlog_epic_by_key.get(ref)
        if backlog_epic is None:
            raise StubPromotionError(
                document.path,
                "epic_ref",
                f"names epic {ref!r}, which is neither in the proposal nor the backlog",
            )
        added_epics.append(_restate_epic(backlog_epic))
        added_keys.add(ref)

    return Proposal(
        epics=list(proposal.epics) + added_epics,
        tickets=list(proposal.tickets) + promoted,
        dependencies=list(proposal.dependencies) + added_dependencies,
        planner_notes=proposal.planner_notes,
    )

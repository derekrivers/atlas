"""Deterministic reconciler (ATLAS-24), per spec §4 and ADR-0007.

Pure function: (gate-passed full-state Proposal, current canonical
backlog, per-run threshold) -> PlanDiff. No I/O, no git, no database,
no randomness. Matching passes in spec order — key, anchor
(unambiguous 1:1 groups only), similarity (Sorensen-Dice over the
normalised token sets of title + objective, greedy by descending
score) — with earlier-pass precedence. Existing items unmatched by any
pass become PROPOSE_ARCHIVE; nothing is ever deleted. Frozen tickets
(spec §4) turn MODIFY/PROPOSE_ARCHIVE into CONFLICT carrying what the
entry would have been. Ambiguity is fail-closed: duplicate echoed keys
and exact similarity ties are CONFLICT, never an arbitrary choice.

Promotion dedup (ATLAS-151): before the matching passes, a keyless
model-emitted ticket whose source_anchor equals a promotion-injected
ticket's anchor is collapsed into the promotion ticket — it takes no
part in matching, emits no ADD, and its dependency refs re-point to the
survivor, deduplicated. Which tickets are promotion-injected is
POSITIONAL identity (``promotion_indices``, supplied by the caller):
three live reproductions (the declined 2026-07-08 double-emission,
ATLAS-149/150, ATLAS-155/158) matched or diverged on every candidate
content feature — edges, anchors, titles, full content — so no
recognition heuristic over duplicate features is stable. Deterministic
promotion content always wins; the diff records one collapse note per
absorbed ticket. For this pre-pass equality only, an active-inbox stub
anchor and its durable ``inbox/processed/`` spelling share one
canonical identity; stored anchors, gate 4, and the anchor index keep
their byte-identical spellings. Boundary: a duplicate citing a foreign
anchor never collides with a promotion anchor and is deliberately NOT
collapsed — foreign-anchor re-emission remains gate-read territory.

Dependency edges reconcile by resolved (source, target) identity:
proposal-only ADD, backlog-only PROPOSE_ARCHIVE, changed reason MODIFY;
a frozen SOURCE conflicts (the edge is the source's planning surface),
a frozen target is permitted.

Rendering and persistence are ATLAS-26's; key assignment is ATLAS-25's.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from atlas.core.models import Epic, Ticket, TicketDependency, TicketStatus
from atlas.planning.proposal import Proposal, ProposalEpic, ProposalTicket

DEFAULT_SIMILARITY_THRESHOLD = 0.85

# Spec §4: tickets in these statuses are frozen to planning.
FROZEN_STATUSES: frozenset[TicketStatus] = frozenset(
    {
        TicketStatus.IN_PROGRESS,
        TicketStatus.PR_OPEN,
        TicketStatus.REVIEW_REQUIRED,
        TicketStatus.CHANGES_REQUESTED,
        TicketStatus.DONE,
        TicketStatus.REJECTED,
    }
)

ADD = "ADD"
MODIFY = "MODIFY"
PROPOSE_ARCHIVE = "PROPOSE_ARCHIVE"
CONFLICT = "CONFLICT"

_TYPE_ORDER = {ADD: 0, MODIFY: 1, PROPOSE_ARCHIVE: 2, CONFLICT: 3}
_KIND_ORDER = {"epic": 0, "ticket": 1, "dependency": 2}

_NON_ALNUM_RE = re.compile(r"[^0-9a-z]+")
_NATURAL_RE = re.compile(r"(\d+)")
_STUB_ANCHOR_IDENTITY_RE = re.compile(
    r"^(docs/planning/inbox/)(?:processed/)?([^/#]+\.md)(#.*)?$"
)

_TICKET_FIELDS = (
    "title",
    "objective",
    "context",
    "ticket_type",
    "risk_level",
    "priority",
    "source_anchor",
    "relevant_docs",
    "acceptance_criteria",
    "non_goals",
    "test_requirements",
    "implementation_notes",
    "documentation_requirements",
    "definition_of_done",
)
_EPIC_FIELDS = (
    "title",
    "description",
    "objective",
    "priority",
    "risk_level",
    "source_anchor",
)


def normalise_tokens(text: str) -> frozenset[str]:
    """Spec §4 normalisation: casefold; every non-alphanumeric character
    becomes a space; whitespace-split into a token set."""
    return frozenset(_NON_ALNUM_RE.sub(" ", text.casefold()).split())


def similarity(title_a: str, objective_a: str, title_b: str, objective_b: str) -> float:
    """Sorensen-Dice over token sets of concatenated title + objective."""
    tokens_a = normalise_tokens(f"{title_a} {objective_a}")
    tokens_b = normalise_tokens(f"{title_b} {objective_b}")
    if not tokens_a and not tokens_b:
        return 1.0
    return 2 * len(tokens_a & tokens_b) / (len(tokens_a) + len(tokens_b))


def containment(roadmap_title: str, proposed_title: str) -> float:
    """Directional containment ratio: the fraction of the FIRST title's tokens
    that also appear in the SECOND (ATLAS-124). Reuses the single tokeniser
    (``normalise_tokens``); a separate function that does NOT alter
    ``similarity`` — the reconciler's matching semantics are untouched.

    Asymmetric by design: it credits "every roadmap token is present in the
    proposal" regardless of how much *extra* the proposal says, which is exactly
    the length-asymmetry false negative ``content_coverage`` suffers when a terse
    roadmap title is a literal subset of a longer proposed title. Disjoint token
    sets give 0.0, so it can only raise recall on genuine subset matches — it
    cannot manufacture a cross-ticket false positive. An empty roadmap title
    (no tokens) returns 0.0, never a vacuous 1.0."""
    roadmap_tokens = normalise_tokens(roadmap_title)
    if not roadmap_tokens:
        return 0.0
    proposed_tokens = normalise_tokens(proposed_title)
    return len(roadmap_tokens & proposed_tokens) / len(roadmap_tokens)


@dataclass(frozen=True)
class Backlog:
    """The current backlog, canonical models (loading is ATLAS-26's)."""

    epics: Sequence[Epic] = ()
    tickets: Sequence[Ticket] = ()
    dependencies: Sequence[TicketDependency] = ()


@dataclass(frozen=True)
class DiffEntry:
    """One diff entry, carrying everything spec §2.4 presents."""

    entry_type: str  # ADD | MODIFY | PROPOSE_ARCHIVE | CONFLICT
    kind: str  # epic | ticket | dependency
    identity: str  # key, new:<n>, new_epic:<n>, or "src -> tgt"
    title: str
    anchor: str | None = None
    changes: dict[str, tuple[Any, Any]] = field(default_factory=dict)
    would_have_been: str | None = None  # CONFLICT: the suppressed type
    reason: str | None = None  # CONFLICT: why

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.entry_type,
            "kind": self.kind,
            "identity": self.identity,
            "title": self.title,
            "anchor": self.anchor,
            "changes": {
                name: [before, after] for name, (before, after) in self.changes.items()
            },
            "would_have_been": self.would_have_been,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CollapseNote:
    """One promotion-dedup collapse (ATLAS-151): the absorbed model ticket,
    the surviving promotion ticket, and the shared anchor that keyed it.

    Not a DiffEntry: a collapse is not an ADD/MODIFY/PROPOSE_ARCHIVE/CONFLICT
    on the backlog, it is a note that a proposal ticket was folded into
    another before matching — so it must not disturb the four-type counts
    contract apply's refusal logic and key assignment read."""

    absorbed: str  # new:<n> of the absorbed model ticket
    absorbed_title: str
    survivor: str  # resolved identity of the promotion ticket
    anchor: str  # the shared source_anchor (the promotion side's)

    def as_dict(self) -> dict[str, str]:
        return {
            "absorbed": self.absorbed,
            "absorbed_title": self.absorbed_title,
            "survivor": self.survivor,
            "anchor": self.anchor,
        }


@dataclass(frozen=True)
class PlanDiff:
    """Deterministically ordered entries plus counts per type (§2.4)."""

    entries: tuple[DiffEntry, ...]
    # Promotion-dedup collapses (ATLAS-151), ordered by absorbed index.
    collapses: tuple[CollapseNote, ...] = ()

    @property
    def counts(self) -> dict[str, int]:
        counts = {ADD: 0, MODIFY: 0, PROPOSE_ARCHIVE: 0, CONFLICT: 0}
        for entry in self.entries:
            counts[entry.entry_type] += 1
        return counts

    @property
    def is_empty(self) -> bool:
        return not self.entries

    def as_summary(self) -> dict[str, Any]:
        """The PlanRun.diff_summary shape: counts + entries by type."""
        return {
            "counts": self.counts,
            "entries": [entry.as_dict() for entry in self.entries],
            "collapses": [note.as_dict() for note in self.collapses],
        }


def _natural(identity: str) -> tuple[object, ...]:
    return tuple(
        int(part) if part.isdigit() else part for part in _NATURAL_RE.split(identity)
    )


def _entry_sort_key(entry: DiffEntry) -> tuple[object, ...]:
    return (
        _KIND_ORDER[entry.kind],
        _TYPE_ORDER[entry.entry_type],
        _natural(entry.identity),
    )


def _plain(value: Any) -> Any:
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if hasattr(value, "value") and not isinstance(value, str | int | float):
        return value.value
    if isinstance(value, str):
        return str(value)  # collapses StrEnum members to plain strings
    return value


def _collapse_anchor_identity(anchor: str) -> str:
    """Canonical key for promotion-collapse comparison only (ATLAS-161).

    Active stubs are indexed under both ``docs/planning/inbox/<name>.md`` and
    ``docs/planning/inbox/processed/<name>.md`` while promotion tickets store
    the durable spelling. The collapse pre-pass treats those spellings as the
    same stub identity without rewriting the proposal, diff, gate resolution,
    or stored anchors.
    """
    match = _STUB_ANCHOR_IDENTITY_RE.fullmatch(anchor)
    if match is None:
        return anchor
    fragment = match.group(3) or ""
    return f"{match.group(1)}{match.group(2)}{fragment}"


@dataclass
class _MatchState:
    """Matching outcome for one entity kind."""

    matches: dict[int, str]  # proposal index -> existing key
    conflicted_proposal: set[int]
    conflicted_existing: set[str]
    conflicts: list[DiffEntry]
    # Promotion dedup (ATLAS-151): absorbed model index -> surviving
    # promotion index. Absorbed indices take no part in any matching pass
    # and emit no entries; dependency refs to them re-point to the survivor.
    absorbed: dict[int, int] = field(default_factory=dict)


def _identity(item: ProposalEpic | ProposalTicket, index: int, kind: str) -> str:
    if item.key is not None:
        return item.key
    return f"new_epic:{index}" if kind == "epic" else f"new:{index}"


def _match_items(
    proposal_items: Sequence[ProposalEpic | ProposalTicket],
    existing_by_key: dict[str, Epic | Ticket],
    kind: str,
    absorbed: dict[int, int] | None = None,
) -> _MatchState:
    """Passes 1 and 2 (key, then unambiguous-1:1 anchor). ``absorbed``
    (ATLAS-151) removes collapsed indices from the matching universe —
    notably restoring pass 2's 1:1 anchor rule, which the duplicate's
    shared anchor would otherwise defeat as an ambiguous group."""
    state = _MatchState({}, set(), set(), [], dict(absorbed or {}))

    # Pass 1: key. Duplicate echoed keys are a CONFLICT (spec §4).
    # (Absorbed items are keyless by construction, so they claim nothing.)
    claims: dict[str, list[int]] = {}
    for index, item in enumerate(proposal_items):
        if item.key is not None and item.key in existing_by_key:
            claims.setdefault(item.key, []).append(index)
    for key, claimants in sorted(claims.items()):
        if len(claimants) == 1:
            state.matches[claimants[0]] = key
        else:
            names = ", ".join(
                f"{_identity(proposal_items[i], i, kind)} ({proposal_items[i].title!r})"
                for i in claimants
            )
            state.conflicts.append(
                DiffEntry(
                    entry_type=CONFLICT,
                    kind=kind,
                    identity=key,
                    title=existing_by_key[key].title,
                    anchor=existing_by_key[key].source_anchor,
                    reason=f"duplicate echoed key {key!r}: claimed by {names}",
                )
            )
            state.conflicted_existing.add(key)
            state.conflicted_proposal.update(claimants)

    # Pass 2: anchor — unambiguous 1:1 groups only; ambiguity falls
    # through to similarity (spec §4 match-ambiguity rule).
    free_proposal = [
        index
        for index in range(len(proposal_items))
        if index not in state.matches
        and index not in state.conflicted_proposal
        and index not in state.absorbed
    ]
    matched_keys = set(state.matches.values()) | state.conflicted_existing
    free_existing = [key for key in sorted(existing_by_key) if key not in matched_keys]
    proposal_by_anchor: dict[str, list[int]] = {}
    for index in free_proposal:
        proposal_by_anchor.setdefault(proposal_items[index].source_anchor, []).append(
            index
        )
    existing_by_anchor: dict[str, list[str]] = {}
    for key in free_existing:
        existing_by_anchor.setdefault(existing_by_key[key].source_anchor, []).append(
            key
        )
    for anchor, indices in sorted(proposal_by_anchor.items()):
        keys = existing_by_anchor.get(anchor, [])
        if len(indices) == 1 and len(keys) == 1:
            state.matches[indices[0]] = keys[0]

    return state


def _similarity_pass(
    proposal_items: Sequence[ProposalEpic | ProposalTicket],
    existing_by_key: dict[str, Epic | Ticket],
    state: _MatchState,
    threshold: float,
    kind: str,
) -> None:
    """Pass 3: greedy by descending score; exact ties competing for the
    same item are CONFLICT (spec §4)."""
    free_proposal = sorted(
        index
        for index in range(len(proposal_items))
        if index not in state.matches
        and index not in state.conflicted_proposal
        and index not in state.absorbed
    )
    taken = set(state.matches.values()) | state.conflicted_existing
    free_existing = sorted(key for key in existing_by_key if key not in taken)

    pairs = []
    for index in free_proposal:
        item = proposal_items[index]
        for key in free_existing:
            existing = existing_by_key[key]
            score = similarity(
                item.title, item.objective, existing.title, existing.objective
            )
            if score >= threshold:
                pairs.append((score, index, key))
    pairs.sort(key=lambda pair: (-pair[0], pair[1], pair[2]))

    position = 0
    while position < len(pairs):
        score = pairs[position][0]
        block = [pair for pair in pairs if pair[0] == score]
        position += len(block)
        live = [
            (index, key)
            for (_, index, key) in block
            if index not in state.matches
            and index not in state.conflicted_proposal
            and key not in state.matches.values()
            and key not in state.conflicted_existing
        ]
        proposal_counts: dict[int, list[str]] = {}
        existing_counts: dict[str, list[int]] = {}
        for index, key in live:
            proposal_counts.setdefault(index, []).append(key)
            existing_counts.setdefault(key, []).append(index)
        for index, keys in sorted(proposal_counts.items()):
            if len(keys) > 1:
                item = proposal_items[index]
                state.conflicts.append(
                    DiffEntry(
                        entry_type=CONFLICT,
                        kind=kind,
                        identity=_identity(item, index, kind),
                        title=item.title,
                        anchor=item.source_anchor,
                        reason=(
                            f"similarity tie at {score:.4f} between existing "
                            f"items {sorted(keys)}; ambiguous match"
                        ),
                    )
                )
                state.conflicted_proposal.add(index)
        for key, indices in sorted(existing_counts.items()):
            if len(indices) > 1 and key not in state.conflicted_existing:
                live_indices = [
                    i for i in indices if i not in state.conflicted_proposal
                ]
                if len(live_indices) > 1:
                    existing = existing_by_key[key]
                    state.conflicts.append(
                        DiffEntry(
                            entry_type=CONFLICT,
                            kind=kind,
                            identity=key,
                            title=existing.title,
                            anchor=existing.source_anchor,
                            reason=(
                                f"similarity tie at {score:.4f}: claimed by "
                                f"proposal items {sorted(live_indices)}"
                            ),
                        )
                    )
                    state.conflicted_existing.add(key)
        for index, key in live:
            if (
                index not in state.matches
                and index not in state.conflicted_proposal
                and key not in state.matches.values()
                and key not in state.conflicted_existing
            ):
                state.matches[index] = key


def _diff_fields(
    proposal_item: ProposalEpic | ProposalTicket,
    existing: Epic | Ticket,
    names: Sequence[str],
) -> dict[str, tuple[Any, Any]]:
    changes = {}
    for name in names:
        before = _plain(getattr(existing, name))
        after = _plain(getattr(proposal_item, name))
        if before != after:
            changes[name] = (before, after)
    return changes


def _absorb_promoted_duplicates(
    tickets: Sequence[ProposalTicket], promotion_indices: frozenset[int]
) -> dict[int, int]:
    """The ATLAS-151 collapse pre-pass: map each keyless model-emitted ticket
    whose source_anchor equals a promotion-injected ticket's anchor to that
    promotion ticket's index. Runs before the three matching passes; indices
    are never renumbered, so ``new:<n>`` identities keep indexing the stored
    proposal and apply's materialisation is untouched. Keyed tickets echo
    existing backlog tickets (never ADD candidates) and are left alone."""
    anchor_to_promotion: dict[str, int] = {}
    for index in sorted(promotion_indices):
        if 0 <= index < len(tickets):
            anchor_to_promotion.setdefault(
                _collapse_anchor_identity(tickets[index].source_anchor), index
            )
    absorbed: dict[int, int] = {}
    for index, ticket in enumerate(tickets):
        if index in promotion_indices or ticket.key is not None:
            continue
        survivor = anchor_to_promotion.get(
            _collapse_anchor_identity(ticket.source_anchor)
        )
        if survivor is not None:
            absorbed[index] = survivor
    return absorbed


def reconcile(
    proposal: Proposal,
    backlog: Backlog,
    *,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    promotion_indices: frozenset[int] = frozenset(),
) -> PlanDiff:
    """Reconcile a gate-passed proposal against the current backlog.

    ``promotion_indices`` (ATLAS-151) names the proposal tickets injected by
    ``promote_inbox_stubs`` — positional identity, supplied by the caller
    (`atlas plan` records the indices it appended; `atlas apply` reconstructs
    the same trailing set under the AT-5 staleness guarantee). Empty set ⇒
    no collapse, byte-for-byte the pre-ATLAS-151 behaviour."""
    entries: list[DiffEntry] = []

    epics_by_key = {epic.key: epic for epic in backlog.epics}
    tickets_by_key = {ticket.key: ticket for ticket in backlog.tickets}
    epic_key_by_id = {epic.id: epic.key for epic in backlog.epics}
    ticket_key_by_id = {ticket.id: ticket.key for ticket in backlog.tickets}

    absorbed = _absorb_promoted_duplicates(proposal.tickets, promotion_indices)

    epic_state = _match_items(proposal.epics, epics_by_key, "epic")  # type: ignore[arg-type]
    _similarity_pass(
        proposal.epics,
        epics_by_key,  # type: ignore[arg-type]
        epic_state,
        similarity_threshold,
        "epic",
    )
    ticket_state = _match_items(
        proposal.tickets,
        tickets_by_key,  # type: ignore[arg-type]
        "ticket",
        absorbed,
    )
    _similarity_pass(
        proposal.tickets,
        tickets_by_key,  # type: ignore[arg-type]
        ticket_state,
        similarity_threshold,
        "ticket",
    )
    entries.extend(epic_state.conflicts)
    entries.extend(ticket_state.conflicts)

    # Epic identities for ticket epic_ref comparison.
    def epic_identity(index: int) -> str:
        if index in epic_state.matches:
            return epic_state.matches[index]
        item = proposal.epics[index]
        return item.key if item.key is not None else f"new_epic:{index}"

    def resolve_epic_ref(ref: str | None) -> str | None:
        if ref is None:
            return None
        if ref.startswith("new_epic:"):
            return epic_identity(int(ref.removeprefix("new_epic:")))
        return ref

    # Epic entries.
    for index, epic_item in enumerate(proposal.epics):
        if index in epic_state.conflicted_proposal:
            continue
        if index not in epic_state.matches:
            entries.append(
                DiffEntry(
                    entry_type=ADD,
                    kind="epic",
                    identity=_identity(epic_item, index, "epic"),
                    title=epic_item.title,
                    anchor=epic_item.source_anchor,
                )
            )
            continue
        existing_epic = epics_by_key[epic_state.matches[index]]
        changes = _diff_fields(epic_item, existing_epic, _EPIC_FIELDS)
        if changes:
            entries.append(
                DiffEntry(
                    entry_type=MODIFY,
                    kind="epic",
                    identity=existing_epic.key,
                    title=existing_epic.title,
                    anchor=epic_item.source_anchor,
                    changes=changes,
                )
            )
    matched_epic_keys = set(epic_state.matches.values())
    for key in sorted(epics_by_key):
        if key not in matched_epic_keys and key not in epic_state.conflicted_existing:
            existing_epic = epics_by_key[key]
            entries.append(
                DiffEntry(
                    entry_type=PROPOSE_ARCHIVE,
                    kind="epic",
                    identity=key,
                    title=existing_epic.title,
                    anchor=existing_epic.source_anchor,
                )
            )

    # Ticket entries, with frozen-ticket immutability (spec §4).
    frozen_keys = {
        ticket.key for ticket in backlog.tickets if ticket.status in FROZEN_STATUSES
    }
    for index, ticket_item in enumerate(proposal.tickets):
        if index in ticket_state.absorbed:
            continue  # collapsed into its promotion ticket (ATLAS-151)
        if index in ticket_state.conflicted_proposal:
            continue
        if index not in ticket_state.matches:
            entries.append(
                DiffEntry(
                    entry_type=ADD,
                    kind="ticket",
                    identity=_identity(ticket_item, index, "ticket"),
                    title=ticket_item.title,
                    anchor=ticket_item.source_anchor,
                )
            )
            continue
        existing_ticket = tickets_by_key[ticket_state.matches[index]]
        changes = _diff_fields(ticket_item, existing_ticket, _TICKET_FIELDS)
        existing_epic_key = (
            epic_key_by_id.get(existing_ticket.epic_id)
            if existing_ticket.epic_id is not None
            else None
        )
        proposed_epic = resolve_epic_ref(ticket_item.epic_ref)
        if existing_epic_key != proposed_epic:
            changes["epic_ref"] = (existing_epic_key, proposed_epic)
        if not changes:
            continue
        if existing_ticket.key in frozen_keys:
            entries.append(
                DiffEntry(
                    entry_type=CONFLICT,
                    kind="ticket",
                    identity=existing_ticket.key,
                    title=existing_ticket.title,
                    anchor=existing_ticket.source_anchor,
                    changes=changes,
                    would_have_been=MODIFY,
                    reason=(
                        f"ticket {existing_ticket.key!r} is frozen "
                        f"(status {existing_ticket.status.value!r}); frozen "
                        "tickets are immutable to planning (spec §4)"
                    ),
                )
            )
        else:
            entries.append(
                DiffEntry(
                    entry_type=MODIFY,
                    kind="ticket",
                    identity=existing_ticket.key,
                    title=existing_ticket.title,
                    anchor=ticket_item.source_anchor,
                    changes=changes,
                )
            )
    matched_ticket_keys = set(ticket_state.matches.values())
    for key in sorted(tickets_by_key):
        if key in matched_ticket_keys or key in ticket_state.conflicted_existing:
            continue
        existing_ticket = tickets_by_key[key]
        if key in frozen_keys:
            entries.append(
                DiffEntry(
                    entry_type=CONFLICT,
                    kind="ticket",
                    identity=key,
                    title=existing_ticket.title,
                    anchor=existing_ticket.source_anchor,
                    would_have_been=PROPOSE_ARCHIVE,
                    reason=(
                        f"ticket {key!r} is frozen (status "
                        f"{existing_ticket.status.value!r}); frozen tickets "
                        "are immutable to planning (spec §4)"
                    ),
                )
            )
        else:
            entries.append(
                DiffEntry(
                    entry_type=PROPOSE_ARCHIVE,
                    kind="ticket",
                    identity=key,
                    title=existing_ticket.title,
                    anchor=existing_ticket.source_anchor,
                )
            )

    # Dependency edges: resolved (source, target) identity (spec §4).
    # An absorbed ref re-points to its surviving promotion ticket before
    # normal resolution (ATLAS-151); the (source, target) dict then dedupes
    # re-pointed edges against the survivor's own edges for free.
    def resolve_ticket_ref(value: str) -> str:
        if value.startswith("new:"):
            index = int(value.removeprefix("new:"))
            index = ticket_state.absorbed.get(index, index)
            return ticket_state.matches.get(index, f"new:{index}")
        return value

    def is_absorbed_ref(value: str) -> bool:
        return (
            value.startswith("new:")
            and int(value.removeprefix("new:")) in ticket_state.absorbed
        )

    proposal_edges: dict[tuple[str, str], str] = {}
    for dependency in proposal.dependencies:
        identity_pair = (
            resolve_ticket_ref(dependency.source),
            resolve_ticket_ref(dependency.target),
        )
        if identity_pair[0] == identity_pair[1] and (
            is_absorbed_ref(dependency.source) or is_absorbed_ref(dependency.target)
        ):
            continue  # re-pointing made a duplicate<->survivor edge a self-loop
        proposal_edges[identity_pair] = dependency.reason
    backlog_edges: dict[tuple[str, str], str] = {}
    for backlog_dependency in backlog.dependencies:
        if backlog_dependency.target_entity_type != "ticket":
            continue  # M1 reconciles ticket-to-ticket edges only (§3.11)
        source_key = ticket_key_by_id.get(backlog_dependency.source_ticket_id)
        target_key = ticket_key_by_id.get(backlog_dependency.target_entity_id)
        if source_key is None or target_key is None:
            continue  # dangling rows are ATLAS-19's validator's concern
        backlog_edges[(source_key, target_key)] = backlog_dependency.reason

    def edge_entry(
        entry_type: str,
        pair: tuple[str, str],
        reason_text: str,
        changes: dict[str, tuple[Any, Any]] | None = None,
    ) -> DiffEntry:
        source, target = pair
        if source in frozen_keys:
            return DiffEntry(
                entry_type=CONFLICT,
                kind="dependency",
                identity=f"{source} -> {target}",
                title=reason_text,
                changes=changes or {},
                would_have_been=entry_type,
                reason=(
                    f"edge source {source!r} is frozen; frozen tickets are "
                    "immutable to planning (spec §4)"
                ),
            )
        return DiffEntry(
            entry_type=entry_type,
            kind="dependency",
            identity=f"{source} -> {target}",
            title=reason_text,
            changes=changes or {},
        )

    for pair in sorted(set(proposal_edges) - set(backlog_edges)):
        entries.append(edge_entry(ADD, pair, proposal_edges[pair]))
    for pair in sorted(set(backlog_edges) - set(proposal_edges)):
        entries.append(edge_entry(PROPOSE_ARCHIVE, pair, backlog_edges[pair]))
    for pair in sorted(set(proposal_edges) & set(backlog_edges)):
        if proposal_edges[pair] != backlog_edges[pair]:
            entries.append(
                edge_entry(
                    MODIFY,
                    pair,
                    proposal_edges[pair],
                    changes={"reason": (backlog_edges[pair], proposal_edges[pair])},
                )
            )

    # One collapse note per absorbed ticket (ATLAS-151), by absorbed index.
    # The survivor's identity resolves exactly like a dependency ref would:
    # its matched key when a pass claimed it, else its own new:<n>.
    collapses = tuple(
        CollapseNote(
            absorbed=f"new:{index}",
            absorbed_title=proposal.tickets[index].title,
            survivor=ticket_state.matches.get(survivor, f"new:{survivor}"),
            anchor=proposal.tickets[survivor].source_anchor,
        )
        for index, survivor in sorted(ticket_state.absorbed.items())
    )

    return PlanDiff(
        entries=tuple(sorted(entries, key=_entry_sort_key)), collapses=collapses
    )

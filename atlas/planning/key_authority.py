"""Key authority (ATLAS-25): the pure assignment half of key minting.

Per spec §2.2 steps 4-5 and knowledge-core "Key counter", keys are
assigned only at apply, only by the environment (ADR-0007). This module
is the deterministic, side-effect-free half (gap 2): it takes a
gate-passed reconciler PlanDiff plus the two current high-water marks and
produces the key map

    new:<n>      -> ATLAS-<k>     (tickets, from the ticket counter)
    new_epic:<n> -> ATLAS-E<k>   (epics, from the epic counter)

with `resolve()` as the reference-rewriting primitive ATLAS-27 applies to
each ticket's epic_ref and each dependency endpoint. The stateful half —
reading and advancing the persisted counters inside the apply
transaction — is atlas.storage.KeyCounterRepo.reserve.

Determinism: ADD items are numbered by ascending placeholder index, which
the reconciler sets to the item's position in the proposal's epics /
tickets array — so the lower proposal position always takes the lower
key, and an identical (PlanDiff, marks) pair always yields an identical
map. Tickets and epics draw from their own marks, independently.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from atlas.planning.reconciler import ADD, PlanDiff

# Spec §2.2 step 4 / data-model §3.11: ticket keys are ATLAS-<n>, epic
# keys ATLAS-E<n>. The prefix is the row identity in key_counters (§3.12).
TICKET_PREFIX = "ATLAS"
EPIC_PREFIX = "ATLAS-E"

_NEW_TICKET_RE = re.compile(r"^new:(\d+)$")
_NEW_EPIC_RE = re.compile(r"^new_epic:(\d+)$")


class KeyReferenceError(ValueError):
    """A reference names a placeholder with no assigned key — an
    out-of-range new:<n>/new_epic:<n>. This happens when the referenced
    proposal item was dropped to CONFLICT (so it received no ADD entry
    and no key) or the index lies past the proposal."""


@dataclass(frozen=True)
class KeyAssignment:
    """The result of a deterministic assignment pass.

    key_map carries every placeholder this diff introduced; the two
    high-water fields are the new marks to persist (KeyCounterRepo.reserve)
    and to record in the render header.
    """

    key_map: Mapping[str, str]
    ticket_high_water: int
    epic_high_water: int

    def resolve(self, reference: str) -> str:
        """Rewrite one reference to its assigned key.

        A real key (anything not a new:/new_epic: placeholder) passes
        through unchanged — existing items echo their keys. A placeholder
        is looked up in key_map; an unknown one is a typed error, never a
        silent pass-through."""
        if reference.startswith(("new:", "new_epic:")):
            try:
                return self.key_map[reference]
            except KeyError as error:
                raise KeyReferenceError(
                    f"reference {reference!r} has no assigned key; it is an "
                    "out-of-range placeholder (no ADD item was numbered for it)"
                ) from error
        return reference


def _add_indices(diff: PlanDiff, kind: str, pattern: re.Pattern[str]) -> list[int]:
    """Ascending placeholder indices of the ADD entries of one kind."""
    indices = []
    for entry in diff.entries:
        if entry.kind == kind and entry.entry_type == ADD:
            match = pattern.match(entry.identity)
            if match is not None:
                indices.append(int(match.group(1)))
    return sorted(indices)


def assign_keys(
    diff: PlanDiff, *, ticket_high_water: int, epic_high_water: int
) -> KeyAssignment:
    """Assign keys to a diff's ADD items from the current high-water marks.

    Pure: no I/O, no session, no randomness. Epics and tickets are
    numbered independently from their own marks, each ascending by
    placeholder index."""
    key_map: dict[str, str] = {}

    epic_hw = epic_high_water
    for index in _add_indices(diff, "epic", _NEW_EPIC_RE):
        epic_hw += 1
        key_map[f"new_epic:{index}"] = f"{EPIC_PREFIX}{epic_hw}"

    ticket_hw = ticket_high_water
    for index in _add_indices(diff, "ticket", _NEW_TICKET_RE):
        ticket_hw += 1
        key_map[f"new:{index}"] = f"{TICKET_PREFIX}-{ticket_hw}"

    return KeyAssignment(
        key_map=key_map, ticket_high_water=ticket_hw, epic_high_water=epic_hw
    )

"""Trust-tier derivation per ADR-0008 (ATLAS-11).

This module is the only place tier logic lives
(docs/architecture/knowledge-core.md, "Trust-tier enforcement").
EvidenceRepo (ATLAS-14/18) consumes ``evidence_tier`` to cap agent-tier
evidence at PENDING; it must not reimplement the mapping.
"""

from typing import Literal

from atlas.core.enums import ActorType

EvidenceTierName = Literal["system", "human", "agent"]


class InvalidActorTypeError(ValueError):
    """Raised when a value is not a valid ActorType."""

    def __init__(self, value: object) -> None:
        super().__init__(
            f"invalid actor type {value!r}; expected one of "
            f"{[member.value for member in ActorType]}"
        )
        self.value = value


_TIER_BY_ACTOR: dict[ActorType, EvidenceTierName] = {
    ActorType.SYSTEM: "system",
    ActorType.HUMAN: "human",
    ActorType.AGENT: "agent",
}


def evidence_tier(created_by_type: ActorType | str) -> EvidenceTierName:
    """Derive the evidence trust tier from a record's creating actor.

    Accepts an ``ActorType`` member or its stored string form
    (``created_by_type`` is TEXT in the database, data-model §1.5).
    Raises :class:`InvalidActorTypeError` for anything else. There is no
    bypass parameter (ADR-0008).
    """
    try:
        actor = ActorType(created_by_type)
    except ValueError as exc:
        raise InvalidActorTypeError(created_by_type) from exc
    return _TIER_BY_ACTOR[actor]

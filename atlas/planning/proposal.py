"""Proposal models and parser (ATLAS-23).

The models transcribe data-model-and-schemas.md §3.11 exactly. Per the
§1 inheritance rule, shared-name fields carry the canonical models'
types and constraints. Per the gap-2 boundary rule (spec §5
attribution): per-item, context-free requirements live here and fail
as gate 1 (schema validity); cross-item and external-context checks
are the explicit gates (atlas.planning.gates).

Proposal items carry no id, status, timestamps, or created_by fields —
system-owned fields are assigned at apply (ADR-0007); extra="forbid"
makes that mechanical.
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from atlas.core.enums import RiskLevel
from atlas.core.models import DependencyType, TicketType

_INT_MIN = -2147483648
_INT_MAX = 2147483647

_NEW_TICKET_RE = re.compile(r"^new:(\d+)$")
_NEW_EPIC_RE = re.compile(r"^new_epic:(\d+)$")


class ProposalError(ValueError):
    """Base for parse-stage failures — all gate-1 attributable."""


class ProposalParseError(ProposalError):
    """The raw model output is not valid JSON (spec §2.1 step 4)."""


class ProposalValidationError(ProposalError):
    """The payload violates the §3.11 contract (gate 1)."""


class ProposalReferenceError(ProposalError):
    """new:<n>/new_epic:<n> forms or bounds are invalid (parse-time,
    per the prompts README sensitivity)."""


class ProposalEpic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str | None
    title: str
    description: str
    objective: str
    priority: int = Field(ge=_INT_MIN, le=_INT_MAX)  # canonical bounds
    risk_level: RiskLevel
    source_anchor: str


class ProposalTicket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str | None
    epic_ref: str | None
    title: str
    objective: str
    context: str = Field(min_length=1)
    ticket_type: TicketType
    risk_level: RiskLevel
    priority: int = Field(ge=_INT_MIN, le=_INT_MAX)  # canonical bounds
    source_anchor: str
    relevant_docs: list[str]
    # Free-form retrieval facets the planner populates (ATLAS-128), mirroring
    # the stored Ticket's ATLAS-127 fields. Required like every §3.11 field —
    # no default, in the relevant_docs / key|epic_ref pattern (a no-default
    # list[str] / str | None): the planner always emits the keys, with []/null
    # when nothing fits. They carry into the stored Ticket at materialisation.
    tags: list[str]
    component: str | None
    acceptance_criteria: list[str] = Field(min_length=1, max_length=7)
    non_goals: list[str] = Field(min_length=1)
    test_requirements: list[str] = Field(min_length=1)
    implementation_notes: list[str]
    documentation_requirements: list[str]
    definition_of_done: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _epic_ref_null_only_for_tech_debt(self) -> ProposalTicket:
        if self.epic_ref is None and self.ticket_type is not TicketType.TECH_DEBT:
            raise ValueError(
                "epic_ref may be null only when ticket_type is tech_debt "
                "(data-model §3.11)"
            )
        return self


class ProposalDependency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    dependency_type: DependencyType
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def _depends_on_only_in_m1(self) -> ProposalDependency:
        if self.dependency_type is not DependencyType.DEPENDS_ON:
            raise ValueError(
                "proposal dependencies are depends_on only in Milestone 1 "
                "(data-model §3.11)"
            )
        return self


class Proposal(BaseModel):
    """The envelope: exactly four keys (data-model §3.11)."""

    model_config = ConfigDict(extra="forbid")

    epics: list[ProposalEpic]
    tickets: list[ProposalTicket]
    dependencies: list[ProposalDependency]
    planner_notes: list[str]


def _reference_problems(proposal: Proposal) -> list[str]:
    problems = []
    epic_count = len(proposal.epics)
    ticket_count = len(proposal.tickets)

    for index, ticket in enumerate(proposal.tickets):
        ref = ticket.epic_ref
        if ref is None:
            continue
        if ref.startswith("new_epic:"):
            match = _NEW_EPIC_RE.match(ref)
            if not match:
                problems.append(
                    f"tickets[{index}].epic_ref {ref!r} is a malformed "
                    "new_epic:<n> reference"
                )
            elif int(match.group(1)) >= epic_count:
                problems.append(
                    f"tickets[{index}].epic_ref {ref!r} is out of bounds "
                    f"(the proposal has {epic_count} epic(s))"
                )
        elif ref.startswith("new:"):
            problems.append(
                f"tickets[{index}].epic_ref {ref!r} uses the ticket "
                "namespace; epics are referenced as new_epic:<n>"
            )

    for index, dependency in enumerate(proposal.dependencies):
        for field_name, value in (
            ("source", dependency.source),
            ("target", dependency.target),
        ):
            if value.startswith("new_epic:"):
                problems.append(
                    f"dependencies[{index}].{field_name} {value!r} references "
                    "an epic; dependencies are ticket-to-ticket only in "
                    "Milestone 1 (data-model §3.11)"
                )
            elif value.startswith("new:"):
                match = _NEW_TICKET_RE.match(value)
                if not match:
                    problems.append(
                        f"dependencies[{index}].{field_name} {value!r} is a "
                        "malformed new:<n> reference"
                    )
                elif int(match.group(1)) >= ticket_count:
                    problems.append(
                        f"dependencies[{index}].{field_name} {value!r} is out "
                        f"of bounds (the proposal has {ticket_count} ticket(s))"
                    )
    return problems


def extract_json_object(raw: str) -> str:
    """Return the JSON object embedded in raw model output, tolerant of a
    surrounding markdown code fence or leading/trailing prose (ATLAS-108).

    The planner templates instruct "no surrounding text", but a prompt
    instruction is a request, not a guarantee: a live staged run returned
    valid JSON wrapped in a ```json ... ``` fence, and ``json.loads`` from
    char 0 failed. A fence is just leading/trailing prose around the object,
    so one mechanism handles both — locate the first ``{`` and scan, with
    brace-depth and JSON-string/escape awareness, to its matching ``}``,
    returning that slice. A ``}`` inside a string value does not end the
    object; an escaped quote does not close the string.

    This is a parse-time convenience only: callers hash the UNSTRIPPED raw
    output for provenance before parsing, so the strip never touches the
    hashed bytes. The helper never raises and never invents an error: when no
    balanced object is found (genuine garbage), the original string is
    returned so the caller's ``json.loads`` fails with the existing typed
    parse error. Bare JSON is a no-op (the slice parses to the same object).
    """
    start = raw.find("{")
    if start == -1:
        return raw  # no object opener: let json.loads fail as today
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(raw)):
        char = raw[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return raw[start : index + 1]
    return raw  # unbalanced object: let json.loads fail as today


def parse_proposal(raw: str) -> Proposal:
    """Raw model output -> validated Proposal; every failure typed.

    Index bounds for new:<n>/new_epic:<n> are validated here, at parse
    time, before the reconciler ever runs (prompts README sensitivity).

    A surrounding code fence or leading/trailing prose is tolerated
    (extract_json_object, ATLAS-108); genuine non-JSON still fails as a
    typed ProposalParseError.
    """
    try:
        payload = json.loads(extract_json_object(raw))
    except json.JSONDecodeError as error:
        raise ProposalParseError(f"model output is not valid JSON: {error}") from error
    try:
        proposal = Proposal.model_validate(payload)
    except ValidationError as error:
        raise ProposalValidationError(
            f"proposal violates the §3.11 contract: {error}"
        ) from error
    problems = _reference_problems(proposal)
    if problems:
        raise ProposalReferenceError(
            "invalid proposal references: " + "; ".join(problems)
        )
    return proposal

"""Multi-call generation orchestration (ATLAS-104, ADR-0010).

Staged generation produces one full-state §3.11 proposal across three
bounded model calls — epics, then tickets (one call per epic), then
dependencies — assembled by *pure, deterministic environment code* into a
single envelope that the existing parser, gates, and reconciler accept
unchanged (planning-large-corpora.md §4/§4.1/§5.1). The model emits
content; the environment owns ALL identity (ADR-0007): ``new_epic:<n>``
by stage-1 emission order, ``new:<n>`` by stage-2 assembled position. A
model-supplied index is impossible by construction — the per-stage output
models are ``extra="forbid"`` and carry no index field — and every
reference the model DOES make (``epic_ref``, dependency endpoints) is
bound-checked here, never trusted (gap 1).

The three per-stage projection schemas reuse the §3.11 field models
verbatim (gap 2): ``StageEpicsOutput`` wraps ``ProposalEpic`` and so on,
so the schema embedded in each prompt cannot drift from the contract.
These wrapper models are deliberately NOT exported from
``atlas.planning`` — the canonical exported set stays exactly the four
§3.11 models (Proposal, ProposalEpic, ProposalTicket, ProposalDependency),
so no new ``docs/generated`` schema is required: §3.11 is unchanged.

ATLAS-105 seam: ``StagedGenerationResult.stage_records`` carries the
per-stage ``(prompt_version, prompt_hash, raw_output_hash)`` records; the
pipeline folds them into a composite top-level prompt hash but does not
yet persist them structurally — the ``generation_stages`` PlanRun field is
ATLAS-105. Per-epic batch SIZING (packing small epics under a token
budget) is ATLAS-106; this module does exactly one call per epic.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ConfigDict, ValidationError

from atlas.planning.client import PlannerClient, TruncatedOutputError
from atlas.planning.proposal import (
    Proposal,
    ProposalDependency,
    ProposalEpic,
    ProposalTicket,
    extract_json_object,
)
from atlas.planning.renderer import RenderedPrompt, render_planner_prompt

# The staged templates (ATLAS-103). Distinct version lineage from the
# single-call planner-v* templates; selected by explicit version=.
STAGE_EPICS_VERSION = "planner-stage-epics-v1.0.0"
# v1.2.0 (ATLAS-110): the output-contract example shows "key": null and an
# anti-copy instruction forbids transcribing ATLAS-<n> keys from the corpus
# (a live run failed gate 6 by copying roadmap keys). v1.1.0 retained.
STAGE_TICKETS_VERSION = "planner-stage-tickets-v1.2.0"
STAGE_DEPENDENCIES_VERSION = "planner-stage-dependencies-v1.0.0"

# ATLAS-109: bounded directed retry on a projection-bound graze. A stage that
# emits valid JSON violating a §3.11 field bound (the measured case: a ticket
# with 8 acceptance_criteria against the ≤7 cap) is re-called WITH a directed
# correction up to this many total attempts (the original call plus retries),
# then fails honestly with the typed StageOutputError. The cap is enforced,
# never relaxed — this only makes delivery resilient to the model's occasional
# one-over. A named constant, not config: 3 is the value (D1).
MAX_STAGE_ATTEMPTS = 3

_NEW_TICKET_RE = re.compile(r"^new:(\d+)$")


# --- per-stage output projections (gap 2: reuse the §3.11 field models) ------


class StageEpicsOutput(BaseModel):
    """Stage 1 emission: the epics-only projection of §3.11. ``extra=forbid``
    makes a model-supplied index (e.g. a ``new_epic`` field) a parse error —
    identity-minting is structurally impossible (ADR-0007)."""

    model_config = ConfigDict(extra="forbid")

    epics: list[ProposalEpic]
    planner_notes: list[str]


class StageTicketsOutput(BaseModel):
    """Stage 2 emission: one epic's tickets. The model carries the seeded
    ``epic_ref`` but assigns no ``new:<n>`` (no such field exists)."""

    model_config = ConfigDict(extra="forbid")

    tickets: list[ProposalTicket]
    planner_notes: list[str]


class StageDependenciesOutput(BaseModel):
    """Stage 3 emission: the depends_on edges over the assembled ``new:<n>``
    indices the environment assigned."""

    model_config = ConfigDict(extra="forbid")

    dependencies: list[ProposalDependency]
    planner_notes: list[str]


@dataclass(frozen=True)
class TicketBatch:
    """One stage-2 call's result, paired with the epic identity it was
    seeded with (``new_epic:<n>`` for a new epic, or an echoed key)."""

    epic_index: str
    output: StageTicketsOutput


# --- typed failures ----------------------------------------------------------


class StageAssemblyError(ValueError):
    """A stage output violated the generation protocol during assembly. The
    model referenced an identity the environment did not assign — a typed
    error, never a silently-accepted value (gap 1)."""


class BatchCountError(StageAssemblyError):
    """The number of ticket batches does not match the number of epics — the
    one-call-per-epic invariant was broken."""


class EpicRefMismatchError(StageAssemblyError):
    """A stage-2 ticket's ``epic_ref`` (or a batch's seed) is not the
    environment-assigned identity of the epic it belongs to."""


class DependencyReferenceError(StageAssemblyError):
    """A stage-3 endpoint references a ``new:<n>`` outside the assembled
    ticket range, an epic, or a malformed placeholder."""


@dataclass(frozen=True)
class StageRecord:
    """Per-stage provenance — the ATLAS-105 seam (specified §5.3, stored by
    105). 104 returns these; the pipeline composites them but does not yet
    persist them structurally."""

    stage: str
    prompt_version: str
    prompt_hash: str
    raw_output_hash: str


class StagedGenerationError(RuntimeError):
    """A staged generation run failed after some raw output existed. Carries
    the stage label, the per-stage records gathered so far, and the relevant
    raw output, so the pipeline records an auditable failed PlanRun."""

    def __init__(
        self,
        *,
        stage: str,
        records: Sequence[StageRecord],
        raw_output: str,
        message: str,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.records = tuple(records)
        self.raw_output = raw_output


class StageTruncatedError(StagedGenerationError):
    """A stage hit the model's output token limit (§5.4) — fails honestly,
    naming the stage, rather than misparsing a cut-off response."""

    def __init__(
        self,
        *,
        stage: str,
        records: Sequence[StageRecord],
        raw_output: str,
        max_tokens: int,
    ) -> None:
        super().__init__(
            stage=stage,
            records=records,
            raw_output=raw_output,
            message=(
                f"staged generation truncated at the token limit in stage "
                f"{stage!r} (max_tokens={max_tokens})"
            ),
        )
        self.max_tokens = max_tokens


class StageOutputError(StagedGenerationError):
    """A stage's raw output was not valid JSON / not a valid projection, or
    the assembled references were inconsistent — the model broke the
    generation protocol."""


class StageProjectionError(StageOutputError):
    """A stage emitted valid JSON that violated a §3.11 projection FIELD BOUND
    — a pydantic ``ValidationError`` (the measured case: a ticket with 8
    ``acceptance_criteria`` against the ≤7 cap). Distinct from a
    ``JSONDecodeError`` (structurally broken output) by TYPE, not by message,
    so the generate() loop can retry this and only this (ATLAS-109, gap 1):
    the output is sound and the model merely grazed a bound, so a directed
    re-call telling it exactly what it violated reliably fixes it. It carries
    the underlying ``ValidationError`` so the retry can derive the correction.
    As a ``StageOutputError`` subclass it still records honestly as a failed
    PlanRun if every retry is exhausted — no pipeline change, no hidden retry."""

    def __init__(
        self,
        *,
        stage: str,
        records: Sequence[StageRecord],
        raw_output: str,
        message: str,
        validation_error: ValidationError,
    ) -> None:
        super().__init__(
            stage=stage, records=records, raw_output=raw_output, message=message
        )
        self.validation_error = validation_error


def _correction_message(error: StageProjectionError) -> str:
    """A directed correction (D2) derived from the rejected attempt's
    ``ValidationError``: which ticket, which field, the actual count, the
    bound — in a form the model can act on. The raw output parsed as JSON (the
    failure was validation, not decode), so re-parsing it to name the offending
    ticket by title is safe; if a lookup misses we fall back to the loc path.
    The closing clause forecloses the wrong fix (padding or dropping)."""
    try:
        payload = json.loads(extract_json_object(error.raw_output))
    except json.JSONDecodeError:
        payload = None

    lines: list[str] = []
    for detail in error.validation_error.errors():
        loc = detail["loc"]
        location = ".".join(str(part) for part in loc)
        item = _describe_item(payload, loc)
        lines.append(f"- {location}{item}: {detail['msg']}.")

    return (
        "Your previous attempt was REJECTED for violating the §3.11 proposal "
        "field bounds. Fix EXACTLY these and re-emit the FULL corrected JSON "
        "for this epic (return the whole object — do not just send the changed "
        "ticket):\n"
        + "\n".join(lines)
        + "\nFor an over-long acceptance_criteria list, SPLIT the ticket into "
        "smaller tickets or TRIM to the most essential criteria so each ticket "
        "has at most 7 — do NOT pad short lists and do NOT drop required "
        "tickets to dodge the bound."
    )


def _describe_item(payload: object, loc: Sequence[object]) -> str:
    """Best-effort human label for the offending item, e.g. the ticket title,
    so the correction names what the model wrote rather than only a path."""
    if (
        isinstance(payload, Mapping)
        and len(loc) >= 2
        and loc[0] == "tickets"
        and isinstance(loc[1], int)
    ):
        tickets = payload.get("tickets")
        if isinstance(tickets, Sequence) and 0 <= loc[1] < len(tickets):
            ticket = tickets[loc[1]]
            if isinstance(ticket, Mapping):
                title = ticket.get("title")
                if isinstance(title, str):
                    return f' (ticket "{title}")'
    return ""


# --- pure assembly (gap 1) ---------------------------------------------------


def assemble_proposal(
    epics_output: StageEpicsOutput,
    ticket_batches: Sequence[TicketBatch],
    dependencies_output: StageDependenciesOutput,
) -> Proposal:
    """Assemble the three stage projections into one §3.11 proposal.

    Pure and deterministic: identical stage outputs yield a byte-identical
    proposal. The environment assigns every index — ``new_epic:<n>`` by
    emission order, ``new:<n>`` by assembled position — and every model
    reference is bound-checked against those ranges (gap 1). This is the
    executable form of the §4.1 worked example.
    """
    epics = list(epics_output.epics)
    if len(ticket_batches) != len(epics):
        raise BatchCountError(
            f"{len(ticket_batches)} ticket batch(es) for {len(epics)} epic(s); "
            "staged generation makes exactly one call per epic"
        )

    tickets: list[ProposalTicket] = []
    for index, (epic, batch) in enumerate(zip(epics, ticket_batches, strict=True)):
        identity = epic.key if epic.key is not None else f"new_epic:{index}"
        if batch.epic_index != identity:
            raise EpicRefMismatchError(
                f"the ticket batch at epic position {index} was seeded with "
                f"{batch.epic_index!r}, but that epic's assembled identity is "
                f"{identity!r}"
            )
        for ticket in batch.output.tickets:
            if ticket.epic_ref != identity:
                raise EpicRefMismatchError(
                    f"ticket {ticket.title!r} carries epic_ref "
                    f"{ticket.epic_ref!r}, not the seeded epic identity "
                    f"{identity!r}; the model never re-targets its epic"
                )
            tickets.append(ticket)

    ticket_count = len(tickets)
    for dependency in dependencies_output.dependencies:
        for field_name, value in (
            ("source", dependency.source),
            ("target", dependency.target),
        ):
            if value.startswith("new_epic:"):
                raise DependencyReferenceError(
                    f"dependency {field_name} {value!r} references an epic; "
                    "dependencies are ticket-to-ticket (data-model §3.11)"
                )
            if value.startswith("new:"):
                match = _NEW_TICKET_RE.match(value)
                if match is None:
                    raise DependencyReferenceError(
                        f"dependency {field_name} {value!r} is a malformed "
                        "new:<n> reference"
                    )
                if int(match.group(1)) >= ticket_count:
                    raise DependencyReferenceError(
                        f"dependency {field_name} {value!r} is out of range; "
                        f"the assembled proposal has {ticket_count} ticket(s)"
                    )

    planner_notes = [
        *epics_output.planner_notes,
        *(note for batch in ticket_batches for note in batch.output.planner_notes),
        *dependencies_output.planner_notes,
    ]
    return Proposal(
        epics=epics,
        tickets=tickets,
        dependencies=list(dependencies_output.dependencies),
        planner_notes=planner_notes,
    )


# --- composite provenance over the per-stage records (§5.3) ------------------


def composite_prompt_version(records: Sequence[StageRecord]) -> str:
    """A legible top-level prompt_version naming every stage template, each
    once in stage order (the per-epic ticket stage repeats one version)."""
    seen: list[str] = []
    for record in records:
        if record.prompt_version not in seen:
            seen.append(record.prompt_version)
    return "staged[" + "+".join(seen) + "]"


def composite_prompt_hash(records: Sequence[StageRecord]) -> str:
    """A single digest over what every stage asked — the staged analogue of
    the single-call prompt_hash, keeping the provenance chain intact."""
    joined = "\n".join(f"{r.prompt_version}:{r.prompt_hash}" for r in records)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StagedGenerationResult:
    """The assembled proposal JSON plus per-stage provenance (the ATLAS-105
    seam). ``assembled_json`` is what the pipeline hashes (raw_output_hash,
    §5.3) and parses — identical to how it treats single-call raw output."""

    assembled_json: str
    stage_records: tuple[StageRecord, ...]

    @property
    def prompt_version(self) -> str:
        return composite_prompt_version(self.stage_records)

    @property
    def prompt_hash(self) -> str:
        return composite_prompt_hash(self.stage_records)


@dataclass(frozen=True)
class StageContext:
    """Everything the staged generator needs, built by the pipeline. The
    current-backlog seeding payload is deliberately absent: the ATLAS-103
    templates declare no backlog variable, so the staged path is first-run
    only — the pipeline refuses a staged re-plan rather than seed badly."""

    product_key: str
    documents: Sequence[Mapping[str, str]]
    prompts_dir: Path | None = None


@runtime_checkable
class StagedProposalGenerator(Protocol):
    """The staged-generation seam — injectable exactly like PlannerClient, so
    tests use a fake returning canned outputs (zero live calls in CI)."""

    def generate(
        self, *, client: PlannerClient, context: StageContext
    ) -> StagedGenerationResult: ...


_StageModelT = TypeVar("_StageModelT", bound=BaseModel)


def _projection_schema(model: type[BaseModel]) -> str:
    return json.dumps(model.model_json_schema(), indent=2)


class TemplateStagedGenerator:
    """Concrete staged generator: render each ATLAS-103 template, call the
    model, parse the projection, assemble. Truncation in any stage fails
    honestly naming the stage (§5.4)."""

    def generate(
        self, *, client: PlannerClient, context: StageContext
    ) -> StagedGenerationResult:
        records: list[StageRecord] = []

        # Stage 1: epics.
        epics_prompt = render_planner_prompt(
            {
                "product_key": context.product_key,
                "documents": context.documents,
                "stage_output_schema": _projection_schema(StageEpicsOutput),
            },
            version=STAGE_EPICS_VERSION,
            prompts_dir=context.prompts_dir,
        )
        epics_raw = self._call(client, epics_prompt, "epics", records)
        epics_output = self._parse(StageEpicsOutput, epics_raw, "epics", records)

        assembled_epics = [
            {
                "index": epic.key if epic.key is not None else f"new_epic:{index}",
                "title": epic.title,
            }
            for index, epic in enumerate(epics_output.epics)
        ]

        # Stage 2: tickets, one call per epic (ATLAS-106 owns batch sizing).
        # A projection-bound graze here (the measured ≤7 acceptance_criteria
        # case) is re-rolled with a directed correction up to MAX_STAGE_ATTEMPTS
        # (ATLAS-109); truncation and non-JSON still fail honestly, no retry.
        ticket_batches: list[TicketBatch] = []
        for index, epic in enumerate(epics_output.epics):
            identity = epic.key if epic.key is not None else f"new_epic:{index}"
            stage = f"tickets:{identity}"

            def _render_tickets(
                correction: str | None,
                epic: ProposalEpic = epic,
                identity: str = identity,
            ) -> RenderedPrompt:
                return render_planner_prompt(
                    {
                        "product_key": context.product_key,
                        "documents": context.documents,
                        "epic": {
                            "title": epic.title,
                            "objective": epic.objective,
                            "description": epic.description,
                        },
                        "epic_index": identity,
                        "assembled_epics": assembled_epics,
                        "stage_output_schema": _projection_schema(StageTicketsOutput),
                        "correction": correction,
                    },
                    version=STAGE_TICKETS_VERSION,
                    prompts_dir=context.prompts_dir,
                )

            tickets_output = self._call_stage_with_retry(
                client, StageTicketsOutput, stage, records, _render_tickets
            )
            ticket_batches.append(
                TicketBatch(epic_index=identity, output=tickets_output)
            )

        # Stage 3: dependencies, seeded with the assembled new:<n> indices.
        position = 0
        assembled_tickets = []
        for batch in ticket_batches:
            for ticket in batch.output.tickets:
                assembled_tickets.append(
                    {
                        "index": f"new:{position}",
                        "title": ticket.title,
                        "epic_ref": batch.epic_index,
                    }
                )
                position += 1
        dependencies_prompt = render_planner_prompt(
            {
                "product_key": context.product_key,
                "documents": context.documents,
                "assembled_tickets": assembled_tickets,
                "stage_output_schema": _projection_schema(StageDependenciesOutput),
            },
            version=STAGE_DEPENDENCIES_VERSION,
            prompts_dir=context.prompts_dir,
        )
        dependencies_raw = self._call(
            client, dependencies_prompt, "dependencies", records
        )
        dependencies_output = self._parse(
            StageDependenciesOutput, dependencies_raw, "dependencies", records
        )

        # Assemble — pure; a reference-integrity violation names the stage.
        try:
            proposal = assemble_proposal(
                epics_output, ticket_batches, dependencies_output
            )
        except StageAssemblyError as error:
            combined = json.dumps(
                {
                    "epics": epics_raw,
                    "tickets": [
                        b.output.model_dump(mode="json") for b in ticket_batches
                    ],
                    "dependencies": dependencies_raw,
                }
            )
            raise StageOutputError(
                stage="assembly",
                records=records,
                raw_output=combined,
                message=str(error),
            ) from error

        assembled_json = json.dumps(
            proposal.model_dump(mode="json"), ensure_ascii=False
        )
        return StagedGenerationResult(
            assembled_json=assembled_json, stage_records=tuple(records)
        )

    def _call_stage_with_retry(
        self,
        client: PlannerClient,
        model: type[_StageModelT],
        stage: str,
        records: list[StageRecord],
        render: Callable[[str | None], RenderedPrompt],
    ) -> _StageModelT:
        """Render → call → parse a stage, retrying a projection-bound graze
        with a directed correction up to ``MAX_STAGE_ATTEMPTS`` total attempts
        (ATLAS-109). The retry trigger is purely structural: only a
        ``StageProjectionError`` (a §3.11 field-bound ValidationError) is
        caught here; a base ``StageOutputError`` (non-JSON) and a
        ``StageTruncatedError`` (raised by ``_call``, upstream of the parse)
        both propagate immediately — no retry. Each attempt is a real model
        call, so each appends its own ``StageRecord`` via ``_call``; retries
        carry a ``(retry N)`` stage label so the provenance is self-describing
        (gap 3). After the final still-failing attempt, the last typed
        ``StageProjectionError`` is re-raised — failing honestly, naming the
        stage and the violation, the cap enforced not relaxed."""
        last_error: StageProjectionError | None = None
        for attempt in range(MAX_STAGE_ATTEMPTS):
            label = stage if attempt == 0 else f"{stage} (retry {attempt})"
            correction = None if last_error is None else _correction_message(last_error)
            prompt = render(correction)
            raw = self._call(client, prompt, label, records)
            try:
                return self._parse(model, raw, label, records)
            except StageProjectionError as error:
                last_error = error
        assert last_error is not None  # MAX_STAGE_ATTEMPTS >= 1
        raise last_error

    @staticmethod
    def _call(
        client: PlannerClient,
        prompt: RenderedPrompt,
        stage: str,
        records: list[StageRecord],
    ) -> str:
        try:
            raw = client.generate(prompt.text)
        except TruncatedOutputError as error:
            records.append(
                StageRecord(
                    stage=stage,
                    prompt_version=prompt.prompt_version,
                    prompt_hash=prompt.prompt_hash,
                    raw_output_hash=hashlib.sha256(
                        error.raw_output.encode("utf-8")
                    ).hexdigest(),
                )
            )
            raise StageTruncatedError(
                stage=stage,
                records=records,
                raw_output=error.raw_output,
                max_tokens=error.max_tokens,
            ) from error
        records.append(
            StageRecord(
                stage=stage,
                prompt_version=prompt.prompt_version,
                prompt_hash=prompt.prompt_hash,
                raw_output_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            )
        )
        return raw

    @staticmethod
    def _parse(
        model: type[_StageModelT],
        raw: str,
        stage: str,
        records: list[StageRecord],
    ) -> _StageModelT:
        try:
            # Fence/prose-tolerant extraction (ATLAS-108); raw_output stays the
            # unstripped bytes (already hashed in _call), so provenance is
            # unaffected. Genuine non-JSON still fails as StageOutputError.
            payload = json.loads(extract_json_object(raw))
        except json.JSONDecodeError as error:
            raise StageOutputError(
                stage=stage,
                records=records,
                raw_output=raw,
                message=f"stage {stage!r} output is not valid JSON: {error}",
            ) from error
        try:
            return model.model_validate(payload)
        except ValidationError as error:
            # A §3.11 field-bound violation on otherwise-valid JSON: the
            # retry-eligible class (ATLAS-109, gap 1). Raised as the distinct
            # StageProjectionError so generate() retries this and only this; a
            # JSONDecodeError above stays a base StageOutputError and propagates.
            raise StageProjectionError(
                stage=stage,
                records=records,
                raw_output=raw,
                message=(
                    f"stage {stage!r} output is not a valid {model.__name__} "
                    f"projection: {error}"
                ),
                validation_error=error,
            ) from error

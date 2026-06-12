"""Hypothesis strategies for the canonical models (ATLAS-19).

Generated instances respect the documented contracts (confidence
bounds, aware datetimes). The exclusions below are the documented
boundary of the round-trip claim, stated in the ATLAS-19 completion
report: no NaN (breaks identity by definition), no infinities (JSON
forbids them), no NUL in text (PostgreSQL TEXT forbids it), no
surrogates (not valid Unicode scalar values), ints bounded to the SQL
INTEGER range, floats bounded to ±1e9, datetimes within 1900-9000 (UTC
conversion overflows near datetime.min/max).
"""

from datetime import UTC, datetime, timedelta, timezone
from typing import Literal

from hypothesis import strategies as st
from pydantic import BaseModel

from atlas.core.enums import ActorType, EntityStatus, EvidenceStatus, RiskLevel
from atlas.core.models import (
    AgentRun,
    AgentRunStatus,
    ArchitectureDecisionRecord,
    ContextPack,
    Epic,
    Evidence,
    Lesson,
    PlanRun,
    Product,
    Ticket,
    TicketDependency,
)
from atlas.core.models.adr import ADRStatus
from atlas.core.models.agent_run import AgentProvider
from atlas.core.models.dependency import DependencyType
from atlas.core.models.epic import EpicStatus
from atlas.core.models.evidence import EvidenceType
from atlas.core.models.lesson import LessonCategory
from atlas.core.models.plan_run import PlanRunStatus
from atlas.core.models.ticket import TicketStatus, TicketType

_SURROGATES: tuple[Literal["Cs"], ...] = ("Cs",)

uuids = st.uuids()
texts = st.text(
    alphabet=st.characters(
        blacklist_categories=_SURROGATES, blacklist_characters="\x00"
    ),
    max_size=60,
)
small_ints = st.integers(min_value=-(2**31), max_value=2**31 - 1)
bounded_floats = st.floats(
    min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False
)
unit_floats = st.floats(min_value=0, max_value=1, allow_nan=False)
aware_datetimes = st.datetimes(
    min_value=datetime(1900, 1, 1),
    max_value=datetime(9000, 1, 1),
    timezones=st.sampled_from(
        [UTC, timezone(timedelta(hours=2)), timezone(timedelta(hours=-7))]
    ),
)
text_lists = st.lists(texts, max_size=4)
uuid_lists = st.lists(uuids, max_size=3)
text_dicts = st.dictionaries(texts, texts, max_size=4)
json_values = st.recursive(
    st.none() | st.booleans() | small_ints | bounded_floats | texts,
    lambda children: (
        st.lists(children, max_size=3) | st.dictionaries(texts, children, max_size=3)
    ),
    max_leaves=8,
)
json_dicts = st.dictionaries(texts, json_values, max_size=4)


def optional(strategy: st.SearchStrategy[object]) -> st.SearchStrategy[object]:
    return st.none() | strategy


# Evidence actor/status pairs are generated jointly: agent-tier evidence
# is PENDING-only because EvidenceRepo.add (ADR-0008) correctly refuses
# anything else — such records are unstorable by design, so the
# round-trip claim excludes them. System/human cover every status.
_evidence_actor_status = st.one_of(
    st.tuples(st.just(ActorType.AGENT), st.just(EvidenceStatus.PENDING)),
    st.tuples(
        st.sampled_from([ActorType.SYSTEM, ActorType.HUMAN]),
        st.sampled_from(EvidenceStatus),
    ),
)


STRATEGIES: dict[type[BaseModel], st.SearchStrategy[BaseModel]] = {
    Product: st.builds(
        Product,
        id=uuids,
        key=texts,
        name=texts,
        description=texts,
        vision=texts,
        status=st.sampled_from(EntityStatus),
        goals=text_lists,
        non_goals=text_lists,
        constraints=text_lists,
        created_by_type=st.sampled_from(ActorType),
        created_by_id=texts,
        created_at=aware_datetimes,
        updated_at=aware_datetimes,
        archived_at=optional(aware_datetimes),
    ),
    ArchitectureDecisionRecord: st.builds(
        ArchitectureDecisionRecord,
        id=uuids,
        product_id=uuids,
        number=small_ints,
        title=texts,
        status=st.sampled_from(ADRStatus),
        context=texts,
        decision=texts,
        rationale=texts,
        consequences=text_lists,
        alternatives_considered=text_lists,
        supersedes_adr_id=optional(uuids),
        created_by_type=st.sampled_from(ActorType),
        created_by_id=texts,
        created_at=aware_datetimes,
        updated_at=aware_datetimes,
    ),
    Epic: st.builds(
        Epic,
        id=uuids,
        product_id=uuids,
        key=texts,
        title=texts,
        description=texts,
        objective=texts,
        status=st.sampled_from(EpicStatus),
        priority=small_ints,
        risk_level=st.sampled_from(RiskLevel),
        created_by_type=st.sampled_from(ActorType),
        created_by_id=texts,
        created_at=aware_datetimes,
        updated_at=aware_datetimes,
        completed_at=optional(aware_datetimes),
    ),
    Ticket: st.builds(
        Ticket,
        id=uuids,
        product_id=uuids,
        epic_id=optional(uuids),
        key=texts,
        title=texts,
        objective=texts,
        context=texts,
        status=st.sampled_from(TicketStatus),
        ticket_type=st.sampled_from(TicketType),
        risk_level=st.sampled_from(RiskLevel),
        priority=small_ints,
        relevant_docs=text_lists,
        acceptance_criteria=text_lists,
        non_goals=text_lists,
        implementation_notes=text_lists,
        test_requirements=text_lists,
        documentation_requirements=text_lists,
        definition_of_done=text_lists,
        estimated_effort=optional(small_ints),
        external_linear_id=optional(texts),
        external_github_issue_id=optional(texts),
        created_by_type=st.sampled_from(ActorType),
        created_by_id=texts,
        created_at=aware_datetimes,
        updated_at=aware_datetimes,
        completed_at=optional(aware_datetimes),
    ),
    TicketDependency: st.builds(
        TicketDependency,
        id=uuids,
        source_ticket_id=uuids,
        target_entity_type=texts,
        target_entity_id=uuids,
        dependency_type=st.sampled_from(DependencyType),
        reason=texts,
        created_by_type=st.sampled_from(ActorType),
        created_by_id=texts,
        created_at=aware_datetimes,
    ),
    Lesson: st.builds(
        Lesson,
        id=uuids,
        product_id=uuids,
        status=st.sampled_from(EntityStatus),
        category=st.sampled_from(LessonCategory),
        title=texts,
        problem=texts,
        solution=texts,
        outcome=texts,
        confidence=unit_floats,
        related_ticket_ids=uuid_lists,
        related_adr_ids=uuid_lists,
        tags=text_lists,
        created_by_type=st.sampled_from(ActorType),
        created_by_id=texts,
        created_at=aware_datetimes,
        updated_at=aware_datetimes,
    ),
    Evidence: _evidence_actor_status.flatmap(
        lambda pair: st.builds(
            Evidence,
            id=uuids,
            product_id=uuids,
            ticket_id=optional(uuids),
            agent_run_id=optional(uuids),
            evidence_type=st.sampled_from(EvidenceType),
            status=st.just(pair[1]),
            summary=texts,
            commit_sha=optional(texts),
            external_run_id=optional(texts),
            payload_hash=optional(texts),
            source_uri=optional(texts),
            raw_payload=json_dicts,
            created_by_type=st.just(pair[0]),
            created_by_id=texts,
            created_at=aware_datetimes,
        )
    ),
    AgentRun: st.builds(
        AgentRun,
        id=uuids,
        product_id=uuids,
        ticket_id=optional(uuids),
        provider=st.sampled_from(AgentProvider),
        model=optional(texts),
        status=st.sampled_from(AgentRunStatus),
        objective=texts,
        input_context_pack_id=optional(uuids),
        output_summary=optional(texts),
        error_summary=optional(texts),
        cost_estimate_usd=optional(bounded_floats),
        prompt_tokens=optional(small_ints),
        completion_tokens=optional(small_ints),
        started_at=optional(aware_datetimes),
        completed_at=optional(aware_datetimes),
        created_at=aware_datetimes,
    ),
    ContextPack: st.builds(
        ContextPack,
        id=uuids,
        product_id=uuids,
        ticket_id=optional(uuids),
        title=texts,
        objective=texts,
        constraints=text_lists,
        relevant_docs=text_lists,
        relevant_adrs=uuid_lists,
        related_tickets=uuid_lists,
        historical_lessons=uuid_lists,
        acceptance_criteria=text_lists,
        risks=text_lists,
        test_commands=text_lists,
        definition_of_done=text_lists,
        rendered_markdown=texts,
        input_doc_shas=text_dicts,
        token_estimate=optional(small_ints),
        created_at=aware_datetimes,
    ),
    PlanRun: st.builds(
        PlanRun,
        id=uuids,
        product_id=uuids,
        status=st.sampled_from(PlanRunStatus),
        input_doc_shas=text_dicts,
        model_provider=texts,
        model_name=texts,
        prompt_version=texts,
        similarity_threshold=unit_floats,
        raw_output_hash=texts,
        diff_summary=json_dicts,
        failure_reason=optional(texts),
        approved_by=optional(texts),
        created_at=aware_datetimes,
        applied_at=optional(aware_datetimes),
    ),
}

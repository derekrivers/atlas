"""ATLAS-60: context pack validation — the five context-renderer.md spec checks,
returned as data (never raised).

The validator COLLECTS every failure (no short-circuit) and DEGRADES its anchor
check by available input: path-level always, slug-level only when a ticket is
supplied. These tests pin each lever and name the wrong answer: a violation that
passes, a short-circuit that hides later failures, a slug check that reports depth
"slug" without a ticket, and the DRAFT/dangling lesson cases collapsing to one
message.

Negative cases mutate a known-good pack via ``model_copy`` so exactly one field
diverges — isolating each check without rebuilding through ``build_context_pack``
(which the builder's own suite covers).
"""

from __future__ import annotations

from uuid import uuid4

from test_pack import build_full, make_corpus, make_lesson, make_ticket

from atlas.context import ContextPackValidation, validate_context_pack

# --- the all-valid baseline ----------------------------------------------------


def test_all_valid_pack_passes() -> None:
    pack, ticket, _related, _adr, lesson = build_full()
    result = validate_context_pack(
        pack, documents=make_corpus(), lessons=[lesson], ticket=ticket
    )
    assert isinstance(result, ContextPackValidation)
    assert result.valid is True
    assert result.failures == ()
    assert result.anchor_check_depth == "slug"


# --- each check fails independently (no short-circuit) -------------------------


def test_empty_objective_fails_alone() -> None:
    pack, ticket, _related, _adr, lesson = build_full()
    broken = pack.model_copy(update={"objective": "   "})
    result = validate_context_pack(
        broken, documents=make_corpus(), lessons=[lesson], ticket=ticket
    )
    assert result.valid is False
    assert result.failures == ("objective: missing or empty",)


def test_no_acceptance_criteria_fails_alone() -> None:
    pack, ticket, _related, _adr, lesson = build_full()
    broken = pack.model_copy(update={"acceptance_criteria": []})
    result = validate_context_pack(
        broken, documents=make_corpus(), lessons=[lesson], ticket=ticket
    )
    assert result.valid is False
    assert len(result.failures) == 1
    assert "acceptance_criteria" in result.failures[0]


def test_no_test_commands_fails_alone() -> None:
    pack, ticket, _related, _adr, lesson = build_full()
    broken = pack.model_copy(update={"test_commands": []})
    result = validate_context_pack(
        broken, documents=make_corpus(), lessons=[lesson], ticket=ticket
    )
    assert result.valid is False
    assert len(result.failures) == 1
    assert "test_commands" in result.failures[0]


def test_estimate_over_budget_fails_alone() -> None:
    pack, ticket, _related, _adr, lesson = build_full()
    # The pack is in-budget at 12,000; a budget of 1 isolates the D4 failure.
    assert pack.token_estimate is not None and pack.token_estimate > 1
    result = validate_context_pack(
        pack, documents=make_corpus(), lessons=[lesson], ticket=ticket, budget=1
    )
    assert result.valid is False
    assert len(result.failures) == 1
    assert "token_estimate" in result.failures[0]
    assert "exceeds budget 1" in result.failures[0]


def test_relevant_doc_absent_from_index_fails_alone() -> None:
    # The wrong answer: a relevant_docs path with no backing indexed document
    # passing. Here ``ghost.md`` is recorded in input_doc_shas but is not in the
    # corpus the index is built from, so only the path-level anchor check fails.
    pack, ticket, _related, _adr, lesson = build_full()
    broken = pack.model_copy(
        update={
            "relevant_docs": [*pack.relevant_docs, "ghost.md"],
            "input_doc_shas": {**pack.input_doc_shas, "ghost.md": "sha-ghost"},
        }
    )
    result = validate_context_pack(
        broken, documents=make_corpus(), lessons=[lesson], ticket=ticket
    )
    assert result.valid is False
    assert len(result.failures) == 1
    assert "anchor[ghost.md]" in result.failures[0]
    assert "not in the indexed document set" in result.failures[0]


def test_sha_mismatch_fails() -> None:
    # A recorded SHA that disagrees with the document's SHA is a staleness/tamper
    # signal, not a missing path.
    pack, ticket, _related, _adr, lesson = build_full()
    broken = pack.model_copy(
        update={"input_doc_shas": {**pack.input_doc_shas, "main.md": "stale-sha"}}
    )
    result = validate_context_pack(
        broken, documents=make_corpus(), lessons=[lesson], ticket=ticket
    )
    assert result.valid is False
    assert len(result.failures) == 1
    assert "anchor[main.md]" in result.failures[0]
    assert "does not match" in result.failures[0]


# --- slug-level runs only with a ticket (D5 depth) -----------------------------


def test_no_ticket_runs_path_depth_only() -> None:
    # The wrong answer: reporting depth "slug" (or running a slug check) with no
    # ticket. Path-level still passes, so the pack is valid at path depth.
    pack, _ticket, _related, _adr, lesson = build_full()
    result = validate_context_pack(
        pack, documents=make_corpus(), lessons=[lesson], ticket=None
    )
    assert result.anchor_check_depth == "path"
    assert result.valid is True


def test_resolving_ticket_runs_slug_depth_and_passes() -> None:
    pack, ticket, _related, _adr, lesson = build_full()
    result = validate_context_pack(
        pack, documents=make_corpus(), lessons=[lesson], ticket=ticket
    )
    assert result.anchor_check_depth == "slug"
    assert result.valid is True


def test_unresolvable_source_anchor_is_slug_depth_typed_failure() -> None:
    # The wrong answer: silently skipping the slug check while reporting depth
    # "slug". A source_anchor whose slug matches no heading must surface the typed
    # reason (UnknownAnchorError).
    pack, _ticket, _related, _adr, lesson = build_full()
    bad_ticket = make_ticket(source_anchor="main.md#no-such-heading")
    result = validate_context_pack(
        pack, documents=make_corpus(), lessons=[lesson], ticket=bad_ticket
    )
    assert result.anchor_check_depth == "slug"
    assert result.valid is False
    assert len(result.failures) == 1
    assert "anchor[main.md#no-such-heading]" in result.failures[0]
    assert "UnknownAnchorError" in result.failures[0]


# --- DRAFT vs dangling lessons (D6) — distinct, not collapsed ------------------


def test_draft_lesson_fails_naming_status() -> None:
    pack, ticket, _related, _adr, _lesson = build_full()
    draft = make_lesson(status="draft")
    broken = pack.model_copy(update={"historical_lessons": [draft.id]})
    result = validate_context_pack(
        broken, documents=make_corpus(), lessons=[draft], ticket=ticket
    )
    assert result.valid is False
    assert len(result.failures) == 1
    assert f"lesson[{draft.id}]" in result.failures[0]
    assert "'draft'" in result.failures[0]


def test_dangling_lesson_fails_with_distinct_not_found_message() -> None:
    pack, ticket, _related, _adr, _lesson = build_full()
    missing_id = uuid4()
    broken = pack.model_copy(update={"historical_lessons": [missing_id]})
    result = validate_context_pack(
        broken, documents=make_corpus(), lessons=[], ticket=ticket
    )
    assert result.valid is False
    assert len(result.failures) == 1
    assert f"lesson[{missing_id}]" in result.failures[0]
    assert "dangling" in result.failures[0]


def test_draft_and_dangling_do_not_collapse() -> None:
    # The wrong answer: the two lesson failure modes producing one shared message.
    pack, ticket, _related, _adr, _lesson = build_full()
    draft = make_lesson(status="draft")
    missing_id = uuid4()
    broken = pack.model_copy(update={"historical_lessons": [draft.id, missing_id]})
    result = validate_context_pack(
        broken, documents=make_corpus(), lessons=[draft], ticket=ticket
    )
    assert result.valid is False
    assert len(result.failures) == 2
    draft_failure = next(f for f in result.failures if str(draft.id) in f)
    dangling_failure = next(f for f in result.failures if str(missing_id) in f)
    assert "'draft'" in draft_failure
    assert "dangling" in dangling_failure


# --- all failures collected (no short-circuit) ---------------------------------


def test_all_failures_collected() -> None:
    # The wrong answer: reporting only the first failure. Three checks violated at
    # once must yield three entries.
    pack, ticket, _related, _adr, lesson = build_full()
    broken = pack.model_copy(
        update={"objective": "", "acceptance_criteria": [], "test_commands": []}
    )
    result = validate_context_pack(
        broken, documents=make_corpus(), lessons=[lesson], ticket=ticket
    )
    assert result.valid is False
    assert len(result.failures) == 3

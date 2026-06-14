"""ATLAS-19: property-based round-trips per knowledge-core "Testing
strategy" — model -> YAML -> model and model -> DB -> model are
identity for every entity, render determinism holds across generated
backlogs, and the parked ATLAS-17 edge (implicitly-resolvable string
content) is pinned. Exclusion boundary documented in
tests/model_strategies.py."""

from typing import Any, cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from model_strategies import STRATEGIES, texts
from pydantic import BaseModel
from test_storage_roundtrip import CASES as REPO_CASES

from atlas.core.models import Product, Ticket
from atlas.core.yaml_io import (
    RenderHeader,
    dump_entity,
    load_entity,
    render_document,
)
from atlas.storage import Database
from atlas.storage.repositories import _Repo

MODEL_IDS = [model.__name__ for model in STRATEGIES]
HEADER = RenderHeader(
    plan_run_id="b3a9f1e2-7c4d-4a8b-9e6f-1d2c3b4a5e60",
    prompt_version="planner-v1.0.0",
    ticket_key_high_water=42,
    epic_key_high_water=7,
)
REPO_BY_MODEL: dict[type[BaseModel], Any] = {
    cast(type[BaseModel], model): repo for repo, model, _ in REPO_CASES
}


def fresh_repo(model_cls: type[BaseModel]) -> _Repo[Any]:
    db = Database("sqlite:///:memory:")
    db.create_all()
    return cast("_Repo[Any]", REPO_BY_MODEL[model_cls](db))


def test_hypothesis_profile_is_derandomised() -> None:
    # The determinism policy: CI cannot be flaky by construction.
    assert settings().derandomize is True


@pytest.mark.parametrize("model_cls", STRATEGIES, ids=MODEL_IDS)
@given(data=st.data())
def test_yaml_round_trip_is_identity(
    model_cls: type[BaseModel], data: st.DataObject
) -> None:
    original = data.draw(STRATEGIES[model_cls], label=model_cls.__name__)
    assert load_entity(model_cls, dump_entity(original)) == original


@pytest.mark.parametrize("model_cls", STRATEGIES, ids=MODEL_IDS)
@settings(max_examples=20)
@given(data=st.data())
def test_db_round_trip_is_identity(
    model_cls: type[BaseModel], data: st.DataObject
) -> None:
    original = data.draw(STRATEGIES[model_cls], label=model_cls.__name__)
    repo = fresh_repo(model_cls)
    repo.add(original)
    assert repo.get(original.id) == original  # type: ignore[attr-defined]


@settings(max_examples=25)
@given(tickets=st.lists(STRATEGIES[Ticket], max_size=4))
def test_render_same_backlog_twice_is_byte_identical(tickets: list[Ticket]) -> None:
    first = render_document("tickets", tickets, HEADER)
    second = render_document("tickets", tickets, HEADER)
    assert first == second


# --- the parked ATLAS-17 edge (gap 3): strings whose content YAML would
# implicitly resolve as another type must survive the round trip. The
# emitter consults its resolver and quotes them; pinned here.

ADVERSARIAL_EXAMPLES = [
    "2026-06-12T10:00:00Z",  # ISO datetime (the originally parked case)
    "2026-06-12 10:00:00",  # space-separated timestamp
    "2026-06-12",  # date
    "true",
    "True",
    "false",
    "yes",
    "no",
    "on",
    "off",
    "null",
    "Null",
    "~",
    "",
    "007",  # octal-looking
    "0x1F",  # hex
    "1e5",  # float exponent
    "-1.5",
    "+12",
    ".inf",
    ".nan",
    "12:34:56",  # YAML 1.1 sexagesimal
    "1_000",  # YAML 1.1 underscored int
    "- item",  # block sequence lead-in
    "[a, b]",  # flow sequence
    "{a: b}",  # flow mapping
    "# comment",
    "*alias",
    "&anchor",
    "!tag",
    "|",
    ">",
    "%directive",
    "@reserved",
    "`reserved",
    "key: value",
    " leading-space",
    "trailing-space ",
    "line\nbreak",
    "\ttab",
    "\x18",  # C0 control
    "\x85",  # NEL — YAML break char, normalised to \n if emitted raw
    "\u2028",  # LS
    "\u2029",  # PS
    "line\nbreak\x85mixed",  # break char inside a literal-block candidate
]


def _product_with_string_content(content: str) -> Product:
    return Product(
        id="7f3e9b2a-5c1d-4e8f-a6b4-9d2c8e7f1a30",  # type: ignore[arg-type]
        key=content,
        name=content,
        description=content,
        vision=content,
        status="active",  # type: ignore[arg-type]
        created_by_type="human",  # type: ignore[arg-type]
        created_by_id="operator",
        created_at="2026-06-12T10:00:00Z",  # type: ignore[arg-type]
        updated_at="2026-06-12T10:00:00Z",  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("content", ADVERSARIAL_EXAMPLES, ids=repr)
def test_named_adversarial_content_round_trips(content: str) -> None:
    product = _product_with_string_content(content)
    assert load_entity(Product, dump_entity(product)) == product


@settings(max_examples=100)
@given(content=texts)
def test_random_string_content_round_trips(content: str) -> None:
    product = _product_with_string_content(content)
    assert load_entity(Product, dump_entity(product)) == product

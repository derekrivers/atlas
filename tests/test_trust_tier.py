"""ATLAS-11: trust-tier derivation per ADR-0008 and knowledge-core.md.

Covers the mapping, the typed error on invalid input, the absence of a
bypass parameter, and the single-module rule ("a single function ... is
the only place tier logic lives").
"""

import ast
import inspect
from pathlib import Path

import pytest

import atlas
from atlas.core import ActorType, InvalidActorTypeError, evidence_tier

TIER_MODULE = Path(atlas.__file__).parent / "core" / "trust.py"


@pytest.mark.parametrize(
    ("actor", "tier"),
    [
        (ActorType.SYSTEM, "system"),
        (ActorType.HUMAN, "human"),
        (ActorType.AGENT, "agent"),
    ],
)
def test_tier_from_actor_type(actor: ActorType, tier: str) -> None:
    assert evidence_tier(actor) == tier


@pytest.mark.parametrize(
    ("stored", "tier"),
    [
        ("system", "system"),
        ("human", "human"),
        ("agent", "agent"),
    ],
)
def test_tier_from_stored_string(stored: str, tier: str) -> None:
    # created_by_type is TEXT in the database (data-model §1.5); the raw
    # stored form must derive the same tier.
    assert evidence_tier(stored) == tier


@pytest.mark.parametrize("invalid", ["robot", "", "Human", "SYSTEM ", 3, None])
def test_invalid_input_raises_typed_error(invalid: object) -> None:
    with pytest.raises(InvalidActorTypeError):
        evidence_tier(invalid)  # type: ignore[arg-type]


def test_error_is_a_distinct_typed_class() -> None:
    assert issubclass(InvalidActorTypeError, ValueError)
    assert InvalidActorTypeError is not ValueError


def test_no_bypass_parameter() -> None:
    # ADR-0008 / knowledge-core.md: "There is no bypass parameter."
    parameters = inspect.signature(evidence_tier).parameters
    assert list(parameters) == ["created_by_type"]


def _tier_definitions(module_path: Path) -> list[str]:
    """Names of functions or module-level bindings carrying tier logic."""
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if "tier" in node.name.lower():
                found.append(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and "tier" in target.id.lower():
                    found.append(target.id)
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and "tier" in target.id.lower():
                found.append(target.id)
    return found


def test_tier_logic_lives_in_exactly_one_module() -> None:
    # knowledge-core.md: evidence_tier is "the only place tier logic
    # lives". Any module other than atlas/core/trust.py defining a
    # tier-named function or binding is a competing implementation.
    package_root = Path(atlas.__file__).parent
    offenders = {
        str(path.relative_to(package_root.parent)): names
        for path in sorted(package_root.rglob("*.py"))
        if path != TIER_MODULE and (names := _tier_definitions(path))
    }
    assert not offenders, f"tier logic outside {TIER_MODULE.name}: {offenders}"
    assert "evidence_tier" in _tier_definitions(TIER_MODULE)

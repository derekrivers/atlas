"""ATLAS-12: shared enums have exactly one definition (atlas/core/enums.py).

The models consume ATLAS-11's ActorType, EntityStatus, RiskLevel, and
EvidenceStatus; a re-declaration anywhere in the package fails here.
"""

import ast
from pathlib import Path

import atlas
import atlas.core.enums
from atlas.core.models import Epic, Product, Ticket

SHARED_ENUM_NAMES = {"ActorType", "EntityStatus", "RiskLevel", "EvidenceStatus"}
CANONICAL_MODULE = Path(atlas.core.enums.__file__)


def test_shared_enums_defined_in_exactly_one_module() -> None:
    package_root = Path(atlas.__file__).parent
    offenders = {}
    for path in sorted(package_root.rglob("*.py")):
        if path == CANONICAL_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        duplicated = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name in SHARED_ENUM_NAMES
        ]
        if duplicated:
            offenders[str(path.relative_to(package_root.parent))] = duplicated
    assert not offenders, f"shared enums re-declared outside enums.py: {offenders}"


def test_models_reference_canonical_shared_enums() -> None:
    # Identity, not equality: the annotation objects are the classes from
    # atlas.core.enums itself.
    assert Product.model_fields["status"].annotation is atlas.core.enums.EntityStatus
    assert Ticket.model_fields["risk_level"].annotation is atlas.core.enums.RiskLevel
    assert Epic.model_fields["created_by_type"].annotation is (
        atlas.core.enums.ActorType
    )

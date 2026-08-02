"""Architecture ownership sensors for delivery admission policy."""

from __future__ import annotations

import ast
from pathlib import Path
from textwrap import dedent

REPO_ROOT = Path(__file__).resolve().parent.parent
POLICY_MODULES = (
    REPO_ROOT / "atlas/core/models/delivery_admission_policy.py",
    REPO_ROOT / "atlas/orchestration/delivery_admission_policy.py",
)


def _symphony_boundary_violations(source: str) -> list[int]:
    violations: list[int] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any("symphony" in alias.name.casefold() for alias in node.names):
                violations.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None and "symphony" in node.module.casefold():
                violations.append(node.lineno)
        elif isinstance(node, ast.Call):
            function = ast.unparse(node.func).casefold()
            if function.startswith("symphony."):
                violations.append(node.lineno)
    return violations


def test_policy_code_cannot_import_or_invoke_symphony() -> None:
    violations: list[str] = []
    for path in POLICY_MODULES:
        for line in _symphony_boundary_violations(path.read_text(encoding="utf-8")):
            violations.append(f"{path}:{line}")

    assert violations == []


def test_symphony_boundary_sensor_fires_on_seeded_defect() -> None:
    # Seeded red first with `assert 1 == 2` (B011); this fixture retains that
    # required defect form while proving the architecture sensor detects the
    # forbidden Symphony dependency rather than relying on a textual search.
    source = dedent(
        """
        import symphony

        assert 1 == 2  # type: ignore[comparison-overlap]
        symphony.cancel_agent("run-1")
        """
    )

    assert _symphony_boundary_violations(source) == [2, 5]

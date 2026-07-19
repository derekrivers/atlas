"""ATLAS-114: the import-linter layer-spine contract is a live sensor.

Three falsifiable guards for the architecture-fitness contract declared in
pyproject [tool.importlinter]:

1. The contract is KEPT on the current tree (it must pass, not be vacuous).
2. The contract demonstrably FIRES: seeding the forbidden
   ``dependencies -> planning`` edge (the inversion ATLAS-113 removed) makes
   ``lint-imports`` exit non-zero and name the offending import.
3. The CLI stays below its operator-approved size ceiling, preventing reusable
   logic from silently re-accreting in the presentation layer.

Guard 2 is the point of the ticket — a contract that never fails proves
nothing. It writes a throwaway module under atlas/dependencies/ and removes
it in a finally block so the tree is left untouched even on failure.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# The console script lives next to the interpreter running the tests (the
# project venv), so we find it without depending on PATH.
LINT_IMPORTS = Path(sys.executable).parent / "lint-imports"
SEEDED_MODULE = REPO_ROOT / "atlas" / "dependencies" / "_seeded_violation.py"
CLI_PATH = REPO_ROOT / "atlas" / "cli.py"
CLI_LINE_CEILING = 2650


def _run_lint_imports() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(LINT_IMPORTS)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def test_layer_spine_contract_is_kept_on_current_tree() -> None:
    result = _run_lint_imports()
    assert result.returncode == 0, (
        f"lint-imports failed on the current tree:\n{result.stdout}\n{result.stderr}"
    )
    assert "Atlas layer spine KEPT" in result.stdout


def test_contract_fires_on_dependencies_to_planning_edge() -> None:
    # Seed the exact inversion the spine forbids: a dependencies-layer module
    # importing the higher planning layer.
    SEEDED_MODULE.write_text(
        '"""Throwaway module: seeds a forbidden dependencies -> planning '
        'import to prove the layer-spine contract fires (ATLAS-114)."""\n'
        "from atlas import planning as _planning  # noqa: F401\n"
    )
    try:
        result = _run_lint_imports()
    finally:
        SEEDED_MODULE.unlink(missing_ok=True)

    assert result.returncode != 0, (
        "lint-imports should fail when dependencies imports planning, but it "
        f"exited 0:\n{result.stdout}"
    )
    assert "Atlas layer spine BROKEN" in result.stdout
    # The report must name the forbidden import, not just fail opaquely.
    assert "atlas.dependencies._seeded_violation" in result.stdout
    assert "atlas.planning" in result.stdout


def test_cli_remains_a_thin_presentation_layer() -> None:
    line_count = len(CLI_PATH.read_text(encoding="utf-8").splitlines())
    assert line_count <= CLI_LINE_CEILING, (
        f"atlas/cli.py has {line_count} lines; ceiling is {CLI_LINE_CEILING}. "
        "Reusable logic belongs in services or atlas.orchestration, not the CLI. "
        "A legitimate ceiling increase requires operator sign-off, not a "
        "reflexive bump."
    )

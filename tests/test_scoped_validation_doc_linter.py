"""ATLAS-257 negative fixtures for the scoped-validation handoff linter."""

from pathlib import Path

from test_doc_linter import write

from atlas.tools.doc_linter import (
    AGENT_CONTRACT_PATHS,
    Finding,
    check_scoped_validation_handoff_contract,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _codes(findings: list[Finding]) -> set[str]:
    return {finding.code for finding in findings}


def _build_contract_fixture(root: Path) -> None:
    for rel in AGENT_CONTRACT_PATHS:
        write(root, rel, (REPO_ROOT / rel).read_text(encoding="utf-8"))


def test_atlas_257_live_contract_passes_scoped_validation_handoff_lint() -> None:
    assert check_scoped_validation_handoff_contract(REPO_ROOT) == []


def test_atlas_257_doc_linter_rejects_agent_ci_polling_instruction(
    tmp_path: Path,
) -> None:
    _build_contract_fixture(tmp_path)
    workflow = (tmp_path / "WORKFLOW.md").read_text(encoding="utf-8")
    write(
        tmp_path,
        "WORKFLOW.md",
        workflow + "\nAgents must poll CI until the required checks pass.\n",
    )

    findings = check_scoped_validation_handoff_contract(tmp_path)

    assert "HND001" in _codes(findings)
    assert any(finding.path == "WORKFLOW.md" for finding in findings)


def test_atlas_257_doc_linter_rejects_scoped_repository_authority_claim(
    tmp_path: Path,
) -> None:
    _build_contract_fixture(tmp_path)
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    write(
        tmp_path,
        "AGENTS.md",
        agents + "\nScoped local checks prove repository-wide completion.\n",
    )

    findings = check_scoped_validation_handoff_contract(tmp_path)

    assert "HND002" in _codes(findings)
    assert any(finding.path == "AGENTS.md" for finding in findings)


def test_atlas_257_doc_linter_allows_explicit_prohibitions(tmp_path: Path) -> None:
    write(
        tmp_path,
        "WORKFLOW.md",
        "Do not poll CI or wait for review.\n"
        "Scoped local checks never prove repository-wide completion.\n",
    )

    assert check_scoped_validation_handoff_contract(tmp_path) == []


def test_atlas_257_doc_linter_does_not_extend_negation_across_but_clause(
    tmp_path: Path,
) -> None:
    write(
        tmp_path,
        "WORKFLOW.md",
        "Agents must not skip selected checks, but agents must poll CI.\n",
    )

    assert "HND001" in _codes(check_scoped_validation_handoff_contract(tmp_path))

"""ATLAS-257 negative fixtures for the scoped-validation handoff linter."""

from pathlib import Path

import pytest
from test_doc_linter import write

from atlas.tools.doc_linter import (
    AGENT_CONTRACT_PATHS,
    EXECUTION_OWNER_PATHS,
    Finding,
    check_scoped_validation_handoff_contract,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_EXECUTION_OWNER_PATHS = (
    "docs/runbooks/symphony-agent-execution.md",
    ".codex/skills/atlas-ticket-execution/SKILL.md",
    ".codex/skills/atlas-ticket-remediation/SKILL.md",
    ".codex/skills/atlas-maintenance-execution/SKILL.md",
    ".codex/skills/atlas-validation/SKILL.md",
)
PERMITTED_CI_ACTORS = ("coordinator", "operator", "reviewer", "system")
CI_OBSERVATION_PREFIXES = (
    "",
    "1. ",
    "- ",
    "- [ ] ",
    "> ",
    "Agents must not poll CI; ",
    "Agents must not poll CI, but ",
    "Agents must not poll CI, yet ",
)


def _codes(findings: list[Finding]) -> set[str]:
    return {finding.code for finding in findings}


def _build_contract_fixture(root: Path) -> None:
    for rel in dict.fromkeys((*AGENT_CONTRACT_PATHS, *EXPECTED_EXECUTION_OWNER_PATHS)):
        write(root, rel, (REPO_ROOT / rel).read_text(encoding="utf-8"))


def test_atlas_103m_execution_owner_enumeration_is_complete() -> None:
    assert EXECUTION_OWNER_PATHS == EXPECTED_EXECUTION_OWNER_PATHS


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


@pytest.mark.parametrize("owner", EXPECTED_EXECUTION_OWNER_PATHS)
@pytest.mark.parametrize(
    ("instruction", "code"),
    (
        ("Agents must poll CI until required checks pass.", "HND001"),
        ("Scoped local validation proves repository-wide completion.", "HND002"),
    ),
)
def test_atlas_103m_every_execution_owner_rejects_prohibited_claims(
    tmp_path: Path, owner: str, instruction: str, code: str
) -> None:
    _build_contract_fixture(tmp_path)
    path = tmp_path / owner
    write(tmp_path, owner, path.read_text(encoding="utf-8") + f"\n{instruction}\n")

    findings = check_scoped_validation_handoff_contract(tmp_path)

    assert any(
        finding.code == code
        and finding.path == owner
        and finding.line == len(path.read_text(encoding="utf-8").splitlines())
        for finding in findings
    )


@pytest.mark.parametrize("owner", EXPECTED_EXECUTION_OWNER_PATHS)
def test_atlas_103m_every_execution_owner_allows_legitimate_controls(
    tmp_path: Path, owner: str
) -> None:
    _build_contract_fixture(tmp_path)
    path = tmp_path / owner
    write(
        tmp_path,
        owner,
        path.read_text(encoding="utf-8")
        + "\nAgents must not poll CI or wait for review.\n"
        + "Scoped local checks never prove repository-wide completion.\n"
        + "The coordinator may poll CI after publication.\n"
        + "The reviewer may monitor checks for the frozen candidate.\n",
    )

    assert check_scoped_validation_handoff_contract(tmp_path) == []


@pytest.mark.parametrize("owner", EXPECTED_EXECUTION_OWNER_PATHS)
@pytest.mark.parametrize(
    ("instruction", "code"),
    (
        (
            "After the operator approves publication, agents must poll CI "
            "until required checks pass.",
            "HND001",
        ),
        (
            "The reviewer confirms scoped local validation proves "
            "repository-wide completion.",
            "HND002",
        ),
        (
            "For system-tier evidence, agents must poll CI until required checks pass.",
            "HND001",
        ),
    ),
)
def test_atlas_103m_actor_mentions_do_not_hide_agent_violations(
    tmp_path: Path, owner: str, instruction: str, code: str
) -> None:
    _build_contract_fixture(tmp_path)
    path = tmp_path / owner
    write(tmp_path, owner, path.read_text(encoding="utf-8") + f"\n{instruction}\n")

    findings = check_scoped_validation_handoff_contract(tmp_path)

    assert any(finding.code == code and finding.path == owner for finding in findings)


@pytest.mark.parametrize("owner", EXPECTED_EXECUTION_OWNER_PATHS)
@pytest.mark.parametrize("actor", PERMITTED_CI_ACTORS)
@pytest.mark.parametrize("prefix", CI_OBSERVATION_PREFIXES)
def test_atlas_103m_allows_bounded_actor_observation_forms(
    tmp_path: Path, owner: str, actor: str, prefix: str
) -> None:
    _build_contract_fixture(tmp_path)
    path = tmp_path / owner
    write(
        tmp_path,
        owner,
        path.read_text(encoding="utf-8")
        + f"\n{prefix}the {actor} may monitor checks for the frozen candidate.\n",
    )

    assert check_scoped_validation_handoff_contract(tmp_path) == []


@pytest.mark.parametrize("owner", EXPECTED_EXECUTION_OWNER_PATHS)
@pytest.mark.parametrize("prefix", CI_OBSERVATION_PREFIXES)
def test_atlas_103m_actor_form_normalization_never_hides_worker_instruction(
    tmp_path: Path, owner: str, prefix: str
) -> None:
    _build_contract_fixture(tmp_path)
    path = tmp_path / owner
    write(
        tmp_path,
        owner,
        path.read_text(encoding="utf-8")
        + f"\n{prefix}agents must monitor checks for the frozen candidate.\n",
    )

    findings = check_scoped_validation_handoff_contract(tmp_path)

    assert any(
        finding.code == "HND001" and finding.path == owner for finding in findings
    )

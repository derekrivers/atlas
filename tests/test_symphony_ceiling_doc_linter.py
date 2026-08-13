"""ATLAS-252 deterministic documentation-linter ceiling-contract fixtures.

The fixtures copy only the static workflow and canonical documents. They do
not start Symphony, an agent, a PM-sync loop or a live worker.
"""

from pathlib import Path

import pytest
from test_doc_linter import write

import atlas.tools.doc_linter as doc_linter
from atlas.tools.doc_linter import (
    SYMPHONY_MILESTONE_BRANCH,
    Finding,
    SymphonyMilestoneValidation,
    check_symphony_ceiling_contract,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATHS = (
    "WORKFLOW.md",
    "docs/atlas/symphony-integration.md",
    "docs/atlas/multi-agent-delivery-control.md",
    "docs/runbooks/operator-environment.md",
)


def _codes(findings: list[Finding]) -> set[str]:
    return {finding.code for finding in findings}


def _build_contract_fixture(root: Path) -> None:
    for rel in CONTRACT_PATHS:
        write(root, rel, (REPO_ROOT / rel).read_text(encoding="utf-8"))


def _set_ceiling(root: Path, ceiling: str) -> None:
    path = root / "WORKFLOW.md"
    workflow = path.read_text(encoding="utf-8")
    write(
        root,
        "WORKFLOW.md",
        workflow.replace(
            "  max_concurrent_agents: 1", f"  max_concurrent_agents: {ceiling}", 1
        ),
    )


def _write_successful_closure(root: Path) -> None:
    write(
        root,
        "docs/closure/phase-15-closure-report.md",
        "# Phase 15 Closure Report — Multi-Agent Delivery Control\n\n"
        "Status: CLOSED, deterministic fixture.\n\n"
        "## Symphony ceiling ramp\n\n"
        "Gate 10 receipt: https://example.invalid/gate-10\n\n"
        "The closure tree declares `max_concurrent_agents: 10`.\n",
    )


def test_atlas_252_doc_linter_accepts_unchanged_open_phase_at_one(
    tmp_path: Path,
) -> None:
    _build_contract_fixture(tmp_path)

    assert check_symphony_ceiling_contract(tmp_path) == []


@pytest.mark.parametrize("ceiling", ["0", "11", "true", "1.0", "'1'"])
def test_atlas_252_doc_linter_rejects_invalid_or_above_ten_ceiling(
    tmp_path: Path,
    ceiling: str,
) -> None:
    _build_contract_fixture(tmp_path)
    _set_ceiling(tmp_path, ceiling)

    assert _codes(check_symphony_ceiling_contract(tmp_path)) & {"SCG001", "SCG002"}


@pytest.mark.parametrize("ceiling", ["2", "3", "5", "7", "10"])
def test_atlas_252_doc_linter_rejects_unaccompanied_open_phase_edit(
    tmp_path: Path,
    ceiling: str,
) -> None:
    _build_contract_fixture(tmp_path)
    _set_ceiling(tmp_path, ceiling)

    findings = check_symphony_ceiling_contract(tmp_path)

    assert "SCG003" in _codes(findings)
    assert any("unaccompanied" in finding.message for finding in findings)


@pytest.mark.parametrize("ceiling", ["1", "3", "5", "7", "10"])
def test_atlas_252_doc_linter_accepts_exact_dedicated_milestone_context(
    tmp_path: Path,
    ceiling: str,
) -> None:
    _build_contract_fixture(tmp_path)
    _set_ceiling(tmp_path, ceiling)

    findings = check_symphony_ceiling_contract(
        tmp_path,
        milestone=SymphonyMilestoneValidation(
            branch=SYMPHONY_MILESTONE_BRANCH,
            level=int(ceiling),
        ),
    )

    assert findings == []


@pytest.mark.parametrize(
    ("branch", "expected_level", "declared_level"),
    [
        ("ordinary-feature-branch", 3, "3"),
        (SYMPHONY_MILESTONE_BRANCH, 5, "3"),
        (SYMPHONY_MILESTONE_BRANCH, 2, "2"),
    ],
)
def test_atlas_252_doc_linter_rejects_invalid_milestone_context(
    tmp_path: Path,
    branch: str,
    expected_level: int,
    declared_level: str,
) -> None:
    _build_contract_fixture(tmp_path)
    _set_ceiling(tmp_path, declared_level)

    findings = check_symphony_ceiling_contract(
        tmp_path,
        milestone=SymphonyMilestoneValidation(
            branch=branch,
            level=expected_level,
        ),
    )

    assert "SCG008" in _codes(findings)


def test_atlas_252_doc_linter_cli_pins_context_to_checked_out_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[SymphonyMilestoneValidation | None] = []

    def fake_lint_repo(
        root: Path,
        database: object | None = None,
        *,
        symphony_milestone: SymphonyMilestoneValidation | None = None,
    ) -> list[Finding]:
        assert root == tmp_path
        assert database is None
        captured.append(symphony_milestone)
        return []

    monkeypatch.setattr(doc_linter, "lint_repo", fake_lint_repo)
    monkeypatch.setattr(
        doc_linter,
        "_current_git_branch",
        lambda root: SYMPHONY_MILESTONE_BRANCH,
    )

    assert (
        doc_linter.main(
            [
                "--repo",
                str(tmp_path),
                "--symphony-milestone-level",
                "7",
            ]
        )
        == 0
    )
    assert captured == [
        SymphonyMilestoneValidation(branch=SYMPHONY_MILESTONE_BRANCH, level=7)
    ]


def test_atlas_252_doc_linter_preserves_max_turns_outside_ramp(
    tmp_path: Path,
) -> None:
    _build_contract_fixture(tmp_path)
    path = tmp_path / "WORKFLOW.md"
    workflow = path.read_text(encoding="utf-8")
    write(
        tmp_path,
        "WORKFLOW.md",
        workflow.replace("  max_turns: 10", "  max_turns: 11", 1),
    )

    assert "SCG007" in _codes(check_symphony_ceiling_contract(tmp_path))


def test_atlas_252_doc_linter_closure_requires_and_accepts_exactly_ten(
    tmp_path: Path,
) -> None:
    _build_contract_fixture(tmp_path)
    _set_ceiling(tmp_path, "10")
    _write_successful_closure(tmp_path)

    assert check_symphony_ceiling_contract(tmp_path) == []


def test_atlas_252_doc_linter_rejects_closed_phase_below_ten(
    tmp_path: Path,
) -> None:
    _build_contract_fixture(tmp_path)
    _set_ceiling(tmp_path, "7")
    _write_successful_closure(tmp_path)

    findings = check_symphony_ceiling_contract(tmp_path)

    assert "SCG003" in _codes(findings)
    assert any(
        "exactly max_concurrent_agents: 10" in finding.message for finding in findings
    )


def test_atlas_252_doc_linter_requires_single_ceiling_authority_wording(
    tmp_path: Path,
) -> None:
    _build_contract_fixture(tmp_path)
    path = tmp_path / "docs/atlas/multi-agent-delivery-control.md"
    text = path.read_text(encoding="utf-8")
    write(
        tmp_path,
        "docs/atlas/multi-agent-delivery-control.md",
        text.replace(
            "There is one operator-owned Symphony ceiling", "Ceilings exist", 1
        ),
    )

    assert "SCG004" in _codes(check_symphony_ceiling_contract(tmp_path))


def test_atlas_252_doc_linter_requires_active_policy_reconciliation(
    tmp_path: Path,
) -> None:
    _build_contract_fixture(tmp_path)
    path = tmp_path / "docs/atlas/multi-agent-delivery-control.md"
    text = path.read_text(encoding="utf-8")
    write(
        tmp_path,
        "docs/atlas/multi-agent-delivery-control.md",
        text.replace(
            "Revision one is immutable historical bootstrap data",
            "Revision one is the current live ceiling",
            1,
        ),
    )

    assert "SCG004" in _codes(check_symphony_ceiling_contract(tmp_path))


def test_atlas_252_doc_linter_requires_all_level_gates_in_order(
    tmp_path: Path,
) -> None:
    _build_contract_fixture(tmp_path)
    path = tmp_path / "docs/runbooks/operator-environment.md"
    text = path.read_text(encoding="utf-8")
    write(
        tmp_path,
        "docs/runbooks/operator-environment.md",
        text.replace(
            "### Gate 1 — serialized baseline admission, pause and rework",
            "### Missing gate",
            1,
        ),
    )

    assert "SCG005" in _codes(check_symphony_ceiling_contract(tmp_path))


def test_atlas_252_doc_linter_rejects_atlas_mutation_path_in_runbook(
    tmp_path: Path,
) -> None:
    _build_contract_fixture(tmp_path)
    path = tmp_path / "docs/runbooks/operator-environment.md"
    text = path.read_text(encoding="utf-8")
    write(
        tmp_path,
        "docs/runbooks/operator-environment.md",
        text + "\nPOST /api/v1/forbidden-ceiling-edit\n",
    )

    assert "SCG006" in _codes(check_symphony_ceiling_contract(tmp_path))


def test_atlas_252_doc_linter_requires_operator_only_policy_boundary(
    tmp_path: Path,
) -> None:
    _build_contract_fixture(tmp_path)
    path = tmp_path / "docs/runbooks/operator-environment.md"
    text = path.read_text(encoding="utf-8")
    write(
        tmp_path,
        "docs/runbooks/operator-environment.md",
        text.replace(
            "ramp adds no endpoint, CLI, agent action or automation that edits "
            "delivery\n"
            "policy",
            "Ramp automation updates delivery policy",
            1,
        ),
    )

    assert "SCG006" in _codes(check_symphony_ceiling_contract(tmp_path))

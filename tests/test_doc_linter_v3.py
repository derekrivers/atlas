"""Doc linter v3 (ATLAS-198): PATH and PHASE integrity checks."""

from pathlib import Path

from test_doc_linter import (
    GOOD_ADR,
    GOOD_MANIFEST,
    build_good_repo,
    codes,
    write,
)

from atlas.tools.doc_linter import (
    check_adrs,
    check_backticked_paths,
    check_generated_schemas,
    check_intra_doc_links,
    check_json_examples,
    check_legacy_names,
    check_manifest,
    check_phase_status,
    check_planning_renders,
    lint_repo,
)
from atlas.tools.schemas_export import SCHEMAS_DIR


def pth_findings(root: Path) -> list[str]:
    return [finding.render() for finding in lint_repo(root) if finding.code == "PTH001"]


def phs_codes(root: Path) -> set[str]:
    return {finding.code for finding in check_phase_status(root)}


def test_backticked_missing_repository_path_fails_pth001(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    write(
        tmp_path,
        "docs/atlas/sample-plan.md",
        "# Sample plan\n\nSee `docs/atlas/missing-spec.md`.\n",
    )

    findings = pth_findings(tmp_path)

    assert len(findings) == 1
    assert (
        findings[0] == "docs/atlas/sample-plan.md:3: PTH001 backticked repository path "
        "does not resolve: docs/atlas/missing-spec.md"
    )


def test_backticked_tools_run_planner_originating_incident_fails_path(
    tmp_path: Path,
) -> None:
    build_good_repo(tmp_path)
    write(
        tmp_path,
        "docs/atlas/sample-plan.md",
        "# Sample plan\n\nRetired harness reference: `tools/run_planner.py`.\n",
    )

    assert any("tools/run_planner.py" in finding for finding in pth_findings(tmp_path))


def test_path_closure_terminal_record_carveout_is_directory_scoped(
    tmp_path: Path,
) -> None:
    build_good_repo(tmp_path)
    dead_path_doc = "# Record\n\nDead path: `tools/run_planner.py`.\n"
    write(tmp_path, "docs/closure/dead-path-record.md", dead_path_doc)
    write(tmp_path, "docs/atlas/sample-plan.md", dead_path_doc)

    findings = pth_findings(tmp_path)

    assert len(findings) == 1
    assert findings[0].startswith("docs/atlas/sample-plan.md:3: PTH001 ")


def test_path_inbox_record_carveout_keeps_operator_stubs_terminal(
    tmp_path: Path,
) -> None:
    build_good_repo(tmp_path)
    write(
        tmp_path,
        "docs/planning/inbox/inbox-stub-path-history.md",
        "# Stub\n\nHistorical path: `tools/run_planner.py`.\n",
    )

    assert pth_findings(tmp_path) == []


def test_phase_closed_section_without_closure_report_fails_phs001(
    tmp_path: Path,
) -> None:
    build_good_repo(tmp_path)
    (tmp_path / "docs/closure/phase-1-closure-report.md").unlink()

    assert "PHS001" in phs_codes(tmp_path)


def test_phase_closure_report_without_closed_roadmap_section_fails_phs002(
    tmp_path: Path,
) -> None:
    build_good_repo(tmp_path)
    write(
        tmp_path,
        "docs/closure/phase-2-closure-report.md",
        "# Phase 2 Closure Report — Operator API\n\nStatus: CLOSED.\n",
    )

    assert "PHS002" in phs_codes(tmp_path)


def test_phase_unrecognised_status_line_fails_closed_phs004(
    tmp_path: Path,
) -> None:
    build_good_repo(tmp_path)
    roadmap = (tmp_path / "docs/atlas/implementation-roadmap.md").read_text(
        encoding="utf-8"
    )
    write(
        tmp_path,
        "docs/atlas/implementation-roadmap.md",
        roadmap.replace("Status: IN PROGRESS.", "Status: WAITING."),
    )

    assert "PHS004" in phs_codes(tmp_path)


def test_phase_current_work_claim_must_name_existing_phase_section(
    tmp_path: Path,
) -> None:
    build_good_repo(tmp_path)
    write(
        tmp_path,
        "ROADMAP.md",
        "# ROADMAP.md\n\nPhases 1 through 1 are closed.\n\n"
        "Current work: the payments phase — not in this roadmap.\n",
    )

    assert "PHS003" in phs_codes(tmp_path)


def test_phase_fractional_sections_map_to_closure_report_filenames(
    tmp_path: Path,
) -> None:
    build_good_repo(tmp_path)
    write(
        tmp_path,
        "ROADMAP.md",
        "# ROADMAP.md\n\nPhases 1 through 4 are closed.\n\n"
        "Current work: Phase 5 — current work.\n",
    )
    write(
        tmp_path,
        "docs/atlas/implementation-roadmap.md",
        "# Atlas Implementation Roadmap\n\n"
        "# Phase 1 — Knowledge Core\n\n"
        "# Phase 2 — Planning Engine\n\n"
        "Phase 2.5 live-discovered fixes:\n\n"
        "# Phase 3 — Dependency Engine\n\n"
        "# Phase 3.5 — Layer Consolidation\n\n"
        "# Phase 4 — PM Engine\n\n"
        "# Phase 5 — Operator API\n\n"
        "Status: IN PROGRESS.\n",
    )
    for phase in ("2", "2.5", "3", "3.5", "4"):
        write(
            tmp_path,
            f"docs/closure/phase-{phase}-closure-report.md",
            f"# Phase {phase} Closure Report\n\nStatus: CLOSED.\n",
        )

    assert not phs_codes(tmp_path)


def test_v3_registration_preserves_existing_check_family_finding_bytes(
    tmp_path: Path,
) -> None:
    build_good_repo(tmp_path)

    bad_adr = GOOD_ADR.replace("## Rationale\n\nSome rationale.\n\n", "")
    write(tmp_path, "docs/decisions/0001-test-decision.md", bad_adr)
    manifest = GOOD_MANIFEST + "- `ghost-doc.md`\n"
    write(tmp_path, "docs/MANIFEST.md", manifest)
    write(
        tmp_path,
        "docs/atlas/sample-plan.md",
        "# Sample plan\n\n"
        "See ATLAS_V2_MASTER_PLAN.md.\n"
        "See [missing](missing-spec.md).\n"
        "```json partial model=Lesson\n"
        '{"category": "testing",\n'
        "```\n",
    )
    write(tmp_path, "docs/planning/tickets.yaml", "tickets:\n  - key: X-1\n")
    write(tmp_path, f"{SCHEMAS_DIR}/Widget.json", "{}\n")
    ghost_line = manifest.splitlines().index("- `ghost-doc.md`") + 1

    assert [finding.render() for finding in check_adrs(tmp_path)] == [
        "docs/decisions/0001-test-decision.md:1: ADR004 missing required section: "
        "Rationale"
    ]
    assert [finding.render() for finding in check_manifest(tmp_path)] == [
        f"docs/MANIFEST.md:{ghost_line}: MAN003 listed path does not exist: "
        "ghost-doc.md"
    ]
    assert [finding.render() for finding in check_legacy_names(tmp_path)] == [
        "docs/atlas/sample-plan.md:3: LEG002 legacy document name referenced: ATLAS_V2",
        "docs/atlas/sample-plan.md:3: LEG002 legacy document name referenced: _V2_",
    ]
    assert [finding.render() for finding in check_intra_doc_links(tmp_path)] == [
        "docs/atlas/sample-plan.md:4: LNK001 relative link target does not resolve: "
        "missing-spec.md"
    ]
    assert [finding.render() for finding in check_planning_renders(tmp_path)] == [
        "docs/planning/tickets.yaml:1: PLAN001 planning file lacks the atlas "
        "apply render header (hand-edit? renders are written only by atlas apply, "
        "ADR-0007)"
    ]
    assert [finding.render() for finding in check_json_examples(tmp_path)] == [
        "docs/atlas/sample-plan.md:5: JSN003 invalid JSON: Expecting property "
        "name enclosed in double quotes: line 1 column 24 (char 23)"
    ]
    assert [finding.render() for finding in check_generated_schemas(tmp_path)] == [
        "docs/generated/schemas/Widget.json:1: GEN001 file is not a canonical "
        "model schema; docs/generated is machine-written; run python -m "
        "atlas.tools.schemas_export"
    ]
    assert codes(check_backticked_paths(tmp_path)) == {"PTH001"}
    assert check_phase_status(tmp_path) == []

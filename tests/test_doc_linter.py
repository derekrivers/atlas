"""Doc linter v1 (ATLAS-4): positive fixture and the seeded bad fixtures
from the ticket's definition of done. Real-repo cleanliness is enforced
solely by the lint-docs CI job (single-owner LINT_RESULT, ADR-0008), not
by a pytest pin."""

from pathlib import Path

from atlas.tools.doc_linter import Finding, lint_repo, main

GOOD_ADR = """\
# ADR-0001: Test decision

## Status

Accepted

## Context

Some context.

## Decision

Some decision.

## Rationale

Some rationale.

## Consequences

- One consequence.

## Alternatives considered

- None worth taking.
"""

GOOD_MANIFEST = """\
# Manifest

Root control documents:

- `README.md`

Strategy and specification (`docs/atlas/`):

- `sample-plan.md`

Architecture (`docs/architecture/`):

- `docs/architecture/arch.md`

Decisions (`docs/decisions/`):

- ADR-0001 Test decision
"""


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def build_good_repo(root: Path) -> None:
    write(root, "README.md", "# Test repo\n")
    write(root, "docs/MANIFEST.md", GOOD_MANIFEST)
    write(root, "docs/atlas/sample-plan.md", "# Sample plan\n\nIntent.\n")
    write(root, "docs/architecture/arch.md", "# Architecture\n\nDetail.\n")
    write(root, "docs/decisions/0001-test-decision.md", GOOD_ADR)
    write(root, "docs/planning/.gitkeep", "")


def codes(findings: list[Finding]) -> set[str]:
    return {finding.code for finding in findings}


def test_clean_fixture_repo_passes(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    assert lint_repo(tmp_path) == []
    assert main(["--repo", str(tmp_path)]) == 0


def test_adr_missing_rationale_fails(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    bad_adr = GOOD_ADR.replace("## Rationale\n\nSome rationale.\n\n", "")
    write(tmp_path, "docs/decisions/0001-test-decision.md", bad_adr)
    findings = lint_repo(tmp_path)
    assert "ADR004" in codes(findings)
    assert any("Rationale" in finding.message for finding in findings)
    assert main(["--repo", str(tmp_path)]) == 1


def test_adr_empty_section_fails(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    bad_adr = GOOD_ADR.replace("Some context.\n", "")
    write(tmp_path, "docs/decisions/0001-test-decision.md", bad_adr)
    assert "ADR005" in codes(lint_repo(tmp_path))


def test_adr_unrecognised_status_fails(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    bad_adr = GOOD_ADR.replace("Accepted", "Pondering")
    write(tmp_path, "docs/decisions/0001-test-decision.md", bad_adr)
    assert "ADR006" in codes(lint_repo(tmp_path))


def test_manifest_entry_pointing_at_missing_file_fails(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    manifest = GOOD_MANIFEST + "- `ghost-doc.md`\n"
    write(tmp_path, "docs/MANIFEST.md", manifest)
    findings = lint_repo(tmp_path)
    assert "MAN003" in codes(findings)
    assert main(["--repo", str(tmp_path)]) == 1


def test_manifest_listed_adr_without_file_fails(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    manifest = GOOD_MANIFEST + "- ADR-0002 Imaginary decision\n"
    write(tmp_path, "docs/MANIFEST.md", manifest)
    assert "MAN004" in codes(lint_repo(tmp_path))


def test_canonical_doc_not_listed_fails(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    write(tmp_path, "docs/atlas/unlisted.md", "# Unlisted\n\nContent.\n")
    findings = lint_repo(tmp_path)
    assert "MAN005" in codes(findings)
    assert any("unlisted.md" in finding.path for finding in findings)


def test_canonical_runbook_not_listed_fails(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    write(tmp_path, "docs/runbooks/new-runbook.md", "# Runbook\n\nSteps.\n")
    findings = lint_repo(tmp_path)
    assert "MAN005" in codes(findings)
    assert any("new-runbook.md" in finding.path for finding in findings)


def test_legacy_historic_name_in_active_doc_fails(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    write(
        tmp_path,
        "docs/atlas/sample-plan.md",
        "# Sample plan\n\nSee ATLAS_V2_MASTER_PLAN.md for the old plan.\n",
    )
    findings = lint_repo(tmp_path)
    assert "LEG002" in codes(findings)
    assert main(["--repo", str(tmp_path)]) == 1


def test_legacy_filename_forms_fail(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    write(tmp_path, "docs/atlas/old-spec-v2.md", "# Old\n")
    write(tmp_path, "roadmap.html", "<html></html>")
    found = codes(lint_repo(tmp_path))
    assert "LEG001" in found


def test_legacy_names_allowed_in_archive(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    write(
        tmp_path,
        "docs/archive/design-history/old-notes.md",
        "Replaced ATLAS_V2_MASTER_PLAN.md and roadmap.html entirely.\n",
    )
    assert not codes(lint_repo(tmp_path)) & {"LEG001", "LEG002"}


def test_retirement_record_line_is_allowed(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    write(
        tmp_path,
        "docs/atlas/sample-plan.md",
        "# Sample plan\n\nRetired: roadmap.html (replaced by Mermaid).\n",
    )
    assert not codes(lint_repo(tmp_path)) & {"LEG001", "LEG002"}


def test_broken_relative_md_link_fails(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    write(
        tmp_path,
        "docs/atlas/sample-plan.md",
        "# Sample plan\n\nSee [the spec](missing-spec.md) for detail.\n",
    )
    findings = lint_repo(tmp_path)
    assert "LNK001" in codes(findings)
    assert any("missing-spec.md" in finding.message for finding in findings)


def test_resolving_links_fragments_and_urls_pass(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    write(
        tmp_path,
        "docs/atlas/sample-plan.md",
        "# Sample plan\n\n"
        "See [arch](../architecture/arch.md), [root](/README.md), "
        "[anchor](#heading), and [web](https://example.com/x.md).\n",
    )
    assert "LNK001" not in codes(lint_repo(tmp_path))


def test_hand_edited_planning_file_fails(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    write(tmp_path, "docs/planning/tickets.yaml", "tickets:\n  - key: X-1\n")
    findings = lint_repo(tmp_path)
    assert "PLAN001" in codes(findings)
    assert main(["--repo", str(tmp_path)]) == 1


def test_planning_render_with_apply_header_passes(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    write(
        tmp_path,
        "docs/planning/tickets.yaml",
        "# Render written only by atlas apply. plan_run_id: 1234\n"
        "# prompt_version: planner-v1.0.0\n"
        "tickets: []\n",
    )
    assert "PLAN001" not in codes(lint_repo(tmp_path))

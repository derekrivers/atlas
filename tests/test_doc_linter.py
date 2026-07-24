"""Doc linter v1 (ATLAS-4): positive fixture and the seeded bad fixtures
from the ticket's definition of done. Real-repo cleanliness is enforced
solely by the lint-docs CI job (single-owner LINT_RESULT, ADR-0008), not
by a pytest pin."""

from pathlib import Path

from atlas.tools.doc_linter import Finding, lint_repo, main
from atlas.tools.schemas_export import export

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
- `implementation-roadmap.md`

Playbooks (generated canonical docs):

Architecture (`docs/architecture/`):

- `docs/architecture/arch.md`

Decisions (`docs/decisions/`):

- ADR-0001 Test decision
"""

GOOD_ROOT_ROADMAP = """\
# ROADMAP.md

The canonical roadmap lives at
`docs/atlas/implementation-roadmap.md`. Phase closure is recorded
in `docs/closure/` — Phase 1 is closed.

Current work: the operator API phase — a read-only HTTP projection surface.
"""

GOOD_IMPLEMENTATION_ROADMAP = """\
# Atlas Implementation Roadmap

# Phase 1 — Knowledge Core

Milestone test: closed.

---

# Phase 2 — Operator API

Status: IN PROGRESS.

Milestone test: underway.
"""

GOOD_PHASE_1_CLOSURE = """\
# Phase 1 Closure Report — Knowledge Core

Status: CLOSED, 2026-06-12.
"""


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def build_good_repo(root: Path) -> None:
    write(root, "README.md", "# Test repo\n")
    write(root, "ROADMAP.md", GOOD_ROOT_ROADMAP)
    write(root, "docs/MANIFEST.md", GOOD_MANIFEST)
    write(root, "docs/atlas/sample-plan.md", "# Sample plan\n\nIntent.\n")
    write(root, "docs/atlas/implementation-roadmap.md", GOOD_IMPLEMENTATION_ROADMAP)
    write(root, "docs/architecture/arch.md", "# Architecture\n\nDetail.\n")
    write(root, "docs/decisions/0001-test-decision.md", GOOD_ADR)
    write(root, "docs/closure/phase-1-closure-report.md", GOOD_PHASE_1_CLOSURE)
    write(root, "docs/planning/.gitkeep", "")
    # Linter v2's regeneration check (GEN001) requires real generated
    # schemas in any clean fixture repo.
    export(root)


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


def test_manifest_check_recurses_into_canonical_subdirectories(
    tmp_path: Path,
) -> None:
    # Seeded red first with `assert 1 == 2` (B011); the regression was the old
    # non-recursive glob never seeing this file.
    build_good_repo(tmp_path)
    nested = "docs/atlas/probes/x.md"
    write(tmp_path, nested, "# Nested canonical doc\n\nContent.\n")

    findings = lint_repo(tmp_path)

    assert "MAN005" in codes(findings)
    assert any(finding.path == nested for finding in findings)

    manifest = (tmp_path / "docs/MANIFEST.md").read_text(encoding="utf-8")
    write(
        tmp_path,
        "docs/MANIFEST.md",
        manifest.replace(
            "- `implementation-roadmap.md`\n\n",
            "- `implementation-roadmap.md`\n"
            f"- `{nested}` — fixture nested canonical doc\n\n",
        ),
    )

    assert lint_repo(tmp_path) == []


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


def test_processed_inbox_iteration_suffix_filename_is_exempt(tmp_path: Path) -> None:
    # D-1: an operator-authored stub name in the terminal inbox is a different
    # namespace from retired canonical-doc generations; the name check exempts
    # docs/planning/inbox/processed/ only.
    build_good_repo(tmp_path)
    write(
        tmp_path,
        "docs/planning/inbox/processed/smoke-b-fixture-v2.md",
        "# Smoke B fixture stub\n",
    )
    assert "LEG001" not in codes(lint_repo(tmp_path))


def test_backticked_inbox_path_reference_is_exempt(tmp_path: Path) -> None:
    # D-2: a run record must be able to name an inbox stub verbatim.
    build_good_repo(tmp_path)
    write(
        tmp_path,
        "docs/atlas/sample-plan.md",
        "# Sample plan\n\n"
        "Stub `docs/planning/inbox/smoke-b-fixture-v2.md` moved to processed/.\n",
    )
    assert "LEG002" not in codes(lint_repo(tmp_path))


def test_v2_filename_outside_processed_inbox_still_fails(tmp_path: Path) -> None:
    # NEGATIVE: the same filename one directory up (inbox/, not processed/) is
    # not exempt — proving the D-1 carve-out is narrow.
    build_good_repo(tmp_path)
    write(
        tmp_path,
        "docs/planning/inbox/smoke-b-fixture-v2.md",
        "# Smoke B fixture stub\n",
    )
    assert "LEG001" in codes(lint_repo(tmp_path))


def test_non_inbox_backticked_v2_reference_still_fails(tmp_path: Path) -> None:
    # NEGATIVE: a backticked -v2.md path that is not under the inbox still fires.
    build_good_repo(tmp_path)
    write(
        tmp_path,
        "docs/atlas/sample-plan.md",
        "# Sample plan\n\nSee `docs/atlas/old-spec-v2.md` for the old spec.\n",
    )
    assert "LEG002" in codes(lint_repo(tmp_path))


def test_leg002_exemption_is_span_scoped_not_line_scoped(tmp_path: Path) -> None:
    # A-2 NEGATIVE: one line with two backticked spans — an exempt inbox path
    # and a non-inbox -v2.md reference. The non-inbox match must still fire,
    # proving the exemption is scoped to the inbox backtick's span, not the line.
    build_good_repo(tmp_path)
    write(
        tmp_path,
        "docs/atlas/sample-plan.md",
        "# Sample plan\n\n"
        "Stub `docs/planning/inbox/smoke-b-fixture-v2.md` supersedes "
        "`docs/atlas/old-spec-v2.md`.\n",
    )
    findings = lint_repo(tmp_path)
    leg002 = [f for f in findings if f.code == "LEG002"]
    # Exactly one: the non-inbox reference fires, the inbox one is exempt. A
    # line-scoped exemption would suppress both and yield zero.
    assert len(leg002) == 1, leg002
    assert leg002[0].path == "docs/atlas/sample-plan.md"


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

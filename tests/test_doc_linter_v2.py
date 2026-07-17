"""Doc linter v2 (ATLAS-16): JSON-example validation and generated-schema
integrity, on the v1 fixture-repo pattern. Each negative fixture asserts
its distinct, correctly-attributed code — the Phase 1 milestone's
falsifiability requirement ("the schema-drift linter fails on a seeded
mismatched JSON example")."""

import shutil
from pathlib import Path

import pytest
from test_doc_linter import build_good_repo, codes, write

from atlas.tools.doc_linter import lint_repo, main
from atlas.tools.schemas_export import SCHEMAS_DIR

GOOD_LESSON_FENCE = """\
# Sample plan

```json partial model=Lesson
{
  "category": "testing",
  "title": "Small tickets work",
  "confidence": 0.5
}
```
"""

FULL_LESSON_BODY = """\
{
  "id": "7f3e9b2a-5c1d-4e8f-a6b4-9d2c8e7f1a30",
  "product_id": "c4a8d1f6-2b9e-4d57-8e3a-6f1b0c9d4e72",
  "status": "draft",
  "category": "testing",
  "title": "Small tickets work",
  "problem": "Large tickets fail.",
  "solution": "Split them.",
  "outcome": "Higher success rate.",
  "confidence": 0.5,
  "source_ticket_id": "1dbac66f-d21a-4531-b071-71d286ccf75b",
  "related_ticket_ids": [],
  "related_adr_ids": [],
  "tags": ["planning"],
  "created_by_type": "agent",
  "created_by_id": "claude",
  "created_at": "2026-06-12T10:00:00Z",
  "updated_at": "2026-06-12T10:00:00Z"
}\
"""


def write_fence(
    root: Path, body: str, marker: str = "json partial model=Lesson"
) -> Path:
    return write(
        root,
        "docs/atlas/sample-plan.md",
        f"# Sample plan\n\n```{marker}\n{body}\n```\n",
    )


def test_clean_mapped_partial_example_passes(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    write(tmp_path, "docs/atlas/sample-plan.md", GOOD_LESSON_FENCE)
    assert lint_repo(tmp_path) == []
    assert main(["--repo", str(tmp_path)]) == 0


def test_clean_full_example_passes_non_partial(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    write_fence(tmp_path, FULL_LESSON_BODY, marker="json model=Lesson")
    assert lint_repo(tmp_path) == []


def test_unmapped_fence_fails_jsn001(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    write_fence(tmp_path, '{"category": "testing"}', marker="json")
    findings = lint_repo(tmp_path)
    assert codes(findings) == {"JSN001"}
    assert main(["--repo", str(tmp_path)]) == 1


def test_unrecognised_marker_fails_jsn001(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    write_fence(tmp_path, "{}", marker="json schema=Lesson")
    assert codes(lint_repo(tmp_path)) == {"JSN001"}


def test_no_schema_contradicting_model_fails_jsn001(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    write_fence(tmp_path, "{}", marker="json no-schema model=Lesson")
    assert codes(lint_repo(tmp_path)) == {"JSN001"}


def test_no_schema_fence_is_exempt(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    write_fence(tmp_path, '{"anything": ["goes", 1, null]}', marker="json no-schema")
    assert lint_repo(tmp_path) == []


def test_unknown_model_fails_jsn002(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    write_fence(tmp_path, "{}", marker="json model=Widget")
    findings = lint_repo(tmp_path)
    assert codes(findings) == {"JSN002"}
    assert any("Widget" in finding.message for finding in findings)


def test_invalid_json_fails_jsn003(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    write_fence(tmp_path, '{"category": "testing",')
    assert codes(lint_repo(tmp_path)) == {"JSN003"}


def test_unknown_key_fails_jsn004(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    write_fence(tmp_path, '{"categry": "testing"}')
    findings = lint_repo(tmp_path)
    assert codes(findings) == {"JSN004"}
    assert any("Lesson.categry" in finding.message for finding in findings)


def test_wrong_type_fails_jsn005(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    write_fence(tmp_path, '{"confidence": "high"}')
    findings = lint_repo(tmp_path)
    assert codes(findings) == {"JSN005"}
    assert any("Lesson.confidence" in finding.message for finding in findings)


def test_bad_enum_value_fails_jsn005(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    write_fence(tmp_path, '{"category": "vibes"}')
    assert codes(lint_repo(tmp_path)) == {"JSN005"}


def test_key_string_in_uuid_list_fails_jsn005(tmp_path: Path) -> None:
    # The historical §7 defect class: human-readable keys in a
    # list[UUID] field are a format mismatch, partial or not.
    build_good_repo(tmp_path)
    write_fence(
        tmp_path,
        '{"relevant_adrs": ["ADR-0003"]}',
        marker="json partial model=ContextPack",
    )
    findings = lint_repo(tmp_path)
    assert codes(findings) == {"JSN005"}
    assert any("uuid" in finding.message for finding in findings)


@pytest.mark.parametrize("out_of_bounds", ["-0.1", "1.1"])
def test_numeric_bound_violation_fails_jsn005(
    tmp_path: Path, out_of_bounds: str
) -> None:
    # Lesson.confidence carries minimum 0 / maximum 1 in the generated
    # schema; both directions are JSN005, not a skipped construct.
    build_good_repo(tmp_path)
    write_fence(tmp_path, f'{{"confidence": {out_of_bounds}}}')
    findings = lint_repo(tmp_path)
    assert codes(findings) == {"JSN005"}
    assert any("Lesson.confidence" in finding.message for finding in findings)


def test_numeric_bounds_accept_boundary_values(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    write_fence(tmp_path, '{"confidence": 1}')
    assert lint_repo(tmp_path) == []


def test_missing_required_fails_jsn006_in_non_partial(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    write_fence(tmp_path, '{"category": "testing"}', marker="json model=Lesson")
    findings = lint_repo(tmp_path)
    assert "JSN006" in codes(findings)
    assert any("Lesson.title" in finding.message for finding in findings)


def test_partial_suppresses_jsn006_but_not_key_and_type_checks(
    tmp_path: Path,
) -> None:
    build_good_repo(tmp_path)
    write_fence(tmp_path, '{"categry": "testing", "confidence": "high"}')
    found = codes(lint_repo(tmp_path))
    assert found == {"JSN004", "JSN005"}


def test_unsupported_schema_construct_fails_closed_jsn007(tmp_path: Path) -> None:
    # A schema construct the validator does not recognise is a failure,
    # never a silent skip. The seeded schema also trips GEN001 (it is a
    # hand-edit by definition); both attributions must be present.
    build_good_repo(tmp_path)
    write(
        tmp_path,
        f"{SCHEMAS_DIR}/Lesson.json",
        '{"type": "object", "properties": '
        '{"title": {"oneOf": [{"type": "string"}]}}}\n',
    )
    write_fence(tmp_path, '{"title": "x"}')
    found = codes(lint_repo(tmp_path))
    assert "JSN007" in found
    assert "GEN001" in found


def test_hand_edited_schema_fails_gen001(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    target = tmp_path / SCHEMAS_DIR / "Lesson.json"
    target.write_text(
        target.read_text(encoding="utf-8").replace('"title"', '"retitled"', 1),
        encoding="utf-8",
    )
    findings = lint_repo(tmp_path)
    assert codes(findings) == {"GEN001"}
    assert any("Lesson.json" in finding.path for finding in findings)
    assert main(["--repo", str(tmp_path)]) == 1


def test_missing_schema_file_fails_gen001(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    (tmp_path / SCHEMAS_DIR / "Ticket.json").unlink()
    assert codes(lint_repo(tmp_path)) == {"GEN001"}


def test_missing_schemas_directory_fails_gen001(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    shutil.rmtree(tmp_path / "docs/generated")
    assert codes(lint_repo(tmp_path)) == {"GEN001"}


def test_extra_schema_file_fails_gen001(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    write(tmp_path, f"{SCHEMAS_DIR}/Widget.json", "{}\n")
    findings = lint_repo(tmp_path)
    assert codes(findings) == {"GEN001"}
    assert any("Widget.json" in finding.path for finding in findings)

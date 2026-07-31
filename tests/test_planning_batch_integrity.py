"""Gate 0: Atlas-owned planning-batch integrity before plan and apply."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from test_apply import APPLY_NOW, confirmed
from test_plan_pipeline import NOW, PLAN_MD, PRODUCT_MD, git, make_repo
from test_stubs_only import july_db

import atlas.planning.apply as apply_module
import atlas.planning.pipeline as pipeline_module
from atlas.core.models import PlanRunStatus
from atlas.planning.apply import run_apply
from atlas.planning.pipeline import PlanResult, run_stubs_only_plan
from atlas.planning.promotion import StubPromotionError
from atlas.planning.stub_integrity import PlanningBatchIntegrityError
from atlas.storage import Database, PlanRunRepo

INBOX = "docs/planning/inbox"
MANIFEST = f"{INBOX}/planning-batch-phase-13-15.yaml"
DOC = "docs/atlas/plan.md"
CANONICAL_FUTURE_DOC = "docs/runbook.md"
NON_CANONICAL_PATHS = (
    "docs//runbook.md",
    "./docs/runbook.md",
    "docs/./runbook.md",
)


def _stub(
    number: int,
    title: str,
    *,
    depends_on: tuple[str, ...] = (),
    documentation_requirements: tuple[str, ...] = (DOC,),
) -> tuple[str, str]:
    name = f"inbox-stub-{number:02d}-{title.lower().replace(' ', '-')}.md"
    path = f"{INBOX}/{name}"
    front_matter = {
        "title": title,
        "objective": f"{title} objective.",
        "context": "Gate 0 planning-batch fixture.",
        "ticket_type": "feature",
        "epic_ref": "ATLAS-E1",
        "risk_level": "medium",
        "component": "planning",
        "tags": ["phase-13"],
        "relevant_docs": [DOC],
        "depends_on": list(depends_on),
        "acceptance_criteria": ["The named behaviour is proved."],
        "non_goals": ["No unrelated change."],
        "test_requirements": ["A named deterministic test passes."],
        "implementation_notes": ["Preserve the operator apply boundary."],
        "documentation_requirements": list(documentation_requirements),
        "definition_of_done": ["Evidence is recorded."],
    }
    content = (
        "---\n"
        + yaml.safe_dump(front_matter, sort_keys=False)
        + "---\n\n"
        + f"# {title}\n"
    )
    return path, content


def _phase_repo(
    tmp_path: Path,
    stubs: list[tuple[str, str]],
    *,
    include_manifest: bool = True,
    manifest_repository_files: list[str] | None = None,
    future_document_paths: list[str] | None = None,
) -> Path:
    repo = make_repo(
        tmp_path, {"PRODUCT.md": PRODUCT_MD, "docs/atlas/plan.md": PLAN_MD}
    )
    base = git(repo, "rev-parse", "HEAD").strip()
    for path, content in stubs:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    if include_manifest:
        stub_paths = [path for path, _ in stubs]
        repository_files = (
            [MANIFEST, *stub_paths]
            if manifest_repository_files is None
            else manifest_repository_files
        )
        manifest = {
            "schema_version": 1,
            "repository": "derekrivers/atlas",
            "base_commit": base,
            "repository_files": repository_files,
            "future_document_paths": future_document_paths or [],
            "stubs": [{"path": path, "phase": 13} for path in stub_paths],
        }
        target = repo / MANIFEST
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "planning batch")
    return repo


def _run(repo: Path, tmp_path: Path) -> tuple[Database, PlanResult]:
    database = july_db(tmp_path)
    return database, run_stubs_only_plan(repo_root=repo, database=database, now=NOW)


def test_valid_ordered_batch_passes_atlas_owned_gate(tmp_path: Path) -> None:
    stubs = [_stub(1, "Security foundation"), _stub(2, "Action ledger")]
    repo = _phase_repo(tmp_path, stubs)
    database, result = _run(repo, tmp_path)

    assert result.status is PlanRunStatus.PROPOSED
    assert result.diff is not None
    assert result.diff.counts["ADD"] == 2
    assert len(PlanRunRepo(database).list()) == 1
    assert MANIFEST in result.plan_run.input_doc_shas


def test_ordered_batch_without_manifest_fails_before_planrun(tmp_path: Path) -> None:
    repo = _phase_repo(
        tmp_path, [_stub(1, "Security foundation")], include_manifest=False
    )
    database = july_db(tmp_path)

    with pytest.raises(PlanningBatchIntegrityError, match="require exactly one"):
        run_stubs_only_plan(repo_root=repo, database=database, now=NOW)
    assert PlanRunRepo(database).list() == []


def test_manifest_must_cover_exact_committed_overlay(tmp_path: Path) -> None:
    stubs = [_stub(1, "Security foundation")]
    repo = _phase_repo(
        tmp_path,
        stubs,
        manifest_repository_files=[MANIFEST],
    )
    database = july_db(tmp_path)

    with pytest.raises(PlanningBatchIntegrityError, match="exact committed overlay"):
        run_stubs_only_plan(repo_root=repo, database=database, now=NOW)
    assert PlanRunRepo(database).list() == []


def test_prose_exact_path_field_fails_before_planrun(tmp_path: Path) -> None:
    stubs = [
        _stub(
            1,
            "Security foundation",
            documentation_requirements=("Update the operator API documentation.",),
        )
    ]
    repo = _phase_repo(tmp_path, stubs)
    database = july_db(tmp_path)

    with pytest.raises(StubPromotionError, match="exact repository-relative"):
        run_stubs_only_plan(repo_root=repo, database=database, now=NOW)
    assert PlanRunRepo(database).list() == []


@pytest.mark.parametrize("path", NON_CANONICAL_PATHS)
def test_noncanonical_stub_path_fails_before_planrun(tmp_path: Path, path: str) -> None:
    stubs = [_stub(1, "Security foundation", documentation_requirements=(path,))]
    repo = _phase_repo(
        tmp_path,
        stubs,
        future_document_paths=[CANONICAL_FUTURE_DOC],
    )
    database = july_db(tmp_path)

    with pytest.raises(StubPromotionError, match="exact repository-relative"):
        run_stubs_only_plan(repo_root=repo, database=database, now=NOW)
    assert PlanRunRepo(database).list() == []


@pytest.mark.parametrize("path", NON_CANONICAL_PATHS)
def test_noncanonical_manifest_future_path_fails_before_planrun(
    tmp_path: Path, path: str
) -> None:
    stubs = [
        _stub(
            1,
            "Security foundation",
            documentation_requirements=(CANONICAL_FUTURE_DOC,),
        )
    ]
    repo = _phase_repo(tmp_path, stubs, future_document_paths=[path])
    database = july_db(tmp_path)

    with pytest.raises(PlanningBatchIntegrityError, match="unsafe path"):
        run_stubs_only_plan(repo_root=repo, database=database, now=NOW)
    assert PlanRunRepo(database).list() == []


def test_forward_sibling_dependency_fails_before_planrun(tmp_path: Path) -> None:
    second_path, second = _stub(2, "Action ledger")
    second_name = Path(second_path).name
    stubs = [
        _stub(1, "Security foundation", depends_on=(second_name,)),
        (second_path, second),
    ]
    repo = _phase_repo(tmp_path, stubs)
    database = july_db(tmp_path)

    with pytest.raises(StubPromotionError, match="earlier ordered stub"):
        run_stubs_only_plan(repo_root=repo, database=database, now=NOW)
    assert PlanRunRepo(database).list() == []


def test_sibling_cycle_fails_before_order_diagnostic(tmp_path: Path) -> None:
    first_path, first = _stub(
        1, "Security foundation", depends_on=("inbox-stub-02-action-ledger.md",)
    )
    second_path, second = _stub(2, "Action ledger", depends_on=(Path(first_path).name,))
    repo = _phase_repo(tmp_path, [(first_path, first), (second_path, second)])
    database = july_db(tmp_path)

    with pytest.raises(PlanningBatchIntegrityError, match="dependency cycle"):
        run_stubs_only_plan(repo_root=repo, database=database, now=NOW)
    assert PlanRunRepo(database).list() == []


def test_apply_revalidates_and_retires_batch_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stubs = [_stub(1, "Security foundation")]
    repo = _phase_repo(tmp_path, stubs)
    database, result = _run(repo, tmp_path)
    assert result.status is PlanRunStatus.PROPOSED

    calls = 0
    real_validate = apply_module.validate_inbox_batch_integrity  # type: ignore[attr-defined]

    def counting_validate(**kwargs: object) -> None:
        nonlocal calls
        calls += 1
        real_validate(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        apply_module, "validate_inbox_batch_integrity", counting_validate
    )
    applied = run_apply(
        repo_root=repo,
        database=database,
        now=APPLY_NOW,
        confirm=confirmed,
    )

    assert applied.outcome == "applied"
    assert calls == 1
    assert not (repo / MANIFEST).exists()
    assert (repo / f"{INBOX}/processed/{Path(MANIFEST).name}").is_file()
    assert not (repo / stubs[0][0]).exists()
    assert (repo / f"{INBOX}/processed/{Path(stubs[0][0]).name}").is_file()


@pytest.mark.parametrize("path", NON_CANONICAL_PATHS)
@pytest.mark.parametrize("invalid_source", ("stub", "manifest"))
def test_apply_rejects_noncanonical_paths_before_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    invalid_source: str,
) -> None:
    requirement = path if invalid_source == "stub" else CANONICAL_FUTURE_DOC
    future_path = path if invalid_source == "manifest" else CANONICAL_FUTURE_DOC
    stubs = [
        _stub(
            1,
            "Security foundation",
            documentation_requirements=(requirement,),
        )
    ]
    repo = _phase_repo(tmp_path, stubs, future_document_paths=[future_path])

    with monkeypatch.context() as planning_context:
        planning_context.setattr(
            pipeline_module, "validate_inbox_batch_integrity", lambda **_: None
        )
        database, result = _run(repo, tmp_path)
    assert result.status is PlanRunStatus.PROPOSED

    error = (
        StubPromotionError if invalid_source == "stub" else PlanningBatchIntegrityError
    )
    with pytest.raises(error):
        run_apply(
            repo_root=repo,
            database=database,
            now=APPLY_NOW,
            confirm=confirmed,
        )

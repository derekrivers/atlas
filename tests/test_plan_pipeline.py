"""ATLAS-26: the `atlas plan` pipeline composition and failure contract.

Every test injects a fake client and an on-disk SQLite database against a
committed git fixture repo — zero real API calls. Field-by-field
assertions on the persisted PlanRun (happy path), the AT-5 provenance
shadow, and one test per gap-1 failure class.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from planner_fakes import (
    FAKE_IDENTITY,
    FakePlannerClient,
    RaisingPlannerClient,
    TruncatingPlannerClient,
)
from test_models_validation import product_kwargs

from atlas.core.models import PlanRunStatus, Product
from atlas.planning.ingestion import DirtyInputError
from atlas.planning.pipeline import (
    NoInputDocumentsError,
    ProductNotFoundError,
    run_plan,
)
from atlas.planning.reconciler import DEFAULT_SIMILARITY_THRESHOLD
from atlas.storage import Database, PlanRunRepo, ProductRepo

NOW = datetime(2026, 6, 14, 12, tzinfo=UTC)

PRODUCT_MD = "# Atlas\n\n## Vision\n\nRepeatable delivery.\n"
PLAN_MD = "# Planning\n\n## Backlog\n\nThe backlog section.\n"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def make_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "fixture"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    for rel, content in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "fixture")
    return repo


def fixture_repo(tmp_path: Path) -> Path:
    return make_repo(
        tmp_path, {"PRODUCT.md": PRODUCT_MD, "docs/atlas/plan.md": PLAN_MD}
    )


def fresh_db(tmp_path: Path, *, with_product: bool = True) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    if with_product:
        ProductRepo(database).add(Product(**product_kwargs()))
    return database


def _epic(**overrides: Any) -> dict[str, Any]:
    return {
        "key": None,
        "title": "Planning Engine",
        "description": "Generative planning.",
        "objective": "Plan and apply.",
        "priority": 10,
        "risk_level": "medium",
        "source_anchor": "docs/atlas/plan.md#planning",
    } | overrides


def _ticket(**overrides: Any) -> dict[str, Any]:
    return {
        "key": None,
        "epic_ref": "new_epic:0",
        "title": "Build plan CLI",
        "objective": "atlas plan exists.",
        "context": "Phase 2.",
        "ticket_type": "feature",
        "risk_level": "medium",
        "priority": 10,
        "source_anchor": "docs/atlas/plan.md#backlog",
        "relevant_docs": [],
        "tags": ["planning"],
        "component": "planning",
        "acceptance_criteria": ["It composes the pipeline."],
        "non_goals": ["No apply."],
        "test_requirements": ["Pipeline tests."],
        "implementation_notes": [],
        "documentation_requirements": [],
        "definition_of_done": ["Tests pass."],
    } | overrides


def proposal_json(**overrides: Any) -> str:
    payload = {
        "epics": [_epic()],
        "tickets": [_ticket()],
        "dependencies": [],
        "planner_notes": [],
    } | overrides
    return json.dumps(payload)


def run(repo: Path, database: Database, client: Any, **kwargs: Any) -> Any:
    return run_plan(
        repo_root=repo,
        database=database,
        client=client,
        identity=FAKE_IDENTITY,
        now=NOW,
        **kwargs,
    )


# --- happy path -------------------------------------------------------------


def test_happy_path_persists_proposed_plan_run(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    client = FakePlannerClient(proposal_json())

    result = run(repo, database, client)

    assert result.status is PlanRunStatus.PROPOSED
    assert result.diff is not None
    assert result.failure_reason is None

    stored = PlanRunRepo(database).list()
    assert len(stored) == 1
    run_row = stored[0]
    assert run_row.status is PlanRunStatus.PROPOSED
    # Provenance + identity, field by field.
    assert run_row.model_provider == "fake"
    assert run_row.model_name == "fake-model-1"
    assert run_row.model_parameters == {"temperature": 0, "max_tokens": 1024}
    assert run_row.prompt_version.startswith("planner-v")
    assert len(run_row.prompt_hash) == 64
    assert len(run_row.raw_output_hash) == 64
    assert run_row.similarity_threshold == DEFAULT_SIMILARITY_THRESHOLD
    assert run_row.diff_summary == result.diff.as_summary()
    assert run_row.failure_reason is None
    assert run_row.applied_at is None
    # Gap 0: the validated proposal is persisted for apply to materialise.
    assert set(run_row.proposal) == {
        "epics",
        "tickets",
        "dependencies",
        "planner_notes",
    }
    assert run_row.proposal["tickets"][0]["title"] == "Build plan CLI"


def test_single_call_populates_degenerate_one_stage_list(tmp_path: Path) -> None:
    # ATLAS-105 gap 2: the single-call path populates generation_stages with a
    # one-element list, so the field's meaning is uniform — a reader always
    # learns how many stages produced the proposal (here, one).
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    run(repo, database, FakePlannerClient(proposal_json()))

    stored = PlanRunRepo(database).list()[0]
    assert len(stored.generation_stages) == 1
    stage = stored.generation_stages[0]
    assert stage["stage"] == "single"
    # The degenerate one-stage record mirrors the top-level chain (ATLAS-104's
    # composite over a single stage is that stage): same prompt + output hashes.
    assert stage["prompt_version"] == stored.prompt_version
    assert stage["prompt_hash"] == stored.prompt_hash
    assert stage["raw_output_hash"] == stored.raw_output_hash


def test_single_call_tolerates_a_fence_and_hashes_the_unstripped_output(
    tmp_path: Path,
) -> None:
    # ATLAS-108: a fenced single-call response parses (the run succeeds), and
    # the hash invariant (gap 2) holds — raw_output_hash is over the FENCED
    # bytes the model sent, not the stripped object.
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    fenced_output = "```json\n" + proposal_json() + "\n```"
    result = run(repo, database, FakePlannerClient(fenced_output))

    assert result.status is PlanRunStatus.PROPOSED
    expected_hash = hashlib.sha256(fenced_output.encode("utf-8")).hexdigest()
    assert result.plan_run.raw_output_hash == expected_hash
    # The degenerate one-stage record also hashes the unstripped bytes.
    assert result.plan_run.generation_stages[0]["raw_output_hash"] == expected_hash


def test_at5_input_doc_shas_equal_ingested_head_shas(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    run(repo, database, FakePlannerClient(proposal_json()))

    stored = PlanRunRepo(database).list()[0]
    head_shas = {
        "PRODUCT.md": git(repo, "rev-parse", "HEAD:PRODUCT.md").strip(),
        "docs/atlas/plan.md": git(repo, "rev-parse", "HEAD:docs/atlas/plan.md").strip(),
    }
    assert stored.input_doc_shas == head_shas


# --- follow-up inbox as a separate plan input source (ATLAS-122) ------------

INBOX_STUB = (
    "<!-- atlas-source-comment-id: c-1 -->\n# Follow-up from ATLAS-9\n\n"
    "Investigate the retry seam.\n"
)
INBOX_PATH = "docs/planning/inbox/ATLAS-9-1.md"


def fixture_repo_with_inbox(tmp_path: Path) -> Path:
    return make_repo(
        tmp_path,
        {
            "PRODUCT.md": PRODUCT_MD,
            "docs/atlas/plan.md": PLAN_MD,
            INBOX_PATH: INBOX_STUB,
        },
    )


def test_committed_inbox_stub_is_planner_input(tmp_path: Path) -> None:
    # AT-1: a committed inbox stub reaches the planner's document payload and is
    # recorded in input_doc_shas under its inbox path — and gets there as the
    # SEPARATE inbox source, never via the §2.1 corpus globs (_GLOBS untouched).
    repo = fixture_repo_with_inbox(tmp_path)
    database = fresh_db(tmp_path)
    client = FakePlannerClient(proposal_json())

    result = run(repo, database, client)

    assert result.status is PlanRunStatus.PROPOSED
    # Visible to the model: the stub content is rendered into the prompt.
    assert client.last_prompt is not None
    assert "Investigate the retry seam." in client.last_prompt
    # Provenance: recorded under its inbox path.
    stored = PlanRunRepo(database).list()[0]
    assert INBOX_PATH in stored.input_doc_shas
    assert (
        stored.input_doc_shas[INBOX_PATH]
        == git(repo, "rev-parse", f"HEAD:{INBOX_PATH}").strip()
    )
    # The corpus globs stay pure: the stub is NOT a §2.1 corpus document.
    from atlas.planning.ingestion import collect_input_documents

    assert INBOX_PATH not in {doc.path for doc in collect_input_documents(repo)}


def test_empty_inbox_is_a_noop(tmp_path: Path) -> None:
    # AT-4: no stubs → the planner input is exactly today's corpus.
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    run(repo, database, FakePlannerClient(proposal_json()))

    stored = PlanRunRepo(database).list()[0]
    assert stored.input_doc_shas == {
        "PRODUCT.md": git(repo, "rev-parse", "HEAD:PRODUCT.md").strip(),
        "docs/atlas/plan.md": git(repo, "rev-parse", "HEAD:docs/atlas/plan.md").strip(),
    }


def test_similarity_threshold_override_is_recorded(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    run(repo, database, FakePlannerClient(proposal_json()), similarity_threshold=0.5)
    assert PlanRunRepo(database).list()[0].similarity_threshold == 0.5


# --- failure contract (gap 1) -----------------------------------------------


def test_dirty_tree_clean_exit_no_plan_run(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    (repo / "PRODUCT.md").write_text(PRODUCT_MD + "\nuncommitted\n", encoding="utf-8")
    with pytest.raises(DirtyInputError):
        run(repo, database, FakePlannerClient(proposal_json()))
    assert PlanRunRepo(database).list() == []


def test_missing_product_clean_exit_no_plan_run(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path, with_product=False)
    with pytest.raises(ProductNotFoundError, match="bootstrap"):
        run(repo, database, FakePlannerClient(proposal_json()))
    assert PlanRunRepo(database).list() == []


def test_no_documents_clean_exit(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"README.md": "# nothing in the input set\n"})
    database = fresh_db(tmp_path)
    with pytest.raises(NoInputDocumentsError):
        run(repo, database, FakePlannerClient(proposal_json()))
    assert PlanRunRepo(database).list() == []


def test_model_call_failure_clean_exit_no_plan_run(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    from atlas.planning.client import ModelCallError

    with pytest.raises(ModelCallError):
        run(repo, database, RaisingPlannerClient())
    assert PlanRunRepo(database).list() == []


def test_malformed_json_records_failed_plan_run(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    result = run(repo, database, FakePlannerClient("not valid json {{"))

    assert result.status is PlanRunStatus.FAILED
    assert result.diff is None
    reason = json.loads(result.failure_reason)
    assert reason["stage"] == "parse"

    stored = PlanRunRepo(database).list()[0]
    assert stored.status is PlanRunStatus.FAILED
    # A recorded failure is as auditable as a success: raw_output_hash kept.
    assert len(stored.raw_output_hash) == 64
    assert stored.input_doc_shas  # provenance chain intact
    assert stored.diff_summary == {}


def test_truncation_records_failed_plan_run_with_specific_reason(
    tmp_path: Path,
) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    partial = '{"epics": [], "tickets": [{"title": "cut off'
    result = run(repo, database, TruncatingPlannerClient(partial=partial))

    # Recorded, not a clean exit; named truncation, NOT a generic parse error.
    assert result.status is PlanRunStatus.FAILED
    reason = json.loads(result.failure_reason)
    assert reason["stage"] == "truncation"
    assert "max_tokens" in reason["error"]

    stored = PlanRunRepo(database).list()[0]
    assert stored.status is PlanRunStatus.FAILED
    assert stored.input_doc_shas  # provenance chain intact
    # raw_output_hash is taken over the partial output (hash parity: same
    # value the non-streaming path would have produced for this text).
    assert stored.raw_output_hash == hashlib.sha256(partial.encode()).hexdigest()


def test_gate_failure_records_failed_plan_run(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    # An unresolvable source_anchor fails gate 4 (resolved at gate time,
    # not parse time), so it is a recorded gate failure.
    bad = proposal_json(
        tickets=[_ticket(source_anchor="docs/atlas/plan.md#does-not-exist")]
    )
    result = run(repo, database, FakePlannerClient(bad))

    assert result.status is PlanRunStatus.FAILED
    reason = json.loads(result.failure_reason)
    assert reason["stage"] == "gates"
    assert any(f["gate"] == 4 for f in reason["failures"])

    stored = PlanRunRepo(database).list()[0]
    assert stored.status is PlanRunStatus.FAILED
    assert len(stored.raw_output_hash) == 64


def test_recorded_failure_is_not_loaded_as_proposed(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    run(repo, database, FakePlannerClient("garbage"))
    # apply (ATLAS-27) loads the latest proposed; a failed run is invisible.
    assert PlanRunRepo(database).latest_proposed() is None

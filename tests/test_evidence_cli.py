"""ATLAS-67: the `atlas evidence` CLI surface (pull / list / show).

Drives ``main(["evidence", ...], database=db, github_client=fake)`` against an
in-memory SQLite database seeded with the ATLAS product and a
``FakeGitHubClient`` replaying the recorded GitHub fixtures (workflow runs,
check runs, PR reviews, PR files, and the pull-request object with head.sha).
The command is a thin driver over the existing normaliser + ``ingest_*`` paths
and the append-only ``EvidenceRepo``, so these tests prove the WIRING — the
head_sha resolution, the three-source ingest, the read-back filtering, and the
clean-error exits — not the mapping computations (covered by
test_evidence_mapping) or the transport (test_github_client).

Falsifiable, with the wrong answer named:

- pull persists 6 checks + 4 reviews + 1 docs as commit-pinned system-tier rows
  and prints a per-source count; EXIT_OK;
- the CI/docs records are pinned to the HEAD SHA, the reviews to their own
  commit — the seeded-defect test (#6): wiring ``pr_number`` where ``head_sha``
  belongs, or dropping a source, makes a NAMED assertion fail;
- a missing token (live path) and an unknown PR each exit EXIT_PRECONDITION with
  one stderr line and no traceback;
- list filters by --commit/--type and --json parses; show prints one record and
  an unknown / non-UUID id is a clean precondition;
- the driver is Protocol-typed and runs fully offline under the fake.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from github_fakes import (
    FakeGitHubClient,
    load_array_fixture,
    load_fixture,
    load_object_fixture,
)
from test_models_validation import product_kwargs

from atlas.cli import EXIT_OK, EXIT_PRECONDITION, main
from atlas.core.enums import ActorType
from atlas.core.models import Product
from atlas.core.models.evidence import EvidenceType
from atlas.evidence.pull import drive_evidence_pull
from atlas.storage import Database, EvidenceRepo, ProductRepo

# The recorded PR's head SHA (pull_request.json) and the reviews' own commit
# (pr_reviews.json). They DIFFER on purpose: CI + docs evidence pins to the head
# SHA, reviews pin to their own commit, so the seeded-defect test can tell a
# head_sha wiring break (CI pinned to pr_number) from correct behaviour.
HEAD_SHA = "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b"
REVIEW_COMMIT = "4c64be37196acc1a3aaf752b5a50bbcf2475dba1"
PR_NUMBER = 11499
REPO = "cli/cli"

# Expected per-source counts from the recorded fixtures.
N_CHECKS = 6  # 3 workflow runs + 3 check runs
N_REVIEWS = 4  # all four reviews carry a commit_id (all commit-pinnable)
N_DOCS = 1  # one per-PR documentation record (two docs/ files touched)
N_TOTAL = N_CHECKS + N_REVIEWS + N_DOCS

_CI_TYPES = {
    EvidenceType.TEST_RESULT,
    EvidenceType.BUILD_RESULT,
    EvidenceType.LINT_RESULT,
    EvidenceType.COVERAGE_REPORT,
}


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


@pytest.fixture
def seeded_db(db: Database) -> Database:
    """``db`` with the ATLAS product the ingest paths attribute evidence to."""
    ProductRepo(db).add(Product(**product_kwargs()))
    return db


def make_fake(*, with_pr: bool = True) -> FakeGitHubClient:
    """A fixture-backed client replaying every recorded source. ``with_pr=False``
    leaves the pull-request unseeded, modelling an unknown PR (a 404)."""
    return FakeGitHubClient(
        workflow_runs=load_fixture("workflow_runs.json", "workflow_runs"),
        check_runs=load_fixture("check_runs.json", "check_runs"),
        pr_reviews=load_array_fixture("pr_reviews.json"),
        pr_files=load_array_fixture("pr_files.json"),
        pull_request=load_object_fixture("pull_request.json") if with_pr else None,
    )


def run_pull(db: Database, fake: FakeGitHubClient, *extra: str) -> int:
    return main(
        ["evidence", "pull", "--pr", str(PR_NUMBER), "--repo", REPO, *extra],
        database=db,
        github_client=fake,
    )


# --- pull: the happy path (criterion 1) -------------------------------------


def test_pull_persists_all_sources_and_prints_counts(
    seeded_db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_pull(seeded_db, make_fake(), "--json")
    assert code == EXIT_OK

    payload = json.loads(capsys.readouterr().out)
    assert payload["checks"] == N_CHECKS
    assert payload["reviews"] == N_REVIEWS
    assert payload["docs"] == N_DOCS
    assert payload["total"] == N_TOTAL
    assert len(payload["evidence_ids"]) == N_TOTAL

    rows = EvidenceRepo(seeded_db).list()
    assert len(rows) == N_TOTAL
    # Criterion 1: every persisted row is commit-pinned system-tier — it passed
    # the ATLAS-61 guard inside EvidenceRepo.add (else add would have raised).
    for record in rows:
        assert record.created_by_type is ActorType.SYSTEM
        assert record.commit_sha is not None
        assert record.external_run_id is not None
        assert record.payload_hash is not None


def test_pull_prints_human_per_source_counts(
    seeded_db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_pull(seeded_db, make_fake()) == EXIT_OK
    out = capsys.readouterr().out
    assert f"checks:  {N_CHECKS}" in out
    assert f"reviews: {N_REVIEWS}" in out
    assert f"docs:    {N_DOCS}" in out
    assert f"total:   {N_TOTAL}" in out


# --- the seeded-defect test (criterion 6) -----------------------------------


def test_ci_and_docs_records_are_pinned_to_head_sha_not_pr_number(
    seeded_db: Database,
) -> None:
    """SEEDED-DEFECT GUARD (#6): CI + docs evidence must pin to the HEAD SHA
    resolved from the pull-request object, reviews to their own commit. Break
    the wiring (pass ``pr_number`` where ``head_sha`` belongs, or drop a source)
    and this fails: the CI/docs ``commit_sha`` would become ``str(PR_NUMBER)``
    and the per-source counts would shift."""
    assert run_pull(seeded_db, make_fake()) == EXIT_OK
    rows = EvidenceRepo(seeded_db).list()

    ci = [r for r in rows if r.evidence_type in _CI_TYPES]
    reviews = [r for r in rows if r.evidence_type is EvidenceType.PR_REVIEW]
    docs = [r for r in rows if r.evidence_type is EvidenceType.DOCUMENTATION_UPDATE]

    assert len(ci) == N_CHECKS
    assert len(reviews) == N_REVIEWS
    assert len(docs) == N_DOCS

    # CI + docs pinned to the head SHA (never pr_number); reviews to their commit.
    assert {r.commit_sha for r in ci} == {HEAD_SHA}
    assert {r.commit_sha for r in docs} == {HEAD_SHA}
    assert {r.commit_sha for r in reviews} == {REVIEW_COMMIT}
    assert str(PR_NUMBER) not in {r.commit_sha for r in rows}


# --- pull: clean preconditions (criterion 2) --------------------------------


def test_pull_missing_token_is_clean_precondition(
    seeded_db: Database,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No injected client -> pull builds the LIVE GitHubRESTClient, which raises
    # MissingGitHubTokenError without a token. A clean one-line exit, no traceback.
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    code = main(
        ["evidence", "pull", "--pr", str(PR_NUMBER), "--repo", REPO],
        database=seeded_db,
    )
    assert code == EXIT_PRECONDITION
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "GITHUB_TOKEN" in captured.err
    assert "Traceback" not in captured.err
    # Nothing was persisted on the precondition exit.
    assert EvidenceRepo(seeded_db).list() == []


def test_pull_unknown_pr_is_clean_precondition(
    seeded_db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    # An unseeded PR models a 404: the fake raises GitHubAPIError, the command
    # maps it to a clean precondition exit (no traceback, nothing persisted).
    code = run_pull(seeded_db, make_fake(with_pr=False))
    assert code == EXIT_PRECONDITION
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "404" in captured.err
    assert "Traceback" not in captured.err
    assert EvidenceRepo(seeded_db).list() == []


def test_pull_missing_product_is_clean_precondition(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    # No ATLAS product seeded: a setup gap, the same clean precondition the
    # planning pipeline raises — not a plan/pull failure, never a traceback.
    code = run_pull(db, make_fake())
    assert code == EXIT_PRECONDITION
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ATLAS" in captured.err
    assert EvidenceRepo(db).list() == []


def test_pull_malformed_repo_is_clean_precondition(
    seeded_db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        ["evidence", "pull", "--pr", str(PR_NUMBER), "--repo", "noslash"],
        database=seeded_db,
        github_client=make_fake(),
    )
    assert code == EXIT_PRECONDITION
    assert "OWNER/REPO" in capsys.readouterr().err


# --- the Protocol-typed driver is offline (criterion 4) ---------------------


def test_drive_pull_is_offline_under_fake(seeded_db: Database) -> None:
    """The driver accepts any GitHubClient and runs with no network. Also pins
    the head_sha resolution: CI + docs are fetched/pinned by HEAD SHA, reviews
    and files by PR number (D3)."""
    fake = make_fake()
    product = ProductRepo(seeded_db).get_by_key("ATLAS")
    assert product is not None

    result = drive_evidence_pull(
        fake,
        "cli",
        "cli",
        PR_NUMBER,
        evidence_repo=EvidenceRepo(seeded_db),
        product_id=product.id,
        now=datetime.now(UTC),
    )
    assert (len(result.checks), len(result.reviews), len(result.docs)) == (
        N_CHECKS,
        N_REVIEWS,
        N_DOCS,
    )

    calls = {call[0]: call for call in fake.calls}
    assert calls["pull_request"] == ("pull_request", "cli", "cli", PR_NUMBER)
    # CI runs fetched by the resolved HEAD SHA...
    assert calls["workflow_runs"] == ("workflow_runs", "cli", "cli", HEAD_SHA)
    assert calls["check_runs"] == ("check_runs", "cli", "cli", HEAD_SHA)
    # ...reviews and files by the PR number.
    assert calls["pr_reviews"] == ("pr_reviews", "cli", "cli", PR_NUMBER)
    assert calls["pr_files"] == ("pr_files", "cli", "cli", PR_NUMBER)


# --- list (criterion 3) -----------------------------------------------------


def _evidence_ids(seeded_db: Database) -> list[str]:
    return [str(r.id) for r in EvidenceRepo(seeded_db).list()]


def test_list_prints_rows_and_filters(
    seeded_db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_pull(seeded_db, make_fake(), "--json") == EXIT_OK
    capsys.readouterr()  # drop pull output

    # All rows.
    assert main(["evidence", "list", "--json"], database=seeded_db) == EXIT_OK
    assert len(json.loads(capsys.readouterr().out)) == N_TOTAL

    # --commit: the head SHA selects CI + docs (6 + 1); the review commit selects
    # the four reviews.
    main(["evidence", "list", "--commit", HEAD_SHA, "--json"], database=seeded_db)
    assert len(json.loads(capsys.readouterr().out)) == N_CHECKS + N_DOCS
    main(["evidence", "list", "--commit", REVIEW_COMMIT, "--json"], database=seeded_db)
    assert len(json.loads(capsys.readouterr().out)) == N_REVIEWS

    # --type: pr_review selects the four reviews; documentation_update the one doc.
    main(["evidence", "list", "--type", "pr_review", "--json"], database=seeded_db)
    assert len(json.loads(capsys.readouterr().out)) == N_REVIEWS
    main(
        ["evidence", "list", "--type", "documentation_update", "--json"],
        database=seeded_db,
    )
    assert len(json.loads(capsys.readouterr().out)) == N_DOCS


def test_list_empty_is_clean(
    seeded_db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["evidence", "list"], database=seeded_db) == EXIT_OK
    assert "No evidence." in capsys.readouterr().out


# --- show (criterion 3) -----------------------------------------------------


def test_show_prints_one_record(
    seeded_db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_pull(seeded_db, make_fake()) == EXIT_OK
    capsys.readouterr()
    target = _evidence_ids(seeded_db)[0]

    assert main(["evidence", "show", target, "--json"], database=seeded_db) == EXIT_OK
    record = json.loads(capsys.readouterr().out)
    assert record["id"] == target
    # The full record carries raw_payload (unlike the list projection).
    assert "raw_payload" in record


def test_show_unknown_id_is_clean_precondition(
    seeded_db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["evidence", "show", str(uuid4())], database=seeded_db)
    assert code == EXIT_PRECONDITION
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no evidence" in captured.err


def test_show_non_uuid_is_clean_precondition(
    seeded_db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["evidence", "show", "not-a-uuid"], database=seeded_db)
    assert code == EXIT_PRECONDITION
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not a valid evidence id" in captured.err


# --- cold (uninitialised) database (ATLAS-130) ------------------------------
#
# Every fixture above calls db.create_all(); this section is the path none of
# them exercise -- a Database whose schema was NEVER created. The first repo
# access raises OperationalError: no such table, which without the
# _evidence_command guard escapes as a raw SQLAlchemy traceback (D7 violation).


@pytest.fixture
def cold_db(tmp_path: Path) -> Database:
    """A Database with NO schema created -- deliberately no ``create_all()``."""
    return Database(f"sqlite:///{tmp_path}/cold.db")


def _assert_clean_cold_db_exit(code: int, capsys: pytest.CaptureFixture[str]) -> None:
    """The shared cold-DB contract: EXIT_PRECONDITION, a one-line message naming
    the uninitialised database, and NO traceback / raw SQLAlchemy text leaking."""
    captured = capsys.readouterr()
    assert code == EXIT_PRECONDITION
    assert captured.out == ""
    assert "not initialised" in captured.err
    assert "run the database migrations" in captured.err
    # The wrong answer this guards: the raw OperationalError escaping.
    assert "Traceback" not in captured.err
    assert "OperationalError" not in captured.err
    assert "SQL" not in captured.err


def test_pull_cold_db_is_clean_precondition(
    cold_db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    """Criterion 1: `pull` against a never-migrated DB exits cleanly. A valid
    --repo (so the malformed-repo check passes) and an injected fake (so no
    token is needed) drive it to the first DB access, ``ProductRepo.get_by_key``
    -> ``no such table: products``."""
    code = run_pull(cold_db, make_fake())
    _assert_clean_cold_db_exit(code, capsys)


def test_list_cold_db_is_clean_precondition(
    cold_db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    """Criterion 2: `list` against a cold DB (``EvidenceRepo.list`` ->
    ``no such table: evidence``) exits cleanly."""
    code = main(["evidence", "list"], database=cold_db)
    _assert_clean_cold_db_exit(code, capsys)


def test_show_cold_db_is_clean_precondition(
    cold_db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    """Criterion 2: `show <id>` against a cold DB (a valid UUID so the parse
    passes, then ``EvidenceRepo.get`` -> ``no such table: evidence``) exits
    cleanly."""
    code = main(["evidence", "show", str(uuid4())], database=cold_db)
    _assert_clean_cold_db_exit(code, capsys)


def test_operational_guard_is_narrow(
    seeded_db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 4: the guard catches OperationalError, not bare Exception. A
    non-OperationalError raised inside an evidence command is NOT swallowed --
    it propagates rather than being mislabelled as an uninitialised database."""

    def _boom(self: EvidenceRepo) -> list[object]:
        raise RuntimeError("not a schema problem")

    monkeypatch.setattr(EvidenceRepo, "list", _boom)
    with pytest.raises(RuntimeError, match="not a schema problem"):
        main(["evidence", "list"], database=seeded_db)

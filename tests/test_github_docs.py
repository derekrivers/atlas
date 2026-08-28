"""Documentation-evidence normaliser tests (ATLAS-66): the docs/ predicate, the
NormalisedDocs PASSED record + synthesised pin triple + deterministic hash, the
absence-based None path, and that the recorded foreign bare-array file list
normalises through the fake transport.

Acceptance criteria 1, 2 (the absence guarantee), and 4. The docs/ predicate and
the empty check in normalise_pr_files are the seeded-defect targets (criterion
6): break either and a named test below fails -- inverting the empty check is the
real absence-based regression criterion 2 protects, more than a prefix typo.
"""

from __future__ import annotations

from typing import Any

from github_fakes import FakeGitHubClient, load_array_fixture

from atlas.core.enums import EvidenceStatus
from atlas.github import (
    GitHubClient,
    NormalisedDocs,
    normalise_pr_files,
    payload_hash,
)

# The recorded fixture is cli/cli#13479 (4 files: 2 under docs/, 2 not), so the
# predicate is exercised on a real mixed file list. Its real head SHA:
HEAD_SHA = "230498e917293e3b6d4e123f4608904cecb9eb8e"
FIXTURE_DOCS_PATHS = (
    "docs/release-process-deep-dive.md",
    "docs/releasing.md",
)


def _file(filename: str, **extra: Any) -> dict[str, Any]:
    """A raw GitHub PR-file payload (the shape the files endpoint returns)."""
    return {"filename": filename, "status": "modified", **extra}


# --- the pin triple + docs_paths + deterministic hash (criterion 1) ---------


def test_normalise_pr_files_with_docs_change_pins_triple_and_lists_paths() -> None:
    files = [
        _file("src/app.py"),  # not docs -> excluded
        _file("docs/guide.md"),
        _file("docs/index.md"),
        _file("README.md"),  # top-level, not under docs/ -> excluded
    ]
    docs = normalise_pr_files(files, head_sha=HEAD_SHA)
    assert docs is not None

    # always PASSED: the presence of a docs/ change is the signal
    assert docs.status == EvidenceStatus.PASSED
    # docs_paths are exactly the docs/ filenames, sorted -- nothing else
    assert docs.docs_paths == ("docs/guide.md", "docs/index.md")

    # the v2 pin permits append-only recovery beside a legacy docs:<head> row.
    expected_subset = {"files": [_file("docs/guide.md"), _file("docs/index.md")]}
    assert docs.external_run_id == f"docs:v2:{HEAD_SHA}"
    assert docs.commit_sha == HEAD_SHA
    assert docs.payload_hash == payload_hash(expected_subset)
    # raw_payload carries the docs subset only (not the excluded files)
    assert docs.raw_payload == expected_subset
    assert docs.source_uri is None
    # dedup key is (external_run_id, payload_hash), like the other shapes
    assert docs.dedup_key == (docs.external_run_id, docs.payload_hash)
    # a docs record is NOT a check/review: no name/reviewer/evidence_type field
    assert "evidence_type" not in NormalisedDocs.__dataclass_fields__
    assert "name" not in NormalisedDocs.__dataclass_fields__
    assert "reviewer" not in NormalisedDocs.__dataclass_fields__


def test_payload_hash_only_covers_the_docs_subset() -> None:
    # An unrelated NON-docs file changing must not churn the docs record's hash:
    # the hash is over the docs/ subset, so two PRs touching the same docs files
    # but different source files yield the same payload_hash.
    a = normalise_pr_files([_file("docs/x.md"), _file("src/a.py")], head_sha=HEAD_SHA)
    b = normalise_pr_files([_file("docs/x.md"), _file("src/b.py")], head_sha=HEAD_SHA)
    assert a is not None and b is not None
    assert a.payload_hash == b.payload_hash


def test_docs_paths_are_sorted_regardless_of_input_order() -> None:
    docs = normalise_pr_files(
        [_file("docs/zeta.md"), _file("docs/alpha.md")], head_sha=HEAD_SHA
    )
    assert docs is not None
    assert docs.docs_paths == ("docs/alpha.md", "docs/zeta.md")


# --- the absence-based None path (criterion 2; seeded-defect target #6) ------


def test_no_docs_change_returns_none() -> None:
    # No file under docs/ -> no record. The absence IS the signal: the wrong
    # answer this guards is manufacturing a record (or a FAILED one) here.
    # Inverting the empty check in normalise_pr_files fails THIS test.
    files = [_file("src/app.py"), _file("README.md"), _file("tests/test_app.py")]
    assert normalise_pr_files(files, head_sha=HEAD_SHA) is None


def test_empty_file_list_returns_none() -> None:
    assert normalise_pr_files([], head_sha=HEAD_SHA) is None


def test_doc_prefix_does_not_match_non_docs_tree() -> None:
    # The predicate is `docs/`, not a loose `doc` substring: a `doc/` or
    # `documentation/` path is NOT a docs/ change. Changing the predicate to
    # `doc/` (the seeded prefix defect) would wrongly match `doc/legacy.md` and
    # this test would fail.
    files = [_file("doc/legacy.md"), _file("documentation/old.md")]
    assert normalise_pr_files(files, head_sha=HEAD_SHA) is None


# --- against the recorded foreign bare-array payload (criterion 4) -----------


def test_recorded_pr_files_normalise_to_the_docs_subset() -> None:
    files = load_array_fixture("pr_files.json")
    # the recorded PR touches 4 files; exactly 2 are under docs/.
    assert len(files) > 2
    docs = normalise_pr_files(files, head_sha=HEAD_SHA)
    assert docs is not None
    assert docs.status == EvidenceStatus.PASSED
    assert docs.docs_paths == FIXTURE_DOCS_PATHS
    # the synthesised pin is over the head SHA, deterministic over the subset
    assert docs.external_run_id == f"docs:v2:{HEAD_SHA}"
    assert docs.commit_sha == HEAD_SHA
    assert docs.dedup_key == (docs.external_run_id, docs.payload_hash)


def test_fake_client_satisfies_protocol_and_replays_files() -> None:
    client = FakeGitHubClient(pr_files=load_array_fixture("pr_files.json"))
    assert isinstance(client, GitHubClient)  # the new method keeps the Protocol
    files = client.fetch_pr_files("cli", "cli", 13479)
    assert files
    # PR files are PR-scoped: the recorded call carries the PR number, not a SHA.
    assert client.calls[0] == ("pr_files", "cli", "cli", 13479)
    # end to end through the fake: raw -> normalised, no network.
    assert normalise_pr_files(files, head_sha=HEAD_SHA) is not None

"""ATLAS-21: document ingestion and the heading-anchor index.

Slug tests are transcribed from spec §2.3; the repository itself is the
reference corpus (a divergence between the algorithm and GitHub's real
slugs for our actual headings fails). Ingestion and dirty-state tests
run against fixture git repositories; the corpus tests build the index
from working-tree content directly so they hold even when the real
repo has uncommitted changes mid-session.
"""

import hashlib
import subprocess
from pathlib import Path

import pytest

from atlas.planning import (
    AnchorIndex,
    DirtyInputError,
    MalformedAnchorError,
    SourceDocument,
    UnknownAnchorError,
    UnknownDocumentError,
    collect_input_documents,
    slugify,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

ACCEPTED_ADR = """\
# ADR-0001: Test decision

## Status

Accepted

## Context

Some context.
"""

PROPOSED_ADR = ACCEPTED_ADR.replace("Accepted", "Proposed").replace("0001", "0002")


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


def input_fixture(tmp_path: Path) -> Path:
    return make_repo(
        tmp_path,
        {
            "PRODUCT.md": "# Product\n\n## Vision\n",
            "ARCHITECTURE.md": "# Architecture\n",
            "ROADMAP.md": "# Roadmap\n\n## Phase 0 — Foundation\n",
            "WORKFLOW.md": "# Workflow\n",
            "docs/decisions/0001-test-decision.md": ACCEPTED_ADR,
            "docs/decisions/0002-pondering.md": PROPOSED_ADR,
            "docs/atlas/b-spec.md": "# B spec\n\n## Setup\n\n## Setup\n",
            "docs/atlas/a-plan.md": "# A plan\n\n## Goals\n",
            "docs/archive/old.md": "# Old\n",
            "NOTES.md": "# Not an input\n",
        },
    )


def git_blob_sha(content: str) -> str:
    data = content.encode("utf-8")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


# --- slug algorithm, transcribed from §2.3 ----------------------------------


@pytest.mark.parametrize(
    ("heading", "slug"),
    [
        ("Heading", "heading"),
        ("MiXeD CaSe 123", "mixed-case-123"),
        ("CRUD: Create/Read", "crud-createread"),
        ("already-hyphened words", "already-hyphened-words"),
        # Em-dash strips, both surrounding spaces become hyphens: the
        # GitHub double-hyphen form.
        (
            "Phase 0 — Foundation and Mechanical Trust",
            "phase-0--foundation-and-mechanical-trust",
        ),
        ("!!!", ""),  # punctuation-only heading: empty slug, pinned
        (
            "Ticket transitions: one writer per state edge",
            "ticket-transitions-one-writer-per-state-edge",
        ),
    ],
    ids=repr,
)
def test_slugify_transcribed_cases(heading: str, slug: str) -> None:
    assert slugify(heading) == slug


def test_duplicate_headings_suffixed() -> None:
    index = AnchorIndex.build(
        [
            SourceDocument(
                path="doc.md",
                sha="abc",
                content="## Setup\n\ntext\n\n## Setup\n\n## Setup\n",
            )
        ]
    )
    assert index.slugs_for("doc.md") == ["setup", "setup-1", "setup-2"]


def test_fenced_headings_are_not_headings() -> None:
    content = "## Real\n\n```text\n## Fake heading\n```\n"
    index = AnchorIndex.build(
        [SourceDocument(path="doc.md", sha="abc", content=content)]
    )
    assert index.slugs_for("doc.md") == ["real"]


# --- anchor_choices: the select-from list for the prompt (ATLAS-111) ---------


def test_anchor_choices_lists_every_anchor_with_heading_in_order() -> None:
    # path#slug + heading for every indexed heading, in document then heading
    # order — the deterministic payload the prompt renders.
    index = AnchorIndex.build(
        [
            SourceDocument(path="a.md", sha="s1", content="# One\n\n## Two\n"),
            SourceDocument(path="b.md", sha="s2", content="### Three — chosen\n"),
        ]
    )
    assert index.anchor_choices() == [
        {"anchor": "a.md#one", "heading": "One"},
        {"anchor": "a.md#two", "heading": "Two"},
        {"anchor": "b.md#three--chosen", "heading": "Three — chosen"},
    ]


def test_anchor_choices_are_exactly_the_gate_4_validated_set() -> None:
    # One source of slugs: every anchor in the list resolves via the SAME index
    # gate 4 uses, to the same heading. The list the model selects from IS the
    # list gate 4 validates against — there is no second slug computation.
    index = AnchorIndex.build(
        [
            SourceDocument(
                path="docs/atlas/plan.md",
                sha="sha-x",
                content="## 4. The boundary\n\n### A. Split — chosen\n",
            )
        ]
    )
    for choice in index.anchor_choices():
        resolved = index.resolve(choice["anchor"])
        assert resolved.heading == choice["heading"]


# --- the repository as reference corpus -------------------------------------


def corpus_index() -> AnchorIndex:
    # Built from working-tree content directly: these tests pin the slug
    # algorithm against real headings and must hold mid-session, when
    # the real repo legitimately has uncommitted changes.
    documents = []
    for rel in (
        "docs/atlas/implementation-roadmap.md",
        "docs/atlas/symphony-integration.md",
    ):
        documents.append(
            SourceDocument(
                path=rel,
                sha="working-tree",
                content=(REPO_ROOT / rel).read_text(encoding="utf-8"),
            )
        )
    return AnchorIndex.build(documents)


def test_roadmap_em_dash_headings_match_github_form() -> None:
    slugs = corpus_index().slugs_for("docs/atlas/implementation-roadmap.md")
    assert "phase-0--foundation-and-mechanical-trust" in slugs
    assert "phase-1--knowledge-core-models-are-the-single-contract" in slugs


@pytest.mark.parametrize(
    "anchor",
    [
        "docs/atlas/symphony-integration.md#context-pack-delivery",
        "docs/atlas/symphony-integration.md#retry-and-failure-seam",
        "docs/atlas/symphony-integration.md#state-mapping",
        "docs/atlas/symphony-integration.md#workflow-contract",
        "docs/atlas/symphony-integration.md#boundary",
        "docs/atlas/symphony-integration.md#ticket-transitions-one-writer-per-state-edge",
    ],
)
def test_roadmaps_real_anchor_links_resolve(anchor: str) -> None:
    # The roadmap's Phase 8 entries reference these anchors today; the
    # index must resolve every one of them.
    resolved = corpus_index().resolve(anchor)
    assert resolved.heading


# --- ingestion (fixture git repos) -------------------------------------------


def test_collects_exactly_the_input_set_in_order(tmp_path: Path) -> None:
    repo = input_fixture(tmp_path)
    documents = collect_input_documents(repo)
    assert [doc.path for doc in documents] == [
        "PRODUCT.md",
        "ARCHITECTURE.md",
        "ROADMAP.md",
        "WORKFLOW.md",
        "docs/decisions/0001-test-decision.md",  # accepted only
        "docs/atlas/a-plan.md",
        "docs/atlas/b-spec.md",
    ]


def test_collection_is_deterministic(tmp_path: Path) -> None:
    repo = input_fixture(tmp_path)
    assert collect_input_documents(repo) == collect_input_documents(repo)


def test_blob_shas_are_real_git_blob_shas(tmp_path: Path) -> None:
    repo = input_fixture(tmp_path)
    documents = {doc.path: doc for doc in collect_input_documents(repo)}
    product = documents["PRODUCT.md"]
    assert product.sha == git_blob_sha(product.content)
    assert product.sha == git(repo, "rev-parse", "HEAD:PRODUCT.md").strip()


def test_proposed_adr_excluded(tmp_path: Path) -> None:
    repo = input_fixture(tmp_path)
    paths = [doc.path for doc in collect_input_documents(repo)]
    assert "docs/decisions/0002-pondering.md" not in paths


def test_domain_docs_included_when_present(tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path,
        {
            "PRODUCT.md": "# P\n",
            "docs/domain/billing.md": "# Billing\n",
            "docs/domain/auth.md": "# Auth\n",
        },
    )
    paths = [doc.path for doc in collect_input_documents(repo)]
    assert paths == ["PRODUCT.md", "docs/domain/auth.md", "docs/domain/billing.md"]


def test_dirty_tracked_input_fails_closed(tmp_path: Path) -> None:
    repo = input_fixture(tmp_path)
    (repo / "ROADMAP.md").write_text("# Roadmap (edited)\n", encoding="utf-8")
    with pytest.raises(DirtyInputError, match=r"ROADMAP\.md"):
        collect_input_documents(repo)


def test_untracked_input_fails_closed_no_fallback(tmp_path: Path) -> None:
    # Rejects the dry-run harness's "untracked-" pseudo-SHA fallback: a
    # fabricated SHA can never satisfy gate 4's resolve-at-recorded-SHA.
    repo = input_fixture(tmp_path)
    (repo / "docs" / "atlas" / "untracked.md").write_text("# New\n", encoding="utf-8")
    with pytest.raises(DirtyInputError, match=r"untracked\.md"):
        collect_input_documents(repo)


def test_dirty_non_input_files_are_ignored(tmp_path: Path) -> None:
    repo = input_fixture(tmp_path)
    (repo / "NOTES.md").write_text("# Edited non-input\n", encoding="utf-8")
    (repo / "scratch.txt").write_text("untracked non-input\n", encoding="utf-8")
    assert collect_input_documents(repo)  # no error


# --- resolution --------------------------------------------------------------


def test_resolve_positive_carries_sha_and_heading(tmp_path: Path) -> None:
    repo = input_fixture(tmp_path)
    documents = collect_input_documents(repo)
    index = AnchorIndex.build(documents)
    resolved = index.resolve("ROADMAP.md#phase-0--foundation")
    assert resolved.heading == "Phase 0 — Foundation"
    assert resolved.sha == index.input_doc_shas["ROADMAP.md"]


def test_resolve_unknown_document_typed(tmp_path: Path) -> None:
    index = AnchorIndex.build(collect_input_documents(input_fixture(tmp_path)))
    with pytest.raises(UnknownDocumentError, match=r"ghost\.md"):
        index.resolve("docs/atlas/ghost.md#anything")


def test_resolve_unknown_slug_typed(tmp_path: Path) -> None:
    index = AnchorIndex.build(collect_input_documents(input_fixture(tmp_path)))
    with pytest.raises(UnknownAnchorError, match="no-such-heading"):
        index.resolve("ROADMAP.md#no-such-heading")


def test_resolve_malformed_anchor_typed(tmp_path: Path) -> None:
    index = AnchorIndex.build(collect_input_documents(input_fixture(tmp_path)))
    with pytest.raises(MalformedAnchorError, match="heading-slug"):
        index.resolve("ROADMAP.md")


def test_staleness_is_sha_comparison(tmp_path: Path) -> None:
    # Spec §2.2 step 2: staleness = fresh ingestion compared against
    # recorded input_doc_shas.
    repo = input_fixture(tmp_path)
    index = AnchorIndex.build(collect_input_documents(repo))
    (repo / "ROADMAP.md").write_text("# Roadmap v2\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "edit roadmap")
    fresh = {doc.path: doc.sha for doc in collect_input_documents(repo)}
    assert fresh != index.input_doc_shas
    assert fresh["ROADMAP.md"] != index.input_doc_shas["ROADMAP.md"]

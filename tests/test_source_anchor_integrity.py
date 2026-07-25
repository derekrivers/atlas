"""ATLAS-196: source_anchor integrity over planning renders and store rows.

Fixture-driven, ATLAS_LIVE_TESTS=0 posture. Seeded-red probes were introduced
with ``assert 1 == 2`` first (B011), then replaced by the behaviour assertions
below.
"""

from pathlib import Path

import sqlalchemy as sa
from test_doc_linter import build_good_repo, codes, write
from test_models_validation import epic_kwargs, product_kwargs, ticket_kwargs

from atlas.core.models import Epic, Product, Ticket
from atlas.storage import Database, EpicRepo, ProductRepo, TicketRepo
from atlas.tools.doc_linter import (
    check_render_source_anchors,
    check_store_source_anchors,
    lint_repo,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
RENDER_HEADER = """\
# Render written only by `atlas apply` (ADR-0006/0007); do not hand-edit.
# plan_run_id: 00000000-0000-0000-0000-000000000001
# prompt_version: fixture
# ticket_key_high_water: 1
# epic_key_high_water: 1
"""
OLD_E11_ANCHOR = "docs/atlas/implementation-roadmap.md#epic-organisational-learning"
REPAIRED_E11_ANCHOR = (
    "docs/atlas/implementation-roadmap.md#epic-organisational-learning-e11"
)
OLD_ATLAS_192_ANCHOR = "README.md#atlas"
REPAIRED_ATLAS_192_ANCHOR = "ROADMAP.md#roadmapmd"


def write_ticket_render(root: Path, anchor: str, key: str = "ATLAS-1") -> None:
    write(
        root,
        "docs/planning/tickets.yaml",
        RENDER_HEADER
        + "tickets:\n"
        + f"- key: {key}\n"
        + f"  source_anchor: {anchor}\n",
    )


def test_current_planning_render_source_anchors_resolve() -> None:
    assert check_render_source_anchors(REPO_ROOT) == []


def test_render_anchor_to_renamed_heading_fails_src002(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    write(tmp_path, "docs/atlas/sample-plan.md", "# New heading\n\nIntent.\n")
    write_ticket_render(tmp_path, "docs/atlas/sample-plan.md#old-heading")

    findings = lint_repo(tmp_path)

    assert codes(findings) == {"SRC002"}
    assert findings[0].render() == (
        "docs/planning/tickets.yaml:8: SRC002 render ATLAS-1 source_anchor "
        "heading does not resolve: docs/atlas/sample-plan.md#old-heading"
    )


def test_render_anchor_to_document_outside_corpus_fails_src001(
    tmp_path: Path,
) -> None:
    build_good_repo(tmp_path)
    write_ticket_render(tmp_path, "README.md#test-repo")

    findings = lint_repo(tmp_path)

    assert codes(findings) == {"SRC001"}
    assert findings[0].render() == (
        "docs/planning/tickets.yaml:8: SRC001 render ATLAS-1 source_anchor "
        "document is outside indexed input set: README.md#test-repo"
    )


def _store_with_atlas_031m_before_repair(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    product = Product(**product_kwargs() | {"key": "ATLAS"})
    ProductRepo(database).add(product)
    epic = Epic(
        **epic_kwargs()
        | {
            "product_id": product.id,
            "key": "ATLAS-E11",
            "title": "Organisational Learning",
            "source_anchor": OLD_E11_ANCHOR,
        }
    )
    EpicRepo(database).add(epic)
    for key, title, anchor in (
        (
            "ATLAS-105",
            "Organisational memory search",
            OLD_E11_ANCHOR,
        ),
        (
            "ATLAS-106",
            "Continuous learning scheduler",
            OLD_E11_ANCHOR,
        ),
        (
            "ATLAS-192",
            "reconcile root documentation pointers",
            OLD_ATLAS_192_ANCHOR,
        ),
    ):
        TicketRepo(database).add(
            Ticket(
                **ticket_kwargs()
                | {
                    "product_id": product.id,
                    "epic_id": epic.id,
                    "key": key,
                    "title": title,
                    "source_anchor": anchor,
                }
            )
        )
    return database


def test_atlas_031m_four_store_anchor_regression_fires_before_repair(
    tmp_path: Path,
) -> None:
    build_good_repo(tmp_path)
    write(
        tmp_path,
        "docs/atlas/implementation-roadmap.md",
        "# Atlas Implementation Roadmap\n\n## Epic: Organisational Learning (E11)\n",
    )
    database = _store_with_atlas_031m_before_repair(tmp_path)

    findings = check_store_source_anchors(tmp_path, database)

    labels = {finding.message.split(" source_anchor", 1)[0] for finding in findings}
    assert labels == {
        "store epic ATLAS-E11",
        "store ticket ATLAS-105",
        "store ticket ATLAS-106",
        "store ticket ATLAS-192",
    }
    assert [finding.code for finding in findings].count("SRC002") == 3
    assert [finding.code for finding in findings].count("SRC001") == 1


def test_repaired_store_anchors_resolve(tmp_path: Path) -> None:
    build_good_repo(tmp_path)
    write(
        tmp_path,
        "docs/atlas/implementation-roadmap.md",
        "# Atlas Implementation Roadmap\n\n## Epic: Organisational Learning (E11)\n",
    )
    database = _store_with_atlas_031m_before_repair(tmp_path)
    with database.session() as session, session.begin():
        session.execute(
            sa.text("UPDATE epics SET source_anchor = :anchor"),
            {"anchor": REPAIRED_E11_ANCHOR},
        )
        session.execute(
            sa.text(
                "UPDATE tickets SET source_anchor = :anchor "
                "WHERE key IN ('ATLAS-105', 'ATLAS-106')"
            ),
            {"anchor": REPAIRED_E11_ANCHOR},
        )
        session.execute(
            sa.text(
                "UPDATE tickets SET source_anchor = :anchor WHERE key = 'ATLAS-192'"
            ),
            {"anchor": REPAIRED_ATLAS_192_ANCHOR},
        )

    assert check_store_source_anchors(tmp_path, database) == []

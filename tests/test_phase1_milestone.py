"""Phase 1 milestone test (ATLAS-19, implementation roadmap):

    "every entity round-trips through YAML and the database; the
    schema-drift linter fails on a seeded mismatched JSON example."

The three legs, named and in one module. The YAML and DB legs are
properties over every canonical model under the derandomised profile;
the linter leg asserts against the ATLAS-16 fixture helpers (reused,
not duplicated)."""

from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st
from model_strategies import STRATEGIES
from test_doc_linter import build_good_repo, codes
from test_doc_linter_v2 import write_fence
from test_property_roundtrips import REPO_BY_MODEL

from atlas.core.yaml_io import dump_entity, load_entity
from atlas.storage import Database
from atlas.tools.doc_linter import lint_repo, main


@given(data=st.data())
def test_milestone_every_entity_round_trips_through_yaml(
    data: st.DataObject,
) -> None:
    for model_cls, strategy in STRATEGIES.items():
        original = data.draw(strategy, label=model_cls.__name__)
        assert load_entity(model_cls, dump_entity(original)) == original


@settings(max_examples=20)
@given(data=st.data())
def test_milestone_every_entity_round_trips_through_database(
    data: st.DataObject,
) -> None:
    for model_cls, strategy in STRATEGIES.items():
        original = data.draw(strategy, label=model_cls.__name__)
        db = Database("sqlite:///:memory:")
        db.create_all()
        repo = REPO_BY_MODEL[model_cls](db)
        repo.add(original)
        assert repo.get(original.id) == original  # type: ignore[attr-defined]


def test_milestone_linter_fails_on_seeded_mismatched_example(
    tmp_path: Path,
) -> None:
    build_good_repo(tmp_path)
    # The seeded mismatch: a wrong-typed value in a mapped example.
    write_fence(tmp_path, '{"confidence": "high"}')
    findings = lint_repo(tmp_path)
    assert "JSN005" in codes(findings)
    assert main(["--repo", str(tmp_path)]) == 1
    # And the same fixture repo passes once the mismatch is repaired —
    # the failure is attributable to the seeded example, nothing else.
    write_fence(tmp_path, '{"confidence": 0.9}')
    assert lint_repo(tmp_path) == []

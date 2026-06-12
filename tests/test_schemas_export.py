"""ATLAS-16: schema export determinism and canonical-model completeness."""

from pathlib import Path

from pydantic import BaseModel

import atlas.core.models as models_pkg
from atlas.tools.schemas_export import (
    CANONICAL_MODELS,
    SCHEMAS_DIR,
    expected_schemas,
    export,
    main,
)


def read_all(root: Path) -> dict[str, bytes]:
    target = root / SCHEMAS_DIR
    return {path.name: path.read_bytes() for path in sorted(target.glob("*.json"))}


def test_exports_one_schema_per_canonical_model(tmp_path: Path) -> None:
    written = export(tmp_path)
    assert len(written) == len(CANONICAL_MODELS) == 10
    names = {path.stem for path in written}
    assert names == {model.__name__ for model in CANONICAL_MODELS}


def test_double_run_is_byte_identical(tmp_path: Path) -> None:
    export(tmp_path)
    first = read_all(tmp_path)
    export(tmp_path)
    assert read_all(tmp_path) == first


def test_output_is_lf_utf8_with_trailing_newline(tmp_path: Path) -> None:
    export(tmp_path)
    for name, content in read_all(tmp_path).items():
        text = content.decode("utf-8")
        assert "\r" not in text, name
        assert text.endswith("\n"), name


def test_export_removes_stale_files(tmp_path: Path) -> None:
    target = tmp_path / SCHEMAS_DIR
    target.mkdir(parents=True)
    stale = target / "RetiredModel.json"
    stale.write_text("{}\n", encoding="utf-8")
    export(tmp_path)
    assert not stale.exists()


def test_main_exits_zero_and_writes(tmp_path: Path) -> None:
    assert main(["--repo", str(tmp_path)]) == 0
    assert len(read_all(tmp_path)) == 10


def test_every_public_model_is_canonical() -> None:
    # A model added to atlas.core.models without joining CANONICAL_MODELS
    # would ship without a schema; this makes that impossible silently.
    public_models = {
        obj
        for name in models_pkg.__all__
        for obj in [getattr(models_pkg, name)]
        if isinstance(obj, type) and issubclass(obj, BaseModel)
    }
    assert public_models == set(CANONICAL_MODELS)


def test_expected_schemas_match_export_output(tmp_path: Path) -> None:
    export(tmp_path)
    on_disk = {
        name.removesuffix(".json"): content.decode("utf-8")
        for name, content in read_all(tmp_path).items()
    }
    assert on_disk == expected_schemas()

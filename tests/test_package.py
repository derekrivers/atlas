"""Smoke tests for ATLAS-2: package installs, version is single-sourced,
and planner prompt templates resolve as package data."""

import importlib.metadata
import importlib.resources

import atlas


def test_atlas_imports() -> None:
    assert atlas.__doc__ is not None


def test_version_matches_installed_metadata() -> None:
    # atlas/__init__.py is the single source of truth; pyproject.toml reads
    # it via hatchling's dynamic version. If they diverge, packaging broke.
    assert atlas.__version__ == importlib.metadata.version("atlas")


def test_planner_prompt_resolves_as_package_data() -> None:
    prompts = importlib.resources.files("atlas.planning") / "prompts"
    template = prompts / "planner-v1.0.0.md.j2"
    assert template.is_file()
    assert template.read_text(encoding="utf-8").strip()


def test_prompts_readme_resolves_as_package_data() -> None:
    readme = importlib.resources.files("atlas.planning") / "prompts" / "README.md"
    assert readme.is_file()
    assert readme.read_text(encoding="utf-8").strip()

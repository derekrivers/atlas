"""ATLAS-254: deterministic tiered local-validation contract.

The six named acceptance tests below pin each criterion from ATL-436.  The
planner is exercised only with supplied identities and paths: discovery and
execution are deliberately outside this boundary.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from atlas.cli import build_parser, main
from atlas.verification.validation_plan import (
    FULL_SWEEP_COMMANDS,
    MAX_CHANGED_PATHS,
    REGISTRY_VERSION,
    ValidationPlan,
    ValidationRegistry,
    calculate_validation_plan,
    load_registry_bytes,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "atlas" / "verification" / "validation_registry_v1.json"
BASE = "a" * 40
HEAD = "b" * 40


@pytest.fixture(scope="module")
def registry() -> ValidationRegistry:
    loaded = load_registry_bytes(REGISTRY_PATH.read_bytes())
    assert loaded.error is None
    assert loaded.registry is not None
    return loaded.registry


def _plan(
    registry: ValidationRegistry,
    *paths: str,
    requirements: tuple[str, ...] = (),
    tests: tuple[str, ...] = (),
    base: str = BASE,
    head: str = HEAD,
    expected_registry_version: str | None = None,
) -> ValidationPlan:
    return calculate_validation_plan(
        base=base,
        head=head,
        changed_paths=tuple(paths),
        ticket_requirements=requirements,
        ticket_tests=tests,
        registry=registry,
        expected_registry_version=expected_registry_version,
    )


@pytest.mark.parametrize(
    ("profile", "path", "requirement"),
    [
        ("python", "atlas/github/normaliser.py", None),
        ("static", "atlas/github/normaliser.py", None),
        ("documentation", "docs/atlas/evidence-pipeline.md", None),
        ("schema", "atlas/core/models/ticket.py", None),
        ("generated-client", "README.md", "generated-client"),
        ("ui", "apps/operator-ui/src/lib/staleness.ts", None),
        ("browser", "apps/operator-ui/src/lib/staleness.ts", None),
        ("full-sweep", "atlas/verification/validation_registry_v1.json", None),
    ],
)
def test_atlas_254_ac1_versioned_registry_maps_every_required_profile_with_reasons(
    registry: ValidationRegistry,
    profile: str,
    path: str,
    requirement: str | None,
) -> None:
    requirements = () if requirement is None else (requirement,)
    plan = _plan(registry, path, requirements=requirements)

    assert registry.version == REGISTRY_VERSION
    assert profile in plan.profiles
    assert any(reason.profile == profile for reason in plan.reasons)
    assert plan.commands


def test_atlas_254_ac1_combined_surfaces_follow_registry_order(
    registry: ValidationRegistry,
) -> None:
    plan = _plan(
        registry,
        "apps/operator-ui/src/lib/staleness.ts",
        "docs/atlas/evidence-pipeline.md",
        "atlas/github/normaliser.py",
    )

    assert plan.profiles == (
        "python",
        "static",
        "documentation",
        "ui",
        "browser",
    )
    assert not plan.full_sweep


def test_atlas_254_ac2_cli_emits_bounded_json_and_human_plans(
    capsys: pytest.CaptureFixture[str],
) -> None:
    common = [
        "validation-plan",
        "--base",
        BASE,
        "--head",
        HEAD,
        "--changed-path",
        "atlas/github/normaliser.py",
        "--ticket-requirement",
        "documentation",
    ]
    assert main([*common, "--json"]) == 0
    json_output = capsys.readouterr().out
    payload = json.loads(json_output)
    assert payload["base"] == BASE
    assert payload["head"] == HEAD
    assert payload["profiles"] == ["python", "static", "documentation"]
    assert len(json_output.encode()) < 256_000

    assert main(common) == 0
    human_output = capsys.readouterr().out
    assert "Complete local sweep: no" in human_output
    assert "Commands (run in order):" in human_output
    assert "uv run pytest" in human_output


def test_atlas_254_ac3_changed_and_ticket_tests_are_mandatory_and_not_excludable(
    registry: ValidationRegistry,
) -> None:
    changed_test = "tests/test_validation_plan.py"
    ticket_test = "apps/operator-ui/tests/e2e/app-shell.spec.ts"
    plan = _plan(
        registry,
        changed_test,
        requirements=("schema",),
        tests=(ticket_test,),
    )

    assert plan.test_targets == (ticket_test, changed_test)
    assert {"python", "schema", "browser"}.issubset(plan.profiles)
    reason_sources = {(reason.source_kind, reason.source) for reason in plan.reasons}
    assert ("changed_test", changed_test) in reason_sources
    assert ("ticket_test", ticket_test) in reason_sources
    assert ("ticket_requirement", "schema") in reason_sources

    with pytest.raises(SystemExit) as rejected:
        build_parser().parse_args(
            [
                "validation-plan",
                "--base",
                BASE,
                "--head",
                HEAD,
                "--changed-path",
                changed_test,
                "--exclude-profile",
                "python",
            ]
        )
    assert rejected.value.code == 2


@pytest.mark.parametrize(
    "case",
    [
        "unknown-path",
        "protected-path",
        "ambiguous-base",
        "registry-version-drift",
        "registry-digest-drift",
    ],
)
def test_atlas_254_ac4_uncertain_or_protected_inputs_select_complete_sweep(
    registry: ValidationRegistry,
    case: str,
) -> None:
    if case == "unknown-path":
        plan = _plan(registry, "unregistered/surface.xyz")
    elif case == "protected-path":
        plan = _plan(registry, ".github/workflows/ci.yml")
    elif case == "ambiguous-base":
        plan = _plan(registry, "README.md", base="origin/main")
    elif case == "registry-version-drift":
        plan = _plan(
            registry,
            "README.md",
            expected_registry_version="validation-registry/v0",
        )
    else:
        altered = REGISTRY_PATH.read_bytes() + b"\n"
        loaded = load_registry_bytes(altered)
        assert loaded.registry is None
        plan = calculate_validation_plan(
            base=BASE,
            head=HEAD,
            changed_paths=("README.md",),
            registry=loaded.registry,
            registry_error=loaded.error,
        )

    assert plan.full_sweep
    assert "full-sweep" in plan.profiles
    assert plan.commands == FULL_SWEEP_COMMANDS
    assert plan.fallback_reasons
    if case == "protected-path":
        assert plan.protected_surface_reasons


def test_atlas_254_ac5_plan_is_byte_stable_and_order_independent(
    registry: ValidationRegistry,
) -> None:
    paths = (
        "docs/atlas/evidence-pipeline.md",
        "tests/test_validation_plan.py",
        "atlas/github/normaliser.py",
    )
    first = _plan(
        registry,
        *paths,
        requirements=("schema", "documentation"),
        tests=("tests/test_cli.py", "tests/test_validation_plan.py"),
    )
    second = _plan(
        registry,
        *reversed(paths),
        requirements=("documentation", "schema"),
        tests=("tests/test_validation_plan.py", "tests/test_cli.py"),
    )

    assert first.json_bytes() == second.json_bytes()
    payload = first.payload()
    assert not {"created_at", "timestamp", "uuid", "model"}.intersection(payload)

    bounded = calculate_validation_plan(
        base=BASE,
        head=HEAD,
        changed_paths=tuple(
            f"unknown/{index:04d}.txt" for index in range(MAX_CHANGED_PATHS + 1)
        ),
        registry=registry,
    )
    assert bounded.changed_paths == ()
    assert len(bounded.json_bytes()) < 10_000


def test_atlas_254_ac6_canonical_docs_separate_local_confidence_from_ci_authority() -> (
    None
):
    required_docs = (
        REPO_ROOT / "AGENTS.md",
        REPO_ROOT
        / "docs"
        / "atlas"
        / "parallel-delivery-efficiency-and-integration-control.md",
        REPO_ROOT / "docs" / "runbooks" / "agent-ticket-prompt.md",
        REPO_ROOT / "docs" / "runbooks" / "local-development.md",
    )
    for path in required_docs:
        contents = path.read_text(encoding="utf-8").lower()
        assert "agent-tier" in contents, path
        assert "system-tier" in contents, path
        assert "ci" in contents, path


def test_plan_calculation_has_no_mutating_or_external_boundary(
    registry: ValidationRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = REPO_ROOT / "atlas" / "verification" / "validation_plan.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported = {
        name
        for node in ast.walk(tree)
        for name in (
            [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else [node.module or ""]
            if isinstance(node, ast.ImportFrom)
            else []
        )
    }
    assert not {
        "subprocess",
        "sqlite3",
        "sqlalchemy",
        "atlas.github",
        "atlas.linear",
        "atlas.storage",
    }.intersection(imported)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("validation planning attempted a mutation")

    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "touch", forbidden)
    monkeypatch.setattr(Path, "unlink", forbidden)
    plan = _plan(registry, "atlas/github/normaliser.py")

    assert plan.profiles == ("python", "static")
    assert not plan.full_sweep

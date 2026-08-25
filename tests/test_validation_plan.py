"""ATLAS-254: deterministic tiered local-validation contract.

The six named acceptance tests below pin each criterion from ATL-436.  The
planner is exercised only with supplied identities and paths: discovery and
execution are deliberately outside this boundary.
"""

from __future__ import annotations

import ast
import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from atlas.cli import build_parser, main
from atlas.verification.validation_plan import (
    FULL_SWEEP_COMMANDS,
    MAX_CHANGED_PATHS,
    REGISTRY_VERSION,
    ProtectedSurfaceReason,
    ValidationPlan,
    ValidationRegistry,
    calculate_validation_plan,
    load_registry_bytes,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "atlas" / "verification" / "validation_registry_v1.json"
BASE = "a" * 40
HEAD = "b" * 40


class RecordingReadOnlyGit:
    def __init__(
        self,
        diff_output: str,
        *,
        missing_tests: tuple[str, ...] = (),
        diff_returncode: int = 0,
    ) -> None:
        self.diff_output = diff_output
        self.missing_tests = set(missing_tests)
        self.diff_returncode = diff_returncode
        self.calls: list[tuple[Path, tuple[str, ...], Mapping[str, str] | None]] = []

    def __call__(
        self,
        cwd: Path,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        args = tuple(argv)
        self.calls.append((cwd, args, env))
        if args[0] == "diff":
            return subprocess.CompletedProcess(
                ["git", *args],
                self.diff_returncode,
                stdout=self.diff_output,
                stderr="diff failed" if self.diff_returncode else "",
            )
        assert args[:2] == ("cat-file", "-t")
        path = args[2].split(":", 1)[1]
        missing = path in self.missing_tests
        return subprocess.CompletedProcess(
            ["git", *args],
            1 if missing else 0,
            stdout="" if missing else "blob\n",
            stderr="missing" if missing else "",
        )


def _modified_diff(*paths: str) -> str:
    return "".join(f"M\0{path}\0" for path in paths)


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
        diff_verification="verified",
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
        (
            "browser",
            "apps/operator-ui/tests/component/operator-shell.browser.test.tsx",
            None,
        ),
        ("skill-contract", ".codex/skills/linear/SKILL.md", None),
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
        "tests/test_cli.py",
    )

    assert plan.profiles == (
        "python",
        "static",
        "documentation",
        "ui",
    )
    assert not plan.full_sweep


def test_atlas_254_ac2_cli_emits_bounded_json_and_human_plans(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = RecordingReadOnlyGit(_modified_diff("atlas/github/normaliser.py"))
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
        "--ticket-test",
        "tests/test_cli.py",
    ]
    assert main([*common, "--json"], git_runner=runner) == 0
    json_output = capsys.readouterr().out
    payload = json.loads(json_output)
    assert payload["base"] == BASE
    assert payload["head"] == HEAD
    assert payload["diff_verification"] == "verified"
    assert payload["profiles"] == ["python", "static", "documentation"]
    assert payload["commands"][0] == "uv run pytest tests/test_cli.py"
    assert "uv run pytest" not in payload["commands"]
    assert len(json_output.encode()) < 256_000

    assert main(common, git_runner=runner) == 0
    human_output = capsys.readouterr().out
    assert "Complete local sweep: no" in human_output
    assert "Commands (run in order):" in human_output
    assert "uv run pytest tests/test_cli.py" in human_output


def test_atlas_254_ac2_cli_fails_closed_on_diff_mismatch_and_keeps_rename_source(
    capsys: pytest.CaptureFixture[str],
) -> None:
    safe_new_path = "atlas/github/normaliser.py"
    protected_old_path = "pyproject.toml"
    runner = RecordingReadOnlyGit(f"R100\0{protected_old_path}\0{safe_new_path}\0")

    assert (
        main(
            [
                "validation-plan",
                "--base",
                BASE,
                "--head",
                HEAD,
                "--changed-path",
                safe_new_path,
                "--json",
            ],
            git_runner=runner,
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["diff_verification"] == "mismatch"
    assert payload["changed_paths"] == [safe_new_path, protected_old_path]
    assert payload["full_sweep"] is True
    assert "changed_path_mismatch" in {
        reason["code"] for reason in payload["fallback_reasons"]
    }
    assert any(
        reason["path"] == protected_old_path
        for reason in payload["protected_surface_reasons"]
    )


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
    ("ticket_test", "expected_profile"),
    [
        ("tests/test_cli.py", "python"),
        ("apps/operator-ui/tests/acceptance/app-shell.test.ts", "ui"),
        (
            "apps/operator-ui/tests/component/operator-shell.browser.test.tsx",
            "browser",
        ),
        ("apps/operator-ui/tests/e2e/app-shell.spec.ts", "browser"),
    ],
)
def test_atlas_254_ac3_ticket_tests_match_the_runner_that_executes_them(
    registry: ValidationRegistry,
    ticket_test: str,
    expected_profile: str,
) -> None:
    plan = _plan(registry, "README.md", tests=(ticket_test,))

    assert ticket_test in plan.test_targets
    assert expected_profile in plan.profiles
    assert not any(
        reason.code == "invalid_ticket_test" for reason in plan.fallback_reasons
    )


@pytest.mark.parametrize(
    "ticket_test",
    [
        "apps/operator-ui/tests/acceptance/example.spec.ts",
        "apps/operator-ui/tests/acceptance/example.test.tsx",
        "apps/operator-ui/tests/component/example.test.ts",
        "apps/operator-ui/tests/setup/example.spec.ts",
    ],
)
def test_atlas_254_ac3_unrunnable_test_shaped_paths_fail_closed(
    registry: ValidationRegistry,
    ticket_test: str,
) -> None:
    plan = _plan(registry, "README.md", tests=(ticket_test,))

    assert plan.full_sweep
    assert ticket_test not in plan.test_targets
    assert any(reason.code == "invalid_ticket_test" for reason in plan.fallback_reasons)


def test_atlas_254_ac3_cli_proves_explicit_ticket_test_exists_at_head(
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing_test = "tests/test_cli.py"
    runner = RecordingReadOnlyGit(
        _modified_diff("README.md"), missing_tests=(missing_test,)
    )

    assert (
        main(
            [
                "validation-plan",
                "--base",
                BASE,
                "--head",
                HEAD,
                "--changed-path",
                "README.md",
                "--ticket-test",
                missing_test,
                "--json",
            ],
            git_runner=runner,
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["full_sweep"] is True
    assert missing_test not in payload["test_targets"]
    assert "unverified_ticket_test" in {
        reason["code"] for reason in payload["fallback_reasons"]
    }


@pytest.mark.parametrize(
    "case",
    [
        "unknown-path",
        "shared-policy-path",
        "ambiguous-base",
        "git-diff-unavailable",
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
    elif case == "shared-policy-path":
        plan = _plan(registry, ".github/workflows/ci.yml")
    elif case == "ambiguous-base":
        plan = _plan(registry, "README.md", base="origin/main")
    elif case == "git-diff-unavailable":
        plan = calculate_validation_plan(
            base=BASE,
            head=HEAD,
            changed_paths=("README.md",),
            registry=registry,
            diff_verification="unavailable",
        )
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
            diff_verification="verified",
        )

    assert plan.full_sweep
    assert "full-sweep" in plan.profiles
    assert plan.commands == FULL_SWEEP_COMMANDS
    if case != "shared-policy-path":
        assert plan.fallback_reasons
    if case == "shared-policy-path":
        assert plan.protected_surface_reasons


@pytest.mark.parametrize(
    "path",
    [
        "atlas/verification/validation_plan.py",
        "atlas/orchestration/validation_plan_cli.py",
    ],
)
def test_atlas_254_ac4_validation_policy_implementation_is_protected(
    registry: ValidationRegistry,
    path: str,
) -> None:
    plan = _plan(registry, path)

    assert plan.full_sweep
    assert any(
        reason.path == path and reason.lane == "validation-policy"
        for reason in plan.protected_surface_reasons
    )


def test_atlas_072m_python_implementation_uses_only_focused_ticket_tests(
    registry: ValidationRegistry,
) -> None:
    plan = _plan(
        registry,
        "atlas/github/normaliser.py",
        tests=("tests/test_cli.py", "tests/test_cli.py"),
    )

    assert plan.test_targets == ("tests/test_cli.py",)
    assert plan.commands[0] == "uv run pytest tests/test_cli.py"
    assert "uv run pytest" not in plan.commands
    assert not plan.full_sweep


def test_atlas_072m_changed_python_test_is_an_automatic_exact_target(
    registry: ValidationRegistry,
) -> None:
    plan = _plan(registry, "tests/test_validation_plan.py")

    assert plan.test_targets == ("tests/test_validation_plan.py",)
    assert plan.commands[0] == "uv run pytest tests/test_validation_plan.py"
    assert not plan.full_sweep


def test_atlas_072m_python_without_provable_test_target_fails_closed(
    registry: ValidationRegistry,
) -> None:
    plan = _plan(registry, "atlas/github/normaliser.py")

    assert plan.full_sweep
    assert any(
        reason.code == "missing_python_test_target" for reason in plan.fallback_reasons
    )


def test_atlas_072m_unrelated_registry_test_does_not_cover_python_source(
    registry: ValidationRegistry,
) -> None:
    plan = _plan(
        registry,
        "atlas/github/normaliser.py",
        ".codex/skills/linear/SKILL.md",
    )

    assert plan.full_sweep
    assert any(
        reason.code == "missing_python_test_target" for reason in plan.fallback_reasons
    )


def test_atlas_072m_docs_only_stays_documentation_only(
    registry: ValidationRegistry,
) -> None:
    plan = _plan(registry, "docs/runbooks/local-development.md")

    assert plan.profiles == ("documentation",)
    assert plan.commands == ("uv run python -m atlas.tools.doc_linter",)
    assert not plan.full_sweep


def test_atlas_072m_linear_skill_markdown_uses_its_contract_test(
    registry: ValidationRegistry,
) -> None:
    plan = _plan(registry, ".codex/skills/linear/SKILL.md")

    assert plan.profiles == ("skill-contract",)
    assert plan.commands == ("uv run pytest tests/test_skill_linear.py",)
    assert not plan.full_sweep


def test_atlas_072m_workflow_protection_is_metadata_not_validation_breadth(
    registry: ValidationRegistry,
) -> None:
    plan = _plan(registry, "WORKFLOW.md")

    assert plan.profiles == ("documentation", "workflow-contract")
    assert plan.commands == (
        "uv run python -m atlas.tools.doc_linter",
        "uv run pytest tests/test_workflow_contract.py",
    )
    assert plan.protected_surface_reasons == (
        ProtectedSurfaceReason(
            lane="workflow-policy",
            path="WORKFLOW.md",
            rule_id="workflow-policy",
            detail="Workflow policy is a protected cross-cutting surface.",
        ),
    )
    assert not plan.full_sweep


def test_atlas_072m_migration_keeps_database_lane_and_focused_schema_tests(
    registry: ValidationRegistry,
) -> None:
    migration = "atlas/storage/migrations/versions/0034_planned_ci_pending_recovery.py"
    plan = _plan(registry, migration)

    assert plan.profiles == ("python", "static", "schema")
    assert (
        "uv run pytest tests/test_schemas_export.py tests/test_storage_schema.py"
        in plan.commands
    )
    assert not any("operator-ui" in command for command in plan.commands)
    assert plan.protected_surface_reasons[0].lane == "database-schema"
    assert plan.protected_surface_reasons[0].path == migration
    assert not plan.full_sweep


def test_atlas_072m_ui_source_and_e2e_ticket_test_are_cheap_plus_exact(
    registry: ValidationRegistry,
) -> None:
    target = "apps/operator-ui/tests/e2e/app-shell.spec.ts"
    plan = _plan(
        registry,
        "apps/operator-ui/src/App.tsx",
        tests=(target,),
    )

    assert plan.profiles == ("ui", "browser")
    assert plan.test_targets == (target,)
    assert plan.commands == (
        "npm --prefix apps/operator-ui ci",
        "npm --prefix apps/operator-ui run lint",
        "npm --prefix apps/operator-ui run typecheck",
        "npm --prefix apps/operator-ui run build:bundle",
        "apps/operator-ui/node_modules/.bin/playwright install chromium",
        "cd apps/operator-ui && ./node_modules/.bin/playwright test "
        "--config playwright.config.ts tests/e2e/app-shell.spec.ts",
    )
    assert not any(
        command.endswith(("test:browser", "test:e2e", "test:a11y"))
        for command in plan.commands
    )
    assert not plan.full_sweep


def test_atlas_072m_browser_config_without_exact_target_fails_closed(
    registry: ValidationRegistry,
) -> None:
    plan = _plan(registry, "apps/operator-ui/playwright.config.ts")

    assert plan.full_sweep
    assert any(
        reason.code == "missing_browser_test_target" for reason in plan.fallback_reasons
    )


@pytest.mark.parametrize(
    "path",
    [
        "atlas/orchestration/validation_plan_cli.py",
        "atlas/verification/validation_plan.py",
        "atlas/verification/validation_registry_v1.json",
        ".github/workflows/ci.yml",
        "apps/operator-ui/package-lock.json",
        "pyproject.toml",
        "uv.lock",
    ],
)
def test_atlas_072m_policy_and_shared_dependency_changes_keep_full_sweep(
    registry: ValidationRegistry,
    path: str,
) -> None:
    plan = _plan(registry, path)

    assert plan.full_sweep
    assert plan.commands == FULL_SWEEP_COMMANDS


def test_atlas_072m_explicit_full_sweep_requirement_is_authoritative(
    registry: ValidationRegistry,
) -> None:
    plan = _plan(registry, "README.md", requirements=("full-sweep",))

    assert plan.full_sweep
    assert plan.commands == FULL_SWEEP_COMMANDS
    assert any(
        reason.source_kind == "ticket_requirement" and reason.source == "full-sweep"
        for reason in plan.reasons
    )


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
        *paths,
        requirements=("schema", "documentation"),
        tests=(
            "tests/test_cli.py",
            "tests/test_validation_plan.py",
            "tests/test_cli.py",
        ),
    )
    second = _plan(
        registry,
        *reversed(paths),
        requirements=("documentation", "schema"),
        tests=("tests/test_validation_plan.py", "tests/test_cli.py"),
    )

    assert first.json_bytes() == second.json_bytes()
    assert first.changed_path_count == len(paths)
    payload = first.payload()
    assert not {"created_at", "timestamp", "uuid", "model"}.intersection(payload)

    bounded = calculate_validation_plan(
        base=BASE,
        head=HEAD,
        changed_paths=tuple(
            f"unknown/{index:04d}.txt" for index in range(MAX_CHANGED_PATHS + 1)
        ),
        registry=registry,
        diff_verification="verified",
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
    plan = _plan(
        registry,
        "atlas/github/normaliser.py",
        tests=("tests/test_cli.py",),
    )

    assert plan.profiles == ("python", "static")
    assert plan.commands[0] == "uv run pytest tests/test_cli.py"
    assert not plan.full_sweep


def test_cli_plan_proof_uses_only_read_only_git_and_performs_no_write(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    changed_path = "atlas/github/normaliser.py"
    ticket_test = "tests/test_cli.py"
    runner = RecordingReadOnlyGit(_modified_diff(changed_path))

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("validation-plan CLI attempted a filesystem write")

    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "touch", forbidden)
    monkeypatch.setattr(Path, "unlink", forbidden)
    assert (
        main(
            [
                "validation-plan",
                "--base",
                BASE,
                "--head",
                HEAD,
                "--changed-path",
                changed_path,
                "--ticket-test",
                ticket_test,
                "--json",
            ],
            git_runner=runner,
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["diff_verification"] == "verified"
    assert [call[1][0] for call in runner.calls] == ["diff", "cat-file"]
    for _cwd, argv, env in runner.calls:
        assert argv[0] in {"diff", "cat-file"}
        assert env == {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}

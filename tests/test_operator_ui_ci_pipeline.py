"""ATLAS-212: Operator UI CI pipeline contract.

The workflow is repo-owned executable policy, so these tests read the working
tree YAML directly. They pin the stage checks as independent CI jobs, keep the
existing Python sweep intact, and tie the local runbook to the commands a
developer needs when a check is red.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
LOCAL_RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "local-development.md"
OPERATOR_UI_PACKAGE_JSON = REPO_ROOT / "apps" / "operator-ui" / "package.json"
OPERATOR_UI_PACKAGE_LOCK = REPO_ROOT / "apps" / "operator-ui" / "package-lock.json"
E2E_SERVER = (
    REPO_ROOT / "apps" / "operator-ui" / "tests" / "e2e" / "atlas-api-server.ts"
)

OPERATOR_UI_STAGE_COMMANDS: dict[str, str] = {
    "lint-operator-ui-openapi": "npm run api:check",
    "lint-operator-ui": "npm run lint",
    "lint-operator-ui-types": "npm run typecheck",
    "test-operator-ui-acceptance": "npm run test:acceptance",
    "test-operator-ui-components": "npm run test:browser",
    "build-operator-ui": "npm run build:bundle",
    "test-operator-ui-e2e": "npm run test:e2e",
    "test-operator-ui-accessibility": "npm run test:a11y",
}

PYTHON_GATE_COMMANDS: dict[str, tuple[str, ...]] = {
    "test": ("uv sync --locked", "uv run pytest"),
    "lint": (
        "uv sync --locked",
        "uv run ruff check .",
        "uv run ruff format --check .",
    ),
    "lint-types": ("uv sync --locked", "uv run mypy atlas tests"),
    "lint-docs": (
        "uv sync --locked",
        "uv run python -m atlas.tools.doc_linter",
    ),
    "lint-imports": ("uv sync --locked", "uv run lint-imports"),
}

PLAYWRIGHT_VERSION = "1.62.0"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(_read_text(path))
    assert isinstance(data, dict)
    return cast(dict[str, Any], data)


def _load_ci() -> dict[Any, Any]:
    data = yaml.safe_load(_read_text(CI_YML))
    assert isinstance(data, dict)
    return data


def _jobs() -> dict[str, dict[str, Any]]:
    jobs = _load_ci()["jobs"]
    assert isinstance(jobs, dict)
    return cast(dict[str, dict[str, Any]], jobs)


def _job(name: str) -> dict[str, Any]:
    job = _jobs()[name]
    assert isinstance(job, dict)
    return job


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    steps = job["steps"]
    assert isinstance(steps, list)
    for step in steps:
        assert isinstance(step, dict)
    return cast(list[dict[str, Any]], steps)


def _run_steps(job: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        run for step in _steps(job) if isinstance((run := step.get("run")), str)
    )


def _stage_run_steps(job: dict[str, Any]) -> tuple[str, ...]:
    return tuple(run for run in _run_steps(job) if run.startswith("npm run "))


def _on_block() -> dict[str, Any]:
    data = _load_ci()
    on_block = data[True] if True in data else data["on"]
    assert isinstance(on_block, dict)
    return cast(dict[str, Any], on_block)


def test_operator_ui_ci_defines_each_stage_as_a_required_check() -> None:
    jobs = _jobs()

    for job_name, command in OPERATOR_UI_STAGE_COMMANDS.items():
        assert job_name in jobs
        job = _job(job_name)
        assert job["runs-on"] == "ubuntu-latest"
        assert "if" not in job, f"{job_name} must not be an advisory/skipped gate"
        assert "continue-on-error" not in job
        assert _stage_run_steps(job) == (command,)


def test_operator_ui_stage_jobs_run_on_ui_and_api_changes() -> None:
    on_block = _on_block()
    pull_request = on_block["pull_request"]
    push = on_block["push"]
    assert isinstance(pull_request, dict)
    assert isinstance(push, dict)

    assert "paths" not in pull_request
    assert "paths-ignore" not in pull_request
    assert "paths" not in push
    assert "paths-ignore" not in push

    for job_name in OPERATOR_UI_STAGE_COMMANDS:
        assert "if" not in _job(job_name)


def test_seeded_stage_failures_are_not_masked_or_collapsed_into_a_rollup() -> None:
    for job_name in OPERATOR_UI_STAGE_COMMANDS:
        job = _job(job_name)
        assert "continue-on-error" not in job
        runs = _run_steps(job)
        assert _stage_run_steps(job) == (OPERATOR_UI_STAGE_COMMANDS[job_name],)
        for run in runs:
            assert "|| true" not in run
            assert "npm run verify" not in run
            assert "./apps/operator-ui/scripts/ci" not in run


def test_python_gate_sweep_is_unchanged_and_unfiltered() -> None:
    jobs = _jobs()
    job_names = list(jobs)

    assert job_names.index("test") < job_names.index("lint")
    assert job_names.index("lint") < job_names.index("lint-types")
    assert job_names.index("lint-types") < job_names.index("lint-docs")
    assert job_names.index("lint-docs") < job_names.index("lint-imports")

    for job_name, commands in PYTHON_GATE_COMMANDS.items():
        job = _job(job_name)
        assert _run_steps(job) == commands
        assert "if" not in job
        assert "setup-node" not in str(job)


def test_playwright_install_uses_lockfile_pinned_local_binary() -> None:
    package_json = _load_json(OPERATOR_UI_PACKAGE_JSON)
    package_lock = _load_json(OPERATOR_UI_PACKAGE_LOCK)
    lock_packages = package_lock["packages"]
    assert isinstance(lock_packages, dict)

    package_dev_dependencies = package_json["devDependencies"]
    assert isinstance(package_dev_dependencies, dict)
    root_lock = lock_packages[""]
    assert isinstance(root_lock, dict)
    root_lock_dev_dependencies = root_lock["devDependencies"]
    assert isinstance(root_lock_dev_dependencies, dict)

    for package_name in ("@playwright/test", "playwright"):
        assert package_dev_dependencies[package_name] == PLAYWRIGHT_VERSION
        assert root_lock_dev_dependencies[package_name] == PLAYWRIGHT_VERSION
        locked_package = lock_packages[f"node_modules/{package_name}"]
        assert isinstance(locked_package, dict)
        assert locked_package["version"] == PLAYWRIGHT_VERSION

    playwright_package = lock_packages["node_modules/playwright"]
    playwright_core_package = lock_packages["node_modules/playwright-core"]
    assert isinstance(playwright_package, dict)
    assert isinstance(playwright_core_package, dict)
    assert playwright_core_package["version"] == PLAYWRIGHT_VERSION
    assert playwright_package["dependencies"] == {"playwright-core": PLAYWRIGHT_VERSION}

    workflow = _read_text(CI_YML)
    assert "npx playwright install" not in workflow
    for job_name in (
        "test-operator-ui-components",
        "test-operator-ui-e2e",
        "test-operator-ui-accessibility",
    ):
        runs = _run_steps(_job(job_name))
        install = "./node_modules/.bin/playwright install --with-deps chromium"
        assert install in runs
        assert runs.index("npm ci") < runs.index(install)
        assert runs.index(install) < runs.index(OPERATOR_UI_STAGE_COMMANDS[job_name])


def test_operator_ui_e2e_ci_uses_seeded_live_api_harness() -> None:
    package_scripts = _load_json(OPERATOR_UI_PACKAGE_JSON)["scripts"]
    assert isinstance(package_scripts, dict)
    assert package_scripts["test:e2e"] == (
        "playwright test --config playwright.config.ts --grep-invert @accessibility"
    )
    assert package_scripts["test:a11y"] == (
        "playwright test --config playwright.config.ts --grep @accessibility"
    )

    e2e_job_runs = _run_steps(_job("test-operator-ui-e2e"))
    assert "uv sync --locked" in e2e_job_runs
    assert "npm run test:e2e" in e2e_job_runs

    accessibility_job_runs = _run_steps(_job("test-operator-ui-accessibility"))
    assert "uv sync --locked" in accessibility_job_runs
    assert "npm run test:a11y" in accessibility_job_runs

    server = _read_text(E2E_SERVER)
    assert "atlas.tools.operator_ui_e2e_seed" in server
    assert "sqlite:///" in server
    assert "'atlas', 'api', 'serve'" in server
    assert "/api/v1/status" in server


def test_operator_ui_acceptance_ci_prepares_python_environment() -> None:
    runs = _run_steps(_job("test-operator-ui-acceptance"))
    assert "uv sync --locked" in runs
    assert runs.index("uv sync --locked") < runs.index("npm run test:acceptance")


def test_local_runbook_documents_every_operator_ui_ci_stage_command() -> None:
    runbook = _read_text(LOCAL_RUNBOOK)
    for command in OPERATOR_UI_STAGE_COMMANDS.values():
        assert command in runbook

    assert "npm ci" in runbook
    assert "./node_modules/.bin/playwright install chromium" in runbook
    assert "./apps/operator-ui/scripts/ci.sh" in runbook
    assert "./apps/operator-ui/scripts/ci-e2e.sh" in runbook

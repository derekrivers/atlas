"""Fresh-process coverage for the repository Hypothesis bootstrap profile."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest
from hypothesis import settings

_PROBE_ENV = "ATLAS_HYPOTHESIS_PROFILE_PROBE"
_PROBE_MARKER = "ATLAS_HYPOTHESIS_SETTINGS="
_DEFAULT_MARKER = "ATLAS_HYPOTHESIS_DEFAULTS="
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_CI_VARIABLES = (
    "CI",
    "__TOX_ENVIRONMENT_VARIABLE_ORIGINAL_CI",
    "TF_BUILD",
    "bamboo.buildKey",
    "BUILDKITE",
    "CIRCLECI",
    "CIRRUS_CI",
    "CODEBUILD_BUILD_ID",
    "GITHUB_ACTIONS",
    "GITLAB_CI",
    "HEROKU_TEST_RUN_ID",
    "TEAMCITY_VERSION",
)


def test_fresh_process_bootstrap_probe() -> None:
    if os.environ.get(_PROBE_ENV) != "1":
        pytest.skip("executed only by the fresh-process bootstrap tests")

    active = settings()
    print(
        _PROBE_MARKER
        + json.dumps(
            {
                "profile": settings.get_current_profile_name(),
                "max_examples": active.max_examples,
                "derandomize": active.derandomize,
                "deadline": None
                if active.deadline is None
                else active.deadline.total_seconds(),
            },
            sort_keys=True,
        )
    )


def _child_environment(*, ci: bool) -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop(_PROBE_ENV, None)
    environment.pop("HYPOTHESIS_PROFILE", None)
    for variable in _CI_VARIABLES:
        environment.pop(variable, None)
    if ci:
        environment["CI"] = "true"
    return environment


def _run_probe(profile: str | None, *, ci: bool) -> subprocess.CompletedProcess[str]:
    environment = _child_environment(ci=ci)
    environment[_PROBE_ENV] = "1"
    if profile is None:
        environment.pop("HYPOTHESIS_PROFILE", None)
    else:
        environment["HYPOTHESIS_PROFILE"] = profile

    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-s",
            f"{Path(__file__).relative_to(_REPOSITORY_ROOT)}::test_fresh_process_bootstrap_probe",
        ],
        cwd=_REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _probe_settings(profile: str | None, *, ci: bool) -> dict[str, object]:
    result = _run_probe(profile, ci=ci)
    assert result.returncode == 0, result.stdout + result.stderr
    payloads = [
        line.removeprefix(_PROBE_MARKER)
        for line in result.stdout.splitlines()
        if line.startswith(_PROBE_MARKER)
    ]
    assert len(payloads) == 1, result.stdout + result.stderr
    return cast(dict[str, object], json.loads(payloads[0]))


def _hypothesis_defaults(*, ci: bool) -> dict[str, object]:
    program = f"""
import json
from hypothesis import settings

active = settings.default
print({_DEFAULT_MARKER!r} + json.dumps({{
    "deadline": None if active.deadline is None else active.deadline.total_seconds(),
    "profile": settings.get_current_profile_name(),
}}, sort_keys=True))
"""
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=_REPOSITORY_ROOT,
        env=_child_environment(ci=ci),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payloads = [
        line.removeprefix(_DEFAULT_MARKER)
        for line in result.stdout.splitlines()
        if line.startswith(_DEFAULT_MARKER)
    ]
    assert len(payloads) == 1, result.stdout + result.stderr
    defaults = cast(dict[str, object], json.loads(payloads[0]))
    assert defaults["profile"] == ("ci" if ci else "default")
    return defaults


@pytest.mark.parametrize("ci", [False, True], ids=["local", "ci"])
def test_missing_profile_uses_deterministic_atlas_defaults(ci: bool) -> None:
    assert _probe_settings(None, ci=ci) == {
        "deadline": None,
        "derandomize": True,
        "max_examples": 50,
        "profile": "atlas",
    }


@pytest.mark.parametrize("ci", [False, True], ids=["local", "ci"])
def test_explicit_atlas_profile_is_honoured(ci: bool) -> None:
    assert _probe_settings("atlas", ci=ci) == {
        "deadline": None,
        "derandomize": True,
        "max_examples": 50,
        "profile": "atlas",
    }


@pytest.mark.parametrize("ci", [False, True], ids=["local", "ci"])
def test_explicit_explore_profile_is_honoured(ci: bool) -> None:
    assert _probe_settings("explore", ci=ci) == {
        "deadline": _hypothesis_defaults(ci=ci)["deadline"],
        "derandomize": False,
        "max_examples": 200,
        "profile": "explore",
    }


@pytest.mark.parametrize("ci", [False, True], ids=["local", "ci"])
@pytest.mark.parametrize("profile", ["", "unsupported-profile"])
def test_invalid_explicit_profile_fails_clearly(profile: str, ci: bool) -> None:
    result = _run_probe(profile, ci=ci)
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert f"Unsupported HYPOTHESIS_PROFILE={profile!r}" in output
    assert _PROBE_MARKER not in output


@pytest.mark.parametrize("ci", [False, True], ids=["local", "ci"])
def test_fresh_processes_do_not_inherit_an_earlier_selection(ci: bool) -> None:
    parent_environment = {key: os.environ.get(key) for key in _CI_VARIABLES}
    assert _probe_settings("explore", ci=ci)["profile"] == "explore"
    assert _run_probe("unsupported-profile", ci=ci).returncode != 0
    assert _probe_settings(None, ci=ci)["profile"] == "atlas"
    assert {key: os.environ.get(key) for key in _CI_VARIABLES} == parent_environment

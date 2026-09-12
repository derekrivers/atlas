"""Fresh-process coverage for the repository Hypothesis bootstrap profile."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import settings

_PROBE_ENV = "ATLAS_HYPOTHESIS_PROFILE_PROBE"
_PROBE_MARKER = "ATLAS_HYPOTHESIS_SETTINGS="
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


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


def _run_probe(profile: str | None) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
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


def _probe_settings(profile: str | None) -> dict[str, object]:
    result = _run_probe(profile)
    assert result.returncode == 0, result.stdout + result.stderr
    payloads = [
        line.removeprefix(_PROBE_MARKER)
        for line in result.stdout.splitlines()
        if line.startswith(_PROBE_MARKER)
    ]
    assert len(payloads) == 1, result.stdout + result.stderr
    return json.loads(payloads[0])


def test_missing_profile_uses_deterministic_atlas_defaults() -> None:
    assert _probe_settings(None) == {
        "deadline": None,
        "derandomize": True,
        "max_examples": 50,
        "profile": "atlas",
    }


def test_explicit_explore_profile_is_honoured() -> None:
    assert _probe_settings("explore") == {
        "deadline": 0.2,
        "derandomize": False,
        "max_examples": 200,
        "profile": "explore",
    }


@pytest.mark.parametrize("profile", ["", "unsupported-profile"])
def test_invalid_explicit_profile_fails_clearly(profile: str) -> None:
    result = _run_probe(profile)
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert f"Unsupported HYPOTHESIS_PROFILE={profile!r}" in output
    assert _PROBE_MARKER not in output


def test_fresh_processes_do_not_inherit_an_earlier_selection() -> None:
    assert _probe_settings("explore")["profile"] == "explore"
    assert _probe_settings(None)["profile"] == "atlas"

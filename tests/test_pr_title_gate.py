"""ATLAS-141: the PR-title provenance gate — a real ``(ATLAS-NN)`` key in every
PR title, enforced in CI (not just prose).

The gate IS the mapper (D1): ``scripts/check_pr_title.evaluate_title`` delegates
to ``atlas.verification.reports.parse_close_set`` and carries no private pattern
of its own — ``test_delegates_to_parse_close_set`` pins that so a divergent
regex cannot creep in later. Title-only (D2): the body cannot satisfy the gate.

Two surfaces are tested: the pure validator + CLI in ``scripts/check_pr_title``
(imported lazily via a fixture so the module's absence is a per-test red, not a
collection error), and the workflow shape in ``.github/workflows/ci.yml`` — read
from the working tree and parsed with ``yaml.safe_load``, following the
``tests.test_workflow_contract`` precedent.
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from types import ModuleType

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"


@pytest.fixture
def gate() -> ModuleType:
    """The module under test, imported at call time (red before it exists)."""
    return importlib.import_module("scripts.check_pr_title")


# --- AC1: the placeholder is rejected ----------------------------------------


def test_placeholder_rejected(gate: ModuleType) -> None:
    verdict = gate.evaluate_title("feat(at8): x (ATLAS-NN)")
    assert verdict.ok is False
    assert verdict.keys == ()


# --- AC2: a real key passes, normalised, in mapper order ----------------------


def test_real_key_passes(gate: ModuleType) -> None:
    verdict = gate.evaluate_title("feat(at8): x (ATLAS-142)")
    assert verdict.ok is True
    assert verdict.keys == ("ATLAS-142",)


# --- AC3: a keyless title is rejected (the literal #137 title) ----------------


def test_keyless_137_title_rejected(gate: ModuleType) -> None:
    verdict = gate.evaluate_title("updated workflow status and slug")
    assert verdict.ok is False
    assert verdict.keys == ()


# --- AC4: the gate delegates — no private pattern -----------------------------


def test_delegates_to_parse_close_set(
    gate: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A title that carries no key of its own — only the (patched) mapper makes
    # it pass, proving the verdict follows parse_close_set and nothing else.
    monkeypatch.setattr(gate, "parse_close_set", lambda title, body: ("ATLAS-1",))
    verdict = gate.evaluate_title("no key anywhere in this title")
    assert verdict.ok is True
    assert verdict.keys == ("ATLAS-1",)


# --- AC5: title-only — the body cannot satisfy the gate -----------------------


def test_signature_is_title_only(gate: ModuleType) -> None:
    params = list(inspect.signature(gate.evaluate_title).parameters.values())
    assert len(params) == 1
    assert params[0].kind in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )


# --- AC6: exit-code mapping, and the failure names the convention -------------


def test_exit_codes(gate: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    assert gate.main(["title with ATLAS-9"]) == 0
    assert gate.main(["no key here"]) == 1
    err = capsys.readouterr().err
    assert "(ATLAS-NN)" in err
    assert "real" in err.lower()
    assert "PR title" in err
    assert gate.main([]) == 2


# --- AC7: the workflow shape (job, `edited` trigger, title via env) -----------


def _load_ci() -> dict[object, object]:
    data = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_ci_workflow_shape() -> None:
    data = _load_ci()
    # PyYAML parses the bare key `on:` as the boolean True (YAML 1.1); accept
    # either spelling so the test does not depend on that quirk.
    on_block = data[True] if True in data else data["on"]
    assert isinstance(on_block, dict)
    assert "edited" in on_block["pull_request"]["types"]

    jobs = data["jobs"]
    assert isinstance(jobs, dict)
    assert "lint-pr-title" in jobs, "the gate job must live in ci.yml (AC7)"

    steps = jobs["lint-pr-title"]["steps"]
    run_steps = [s for s in steps if "check_pr_title" in str(s.get("run", ""))]
    assert len(run_steps) == 1, "exactly one step invokes the gate script"
    step = run_steps[0]

    env = step.get("env", {})
    assert any("github.event.pull_request.title" in str(v) for v in env.values()), (
        "the title must reach the script through an env var (D6)"
    )
    assert "github.event.pull_request.title" not in step["run"], (
        "the title must NOT be interpolated inline into the run body (D6)"
    )


# --- ATLAS-160: meta-label discipline (seeded red first, per B011 house rule) --


def test_reused_meta_label_fails_naming_collision_and_next_free() -> None:
    assert 1 == 2


def test_004m_reuse_rejected_regression() -> None:
    assert 1 == 2


def test_fresh_meta_label_passes() -> None:
    assert 1 == 2


def test_real_key_title_ignores_history() -> None:
    assert 1 == 2


def test_keyless_title_failure_suggests_next_free() -> None:
    assert 1 == 2


def test_no_history_flag_is_legacy_behaviour() -> None:
    assert 1 == 2


def test_lowercase_and_unpadded_meta_collide() -> None:
    assert 1 == 2


def test_keyless_history_subjects_contribute_nothing() -> None:
    assert 1 == 2


def test_sub_floor_label_rejected_as_burned() -> None:
    assert 1 == 2


def test_next_free_respects_title_usage_floor() -> None:
    assert 1 == 2


def test_subject_scan_overtakes_floor() -> None:
    assert 1 == 2


def test_history_flag_exit_codes() -> None:
    assert 1 == 2


def test_ci_workflow_history_plumbing() -> None:
    assert 1 == 2

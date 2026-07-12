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
#
# Meta labels (ATLAS-00xM) are NON-keys tagging records PRs (D7). With a
# --history file of merged squash subjects, the gate rejects a label already in
# that history (naming the colliding PR and the next free label), rejects
# labels below the burned floor (titles used 000M-016M but single-commit
# squashes dropped them from subjects — subjects carry only 002M/003m/004M),
# passes a fresh label, and appends the next-free suggestion to label-less
# failures. Without --history, behaviour is exactly the pre-ATLAS-160 gate
# (D8). Real-key behaviour is byte-identical: every pre-existing test above
# runs unmodified.

# The register's account of the real double assignment: #155 AND #158 both
# bearing ATLAS-004M (in live subject history the #155 label was lost to the
# single-commit squash — the fixture encodes the record, per the AC), plus the
# other real meta subjects and the keyless #169/#170-shape subjects the scan
# must tolerate.
HISTORY_004M_DOUBLE = [
    "ATLAS-002M - Build Phase Test Scripts and Add Reviewer Docs (#147)",
    "ATLAS-003m smoke b closeout (#154)",
    "ATLAS-004M - Record key-namespace burn 111..146 (#155)",
    "fix(dependencies): scope terminal-dependency rule to done sources "
    "(ATLAS-004M) (#158)",
    "planning: add durable-stub-anchors inbox stub (#169)",
    "planning: add meta-label-discipline inbox stub (#170)",
]

# The literal #169 shape: a keyless, label-less stub-landing title.
TITLE_169_MISSING_LABEL = "planning: add durable-stub-anchors inbox stub"


@pytest.fixture
def history_file(tmp_path: Path) -> Path:
    """HISTORY_004M_DOUBLE written one-subject-per-line, as CI produces it."""
    path = tmp_path / "merged-titles.txt"
    path.write_text("\n".join(HISTORY_004M_DOUBLE) + "\n", encoding="utf-8")
    return path


def test_reused_meta_label_fails_naming_collision_and_next_free(
    gate: ModuleType, capsys: pytest.CaptureFixture[str], history_file: Path
) -> None:
    """AC1: a reused label fails naming the colliding PR ref and the next free
    label. First 004M subject in the fixture is #155, so that is the named
    collision. Wrong answers: exit 0 (the pre-fix misparse pass), or a failure
    message that leaves the operator to enumerate history by hand."""
    code = gate.main(
        ["docs: another record (ATLAS-004M)", "--history", str(history_file)]
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "ATLAS-004M" in err
    assert "#155" in err
    assert "ATLAS-017M" in err


def test_004m_reuse_rejected_regression(gate: ModuleType, history_file: Path) -> None:
    """AC4, the seeded regression: PRE-fix this exact invocation exited 0 —
    parse_close_set misparsed ATLAS-004M as real key ATLAS-004 (no trailing
    boundary) and the gate passed the title as a real-key title, blind to any
    history. POST-fix it exits 1 as a meta collision."""
    code = gate.main(
        [
            "chore: reuse the burn record label (ATLAS-004M)",
            "--history",
            str(history_file),
        ]
    )
    assert code == 1


def test_fresh_meta_label_passes(
    gate: ModuleType, capsys: pytest.CaptureFixture[str], history_file: Path
) -> None:
    """AC2 (negative required by the render): the next free label passes.
    Wrong answer: rejecting all meta labels, which would re-break every
    records/stub-landing PR (the #164/#165/#169 failure shape)."""
    code = gate.main(
        ["planning: land some inbox stub (ATLAS-017M)", "--history", str(history_file)]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "PR title OK" in out
    assert "ATLAS-017M" in out


def test_real_key_title_ignores_history(
    gate: ModuleType, capsys: pytest.CaptureFixture[str], history_file: Path
) -> None:
    """AC2: a real-key title with --history present behaves exactly as the
    legacy gate — same exit code, same output line. Wrong answer: meta
    machinery leaking into the real-key path."""
    code = gate.main(["feat(at8): x (ATLAS-142)", "--history", str(history_file)])
    assert code == 0
    assert capsys.readouterr().out == "PR title OK — resolves ATLAS-142\n"


def test_keyless_title_failure_suggests_next_free(
    gate: ModuleType, capsys: pytest.CaptureFixture[str], history_file: Path
) -> None:
    """AC3: the label-less failure (#169 shape) carries the convention message
    AND the next-free suggestion, ending the guess-the-next-number round."""
    code = gate.main([TITLE_169_MISSING_LABEL, "--history", str(history_file)])
    assert code == 1
    err = capsys.readouterr().err
    assert "(ATLAS-NN)" in err
    assert "ATLAS-017M" in err


def test_no_history_flag_is_legacy_behaviour(
    gate: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """D8: history comes ONLY from --history. Without the flag there is no
    collision check and no suggestion — a meta-labeled title now fails with the
    plain convention message (post-boundary, the M-form resolves no key).
    Wrong answers: the script shelling out to git on its own, or the
    suggestion appearing with no history to derive it from."""
    code = gate.main(["docs: another record (ATLAS-004M)"])
    assert code == 1
    err = capsys.readouterr().err
    assert "(ATLAS-NN)" in err
    assert "ATLAS-017M" not in err
    assert "#155" not in err


def test_lowercase_and_unpadded_meta_collide(gate: ModuleType) -> None:
    """Labels compare numerically and case-insensitively: atlas-3m collides
    with the merged ATLAS-003m (real history — #154 is lowercase). Wrong
    answer: padding or case splitting the namespace into parallel counters."""
    verdict = gate.evaluate_meta(
        "docs: closeout addendum (atlas-3m)", HISTORY_004M_DOUBLE
    )
    assert verdict.state == "collision"
    assert verdict.label == "ATLAS-003M"
    assert verdict.colliding_ref == "#154"


def test_keyless_history_subjects_contribute_nothing(gate: ModuleType) -> None:
    """Merged history contains keyless subjects (#169/#170 landed label-less);
    they must not poison the scan. An empty-contribution history leaves the
    floor as the suggestion."""
    keyless = [
        "planning: add durable-stub-anchors inbox stub (#169)",
        "planning: apply PlanRun <32d81e84> (#171)",
    ]
    verdict = gate.evaluate_meta("no label here", keyless)
    assert verdict.state == "absent"
    assert verdict.next_free == "ATLAS-017M"


def test_sub_floor_label_rejected_as_burned(
    gate: ModuleType, capsys: pytest.CaptureFixture[str], history_file: Path
) -> None:
    """The floor covers the title/subject divergence: ATLAS-010M lived in PR
    #162's TITLE but never reached subject history, so a subjects-only scan
    would readmit it. Any label below _META_FLOOR is burned. Wrong answer:
    state='fresh' — which would recreate the double-assignment this ticket
    exists to end."""
    code = gate.main(
        ["docs: records note (ATLAS-010M)", "--history", str(history_file)]
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "ATLAS-010M" in err
    assert "floor" in err
    assert "ATLAS-017M" in err


def test_next_free_respects_title_usage_floor(gate: ModuleType) -> None:
    """Operator amendment: next-free = max(subject max + 1, _META_FLOOR).
    Subject max in the fixture is 004M, so max+1 alone would suggest 005M — a
    title-burned label (#156's title). The floor lifts it to 017M."""
    verdict = gate.evaluate_meta("no label here", HISTORY_004M_DOUBLE)
    assert verdict.next_free == "ATLAS-017M"
    assert gate._META_FLOOR == 17


def test_subject_scan_overtakes_floor(gate: ModuleType) -> None:
    """Subject scanning stays the live mechanism: once subjects carry numbers
    at or above the floor, max+1 wins and the floor is a fossil."""
    history = [*HISTORY_004M_DOUBLE, "docs: future record (ATLAS-020M) (#199)"]
    verdict = gate.evaluate_meta("no label here", history)
    assert verdict.next_free == "ATLAS-021M"


def test_history_flag_exit_codes(
    gate: ModuleType, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """D3 extended: an unreadable --history file and a dangling --history are
    usage errors (2), distinct from a gate failure (1)."""
    missing = tmp_path / "absent.txt"
    assert gate.main(["title (ATLAS-017M)", "--history", str(missing)]) == 2
    assert gate.main(["title (ATLAS-017M)", "--history"]) == 2
    capsys.readouterr()


def test_ci_workflow_history_plumbing() -> None:
    """AC7 extended: the lint-pr-title job fetches full history (the checkout
    carries fetch-depth: 0), pipes main's squash subjects to a file, and passes
    it via --history — while the D6 shape holds (title via env, never
    interpolated into the run body)."""
    data = _load_ci()
    jobs = data["jobs"]
    assert isinstance(jobs, dict)
    steps = jobs["lint-pr-title"]["steps"]

    checkout_steps = [s for s in steps if "actions/checkout" in str(s.get("uses", ""))]
    assert len(checkout_steps) == 1
    assert checkout_steps[0]["with"]["fetch-depth"] == 0

    run_steps = [s for s in steps if "check_pr_title" in str(s.get("run", ""))]
    assert len(run_steps) == 1
    run = run_steps[0]["run"]
    assert "git log --format=%s origin/main" in run
    assert "--history" in run
    assert "github.event.pull_request.title" not in run

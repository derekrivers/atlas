"""ATLAS-136 (F3 + A1 + A2): the operator preflight.

Fixtures use the in-memory fake `LinearClient` and a temp WORKFLOW.md, so the
whole suite runs with NO live project — `run_preflight` returns data, never
raises, and each check is exercised red-first. A separate block drives the
`atlas preflight` CLI with an injected fake client to pin the two-bucket exit
code (precondition vs recorded-failure vs all-pass).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

import pytest
from linear_fakes import InMemoryLinearClient

from atlas.cli import main
from atlas.core.models.ticket import TicketStatus
from atlas.linear.client import PROJECT_ID_ENV, TEAM_ID_ENV, WorkflowState
from atlas.linear.ownership import STATE_MAP_ENV, LinearStatusMap
from atlas.linear.preflight import (
    ASSIGNEE_ENV,
    Finding,
    ModelProbe,
    PreflightReport,
    ProbeResult,
    run_preflight,
)

PROJECT_ID = "proj-uuid"
PROJECT_SLUG = "atlas-team"
# The team id run_preflight passes to the team-scoped states fetch (ATLAS-148).
# The in-memory fake models one team, so the value only proves the threading.
TEAM_ID = "team-1"

# A complete, coherent baseline: every contract name (active and terminal lists
# plus Planned and the handoff names) exists as an exact-cased state, with a type the
# status map accepts. Each written status maps to exactly one state.
_BASELINE_STATES: tuple[WorkflowState, ...] = (
    WorkflowState("state-planned", "Planned", "unstarted"),
    WorkflowState("state-ready", "Ready for Agent", "unstarted"),
    WorkflowState("state-inprogress", "In Progress", "started"),
    WorkflowState("state-propen", "PR Open", "started"),
    WorkflowState("state-ci-pending", "CI Pending", "started"),
    WorkflowState("state-changes", "Changes Requested", "started"),
    WorkflowState("state-review", "Review Required", "started"),
    WorkflowState("state-needshuman", "Needs Human", "started"),
    WorkflowState("state-done", "Done", "completed"),
    WorkflowState("state-canceled", "Canceled", "canceled"),
    WorkflowState("state-duplicate", "Duplicate", "duplicate"),
)

_BASELINE_MAP: dict[str, TicketStatus] = {
    "state-planned": TicketStatus.PLANNED,
    "state-ready": TicketStatus.READY_FOR_AGENT,
    "state-inprogress": TicketStatus.IN_PROGRESS,
    "state-propen": TicketStatus.PR_OPEN,
    "state-ci-pending": TicketStatus.CI_PENDING,
    "state-changes": TicketStatus.CHANGES_REQUESTED,
    "state-review": TicketStatus.REVIEW_REQUIRED,
    "state-needshuman": TicketStatus.NEEDS_HUMAN_DECISION,
    "state-done": TicketStatus.DONE,
    "state-canceled": TicketStatus.REJECTED,
    "state-duplicate": TicketStatus.REJECTED,
}


def _workflow_md(
    tmp_path: Path,
    *,
    project_slug: str | None = PROJECT_SLUG,
    codex_command: str | None = None,
) -> Path:
    """Write a temp WORKFLOW.md whose front matter mirrors the canonical
    contract's tracker block (the only part C1-C5 read). A ``codex_command``
    adds a ``codex.command`` line for C6's model-parse."""
    path = tmp_path / "WORKFLOW.md"
    codex_block = f"codex:\n  command: {codex_command}\n" if codex_command else ""
    project_slug_line = (
        f'    project_slug: "{project_slug}"\n' if project_slug is not None else ""
    )
    path.write_text(
        "---\n"
        "tracker:\n"
        "  kind: linear\n"
        "  provider:\n"
        f"{project_slug_line}"
        "  active_states:\n"
        "    - Ready for Agent\n"
        "    - In Progress\n"
        "    - PR Open\n"
        "    - Changes Requested\n"
        "  terminal_states:\n"
        "    - Done\n"
        "    - Canceled\n"
        "    - Duplicate\n"
        f"{codex_block}"
        "---\n"
        "prompt body\n",
        encoding="utf-8",
    )
    return path


def _client(
    states: Iterable[WorkflowState] = _BASELINE_STATES,
    *,
    seed_project: bool = True,
    project_slug: str = PROJECT_SLUG,
) -> InMemoryLinearClient:
    client = InMemoryLinearClient(workflow_states=list(states))
    if seed_project:
        client.seed_project(PROJECT_ID, project_slug)
    return client


def _run(
    tmp_path: Path,
    *,
    client: InMemoryLinearClient | None = None,
    status_map: LinearStatusMap | None = None,
    project_slug: str | None = PROJECT_SLUG,
    allow_assignee: bool = False,
    check_model: bool = False,
    model_probe: ModelProbe | None = None,
    codex_command: str | None = None,
) -> tuple[Finding, ...]:
    report = run_preflight(
        workflow_md_path=_workflow_md(
            tmp_path, project_slug=project_slug, codex_command=codex_command
        ),
        client=client if client is not None else _client(),
        status_map=status_map
        if status_map is not None
        else LinearStatusMap(_BASELINE_MAP),
        team_id=TEAM_ID,
        project_id=PROJECT_ID,
        allow_assignee=allow_assignee,
        check_model=check_model,
        model_probe=model_probe,
    )
    return report.findings


def _failing(findings: Iterable[Finding], check_id: str) -> list[Finding]:
    return [f for f in findings if f.check_id == check_id and not f.ok]


@pytest.fixture(autouse=True)
def _no_assignee(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most checks assume LINEAR_ASSIGNEE is unset; C5 tests override it."""
    monkeypatch.delenv(ASSIGNEE_ENV, raising=False)


# --- AC3.1: a fully-correct fixture is all-pass -------------------------------


def test_ac3_1_correct_fixture_all_pass(tmp_path: Path) -> None:
    findings = _run(tmp_path)
    assert all(f.ok for f in findings), [f for f in findings if not f.ok]


@pytest.mark.parametrize("project_slug", [None, ""])
def test_c4_missing_or_empty_nested_project_slug_fails(
    tmp_path: Path, project_slug: str | None
) -> None:
    findings = _run(tmp_path, project_slug=project_slug)
    c4 = _failing(findings, "C4")
    assert len(c4) == 1
    assert (
        c4[0].message == "WORKFLOW.md tracker.provider.project_slug is missing or empty"
    )


# --- AC3.2: a case-only near-miss is a distinct case-drift finding (D3) --------


def test_ac3_2_case_drift_is_its_own_finding(tmp_path: Path) -> None:
    states = tuple(
        WorkflowState(s.id, "Pr Open" if s.name == "PR Open" else s.name, s.type)
        for s in _BASELINE_STATES
    )
    findings = _run(tmp_path, client=_client(states))
    drift = _failing(findings, "C1")
    assert len(drift) == 1
    assert "case" in drift[0].message.lower()
    assert "PR Open" in drift[0].message  # the contract spelling that has no match


# --- AC3.3: the placeholder project slug fails C4 -----------------------------


def test_ac3_3_placeholder_slug_fails(tmp_path: Path) -> None:
    findings = _run(
        tmp_path,
        client=_client(project_slug="atlas-REPLACE_ME"),
        project_slug="atlas-REPLACE_ME",
    )
    c4 = _failing(findings, "C4")
    assert len(c4) == 1
    assert "placeholder" in c4[0].message.lower()


# --- AC3.4: a UUID↔slug mismatch fails C4 (and an unresolved UUID, per the
# correction, is its own failing finding — never a crash, never a pass) --------


def test_ac3_4_slug_mismatch_fails(tmp_path: Path) -> None:
    findings = _run(tmp_path, client=_client(project_slug="some-other-slug"))
    c4 = _failing(findings, "C4")
    assert len(c4) == 1
    assert "some-other-slug" in c4[0].message


def test_ac3_4b_unresolved_project_id_is_failing_finding(tmp_path: Path) -> None:
    findings = _run(tmp_path, client=_client(seed_project=False))
    c4 = _failing(findings, "C4")
    assert len(c4) == 1
    assert "does not resolve" in c4[0].message


# --- AC3.5: a set LINEAR_ASSIGNEE fails C5 unless acknowledged ----------------


def test_ac3_5_assignee_fails_without_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ASSIGNEE_ENV, "user-123")
    findings = _run(tmp_path, allow_assignee=False)
    assert len(_failing(findings, "C5")) == 1


def test_ac3_5_assignee_passes_with_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ASSIGNEE_ENV, "user-123")
    findings = _run(tmp_path, allow_assignee=True)
    c5 = [f for f in findings if f.check_id == "C5"]
    assert len(c5) == 1
    assert c5[0].ok  # pass-with-note
    assert "allow" in c5[0].message.lower()


# --- AC3.6: two states mapped to one written status fail C3 (ambiguous) -------


def test_ac3_6_ambiguous_write_target_fails(tmp_path: Path) -> None:
    states = (
        *_BASELINE_STATES,
        WorkflowState("state-done-2", "Done Also", "completed"),
    )
    ambiguous = dict(_BASELINE_MAP)
    ambiguous["state-done-2"] = TicketStatus.DONE  # second state -> DONE
    findings = _run(
        tmp_path, client=_client(states), status_map=LinearStatusMap(ambiguous)
    )
    c3 = _failing(findings, "C3")
    assert len(c3) == 1
    assert "done" in c3[0].message.lower()


# --- C1 covers non-routed mirrored names (Planned plus handoffs) ----------------


def test_ac3_8_missing_handoff_state_fails(tmp_path: Path) -> None:
    # Drop "Review Required" entirely; it appears in neither active nor terminal
    # lists, so limiting C1 to the front-matter lists would leave this green and
    # the agent would stall silently at handoff.
    states = tuple(s for s in _BASELINE_STATES if s.name != "Review Required")
    # the status map must drop the now-missing state too, so C2/C3 don't mask C1
    pruned = {k: v for k, v in _BASELINE_MAP.items() if k != "state-review"}
    findings = _run(
        tmp_path, client=_client(states), status_map=LinearStatusMap(pruned)
    )
    c1 = _failing(findings, "C1")
    assert any("Review Required" in f.message for f in c1)


def test_ac3_8b_case_drifted_handoff_state_fails(tmp_path: Path) -> None:
    # "Review Needed" instead of "Review Required" — not even a case match, a
    # genuine rename; C1 must flag the missing contract name.
    states = tuple(
        WorkflowState(
            s.id, "Review Needed" if s.name == "Review Required" else s.name, s.type
        )
        for s in _BASELINE_STATES
    )
    findings = _run(tmp_path, client=_client(states))
    c1 = _failing(findings, "C1")
    assert any("Review Required" in f.message for f in c1)


def test_planned_is_required_by_name_and_as_a_unique_write_target(
    tmp_path: Path,
) -> None:
    states = tuple(state for state in _BASELINE_STATES if state.name != "Planned")
    pruned = {
        key: value for key, value in _BASELINE_MAP.items() if key != "state-planned"
    }

    findings = _run(
        tmp_path, client=_client(states), status_map=LinearStatusMap(pruned)
    )

    assert any("Planned" in finding.message for finding in _failing(findings, "C1"))
    assert any("planned" in finding.message for finding in _failing(findings, "C3"))


# --- AC3.7: the CLI exit code is the two-bucket contract ----------------------


def _cli_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the env the preflight CLI reads (status map + team id + project id).
    The client is injected, so no API key is needed. The team id is required
    since ATLAS-148: the states fetch is team-scoped."""
    state_map = {sid: status.value for sid, status in _BASELINE_MAP.items()}
    monkeypatch.setenv(STATE_MAP_ENV, json.dumps(state_map))
    monkeypatch.setenv(TEAM_ID_ENV, TEAM_ID)
    monkeypatch.setenv(PROJECT_ID_ENV, PROJECT_ID)
    monkeypatch.delenv(ASSIGNEE_ENV, raising=False)


def test_ac3_7_cli_exit_zero_on_all_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cli_env(monkeypatch)
    wf = _workflow_md(tmp_path)
    code = main(
        ["preflight", "--workflow-md", str(wf)],
        linear_client=_client(),
    )
    assert code == 0


def test_ac3_7_cli_exit_recorded_failure_on_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cli_env(monkeypatch)
    monkeypatch.setenv(ASSIGNEE_ENV, "user-123")  # one failing finding (C5)
    wf = _workflow_md(tmp_path)
    code = main(
        ["preflight", "--workflow-md", str(wf)],
        linear_client=_client(),
    )
    assert code == 1  # EXIT_RECORDED_FAILURE, distinct from precondition


def test_ac3_7_cli_exit_precondition_on_missing_state_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cli_env(monkeypatch)
    monkeypatch.delenv(STATE_MAP_ENV, raising=False)  # misconfigured environment
    wf = _workflow_md(tmp_path)
    code = main(
        ["preflight", "--workflow-md", str(wf)],
        linear_client=_client(),
    )
    assert code == 2  # EXIT_PRECONDITION


# --- C6: pinned-model reachability (Smoke A finding T1) -----------------------
#
# The probe is injected as a fake (`ModelProbe`), so NO test shells out to
# codex. The pure scorer decides reject-vs-pass from ProbeResult.output/error
# (there is no runner-supplied `ok`), which is exactly what makes the echo
# defence (AC2b) falsifiable here rather than only against a live codex.

# A representative command in the real double-quoted-inside-single-quotes form.
_CODEX_COMMAND = (
    "codex --config shell_environment_policy.inherit=core "
    "--config 'model=\"gpt-5.5\"' --config model_reasoning_effort=xhigh app-server"
)


def _probe(result: ProbeResult) -> ModelProbe:
    """A fake ModelProbe that always returns ``result`` (ignores the model)."""

    def probe(model: str) -> ProbeResult:
        return result

    return probe


def _never_probe(model: str) -> ProbeResult:
    """A ModelProbe that must never be called (guards the unparseable branch,
    which returns before probing)."""
    raise AssertionError("model probe must not run when the model is unparseable")


def _c6(findings: Iterable[Finding]) -> list[Finding]:
    return [f for f in findings if f.check_id == "C6"]


# AC2 — a reachable model (the computed answer present) passes.
def test_ac2_reachable_model_passes(tmp_path: Path) -> None:
    findings = _run(
        tmp_path,
        check_model=True,
        codex_command=_CODEX_COMMAND,
        model_probe=_probe(ProbeResult(output="The result is 42.")),
    )
    c6 = _c6(findings)
    assert len(c6) == 1
    assert c6[0].ok and not c6[0].skipped  # a valid answer must not score as fail


# AC2b — the critical echo defence: a prompt-echo with no computed 42 must NOT
# pass. Scoring a prompt-echo as reachable would reintroduce ATL-224 (an empty
# turn that looks clean) into the guard itself.
def test_ac2b_prompt_echo_does_not_pass(tmp_path: Path) -> None:
    echo = "Reply with only the result of 6 * 7, and nothing else."
    assert "42" not in echo  # the whole point: the answer is absent from the echo
    findings = _run(
        tmp_path,
        check_model=True,
        codex_command=_CODEX_COMMAND,
        model_probe=_probe(ProbeResult(output=echo)),
    )
    c6 = _c6(findings)
    assert len(c6) == 1
    assert not c6[0].ok  # NOT reachable
    assert not c6[0].skipped  # a reject, not a skip


# AC3 — a rejected model fails, surfacing the runner's raw error verbatim.
def test_ac3_rejected_model_fails_verbatim(tmp_path: Path) -> None:
    raw = (
        'ERROR: {"detail":"model gpt-5.5 is not supported when using Codex with '
        'a ChatGPT account"}'
    )
    findings = _run(
        tmp_path,
        check_model=True,
        codex_command=_CODEX_COMMAND,
        model_probe=_probe(ProbeResult(error=raw)),
    )
    c6 = _c6(findings)
    assert len(c6) == 1
    assert not c6[0].ok and not c6[0].skipped
    assert raw in c6[0].message  # verbatim, not paraphrased or swallowed


# AC3 (precedence) — an error wins even when a coincidental 42 is in the output.
def test_ac3_error_beats_coincidental_42(tmp_path: Path) -> None:
    findings = _run(
        tmp_path,
        check_model=True,
        codex_command=_CODEX_COMMAND,
        model_probe=_probe(ProbeResult(output="42", error="ERROR: rate limited")),
    )
    c6 = _c6(findings)
    assert len(c6) == 1
    assert not c6[0].ok  # reject decided before the 42 token is scored
    assert "rate limited" in c6[0].message


# AC4 — a missing codex binary is a skip (EXIT_PRECONDITION), not a fail.
def test_ac4_missing_binary_skips(tmp_path: Path) -> None:
    findings = _run(
        tmp_path,
        check_model=True,
        codex_command=_CODEX_COMMAND,
        model_probe=_probe(ProbeResult(binary_missing=True)),
    )
    c6 = _c6(findings)
    assert len(c6) == 1
    assert c6[0].skipped and c6[0].ok  # skip does not fail the run


# AC5 — a timeout is a skip, never "model rejected".
def test_ac5_timeout_skips(tmp_path: Path) -> None:
    findings = _run(
        tmp_path,
        check_model=True,
        codex_command=_CODEX_COMMAND,
        model_probe=_probe(ProbeResult(timed_out=True)),
    )
    c6 = _c6(findings)
    assert len(c6) == 1
    assert c6[0].skipped and c6[0].ok


# AC9 — an unauthenticated codex is a skip with a login-remediation message,
# never a rejection (the most likely first-run miscategorisation).
def test_ac9_auth_missing_skips_with_remediation(tmp_path: Path) -> None:
    findings = _run(
        tmp_path,
        check_model=True,
        codex_command=_CODEX_COMMAND,
        model_probe=_probe(ProbeResult(auth_missing=True)),
    )
    c6 = _c6(findings)
    assert len(c6) == 1
    assert c6[0].skipped and c6[0].ok
    assert "login" in c6[0].message.lower()


# AC6 — an unparseable model (no model= clause) is a skip, and the probe is
# never consulted (the classifier returns before probing).
def test_ac6_unparseable_model_skips_without_probing(tmp_path: Path) -> None:
    findings = _run(
        tmp_path,
        check_model=True,
        codex_command="codex --config approval_policy=never app-server",
        model_probe=_never_probe,
    )
    c6 = _c6(findings)
    assert len(c6) == 1
    assert c6[0].skipped and c6[0].ok
    assert "parseable" in c6[0].message.lower()


# AC7 — C6 is opt-in: without check_model it does not appear at all.
def test_ac7_c6_absent_without_flag(tmp_path: Path) -> None:
    findings = _run(
        tmp_path,
        codex_command=_CODEX_COMMAND,
        model_probe=_probe(ProbeResult(output="42")),
    )
    assert _c6(findings) == []


# AC8 — a skipped C6 does not fail the report: report.ok stays True, only
# report.skipped flips (guards the D1 semantics).
def test_ac8_skip_is_not_a_report_failure(tmp_path: Path) -> None:
    findings = _run(
        tmp_path,
        check_model=True,
        codex_command=_CODEX_COMMAND,
        model_probe=_probe(ProbeResult(binary_missing=True)),
    )
    report = PreflightReport(findings)
    assert report.ok is True  # skip is ok=True, so the all-clear holds
    assert report.skipped is True  # but the skip is surfaced


# --- C6 at the CLI: the D2 exit precedence (fail > skip > pass) ----------------


def _cli_check_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: ProbeResult,
) -> int:
    _cli_env(monkeypatch)
    wf = _workflow_md(tmp_path, codex_command=_CODEX_COMMAND)
    return main(
        ["preflight", "--workflow-md", str(wf), "--check-model"],
        linear_client=_client(),
        model_probe=_probe(result),
    )


def test_ac2_cli_exit_zero_on_reachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _cli_check_model(tmp_path, monkeypatch, ProbeResult(output="42")) == 0


def test_ac3_cli_exit_recorded_failure_on_reject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = _cli_check_model(tmp_path, monkeypatch, ProbeResult(error="ERROR: nope"))
    assert code == 1  # EXIT_RECORDED_FAILURE


def test_ac4_cli_exit_precondition_on_binary_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = _cli_check_model(tmp_path, monkeypatch, ProbeResult(binary_missing=True))
    assert code == 2  # EXIT_PRECONDITION, not EXIT_RECORDED_FAILURE


def test_ac5_cli_exit_precondition_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = _cli_check_model(tmp_path, monkeypatch, ProbeResult(timed_out=True))
    assert code == 2


def test_ac9_cli_exit_precondition_on_auth_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = _cli_check_model(tmp_path, monkeypatch, ProbeResult(auth_missing=True))
    assert code == 2

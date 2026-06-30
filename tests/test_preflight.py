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
from atlas.linear.client import PROJECT_ID_ENV, WorkflowState
from atlas.linear.ownership import STATE_MAP_ENV, LinearStatusMap
from atlas.linear.preflight import ASSIGNEE_ENV, Finding, run_preflight

PROJECT_ID = "proj-uuid"
PROJECT_SLUG = "atlas-team"

# A complete, coherent baseline: every contract name (active and terminal lists
# plus the two handoff names) exists as an exact-cased state, with a type the
# status map accepts. Each written status maps to exactly one state.
_BASELINE_STATES: tuple[WorkflowState, ...] = (
    WorkflowState("state-ready", "Ready for Agent", "unstarted"),
    WorkflowState("state-inprogress", "In Progress", "started"),
    WorkflowState("state-propen", "PR Open", "started"),
    WorkflowState("state-changes", "Changes Requested", "started"),
    WorkflowState("state-review", "Review Required", "started"),
    WorkflowState("state-needshuman", "Needs Human", "started"),
    WorkflowState("state-done", "Done", "completed"),
    WorkflowState("state-cancelled", "Cancelled", "cancelled"),
    WorkflowState("state-canceled", "Canceled", "cancelled"),
    WorkflowState("state-duplicate", "Duplicate", "cancelled"),
)

_BASELINE_MAP: dict[str, TicketStatus] = {
    "state-ready": TicketStatus.READY_FOR_AGENT,
    "state-inprogress": TicketStatus.IN_PROGRESS,
    "state-propen": TicketStatus.PR_OPEN,
    "state-changes": TicketStatus.CHANGES_REQUESTED,
    "state-review": TicketStatus.REVIEW_REQUIRED,
    "state-needshuman": TicketStatus.NEEDS_HUMAN_DECISION,
    "state-done": TicketStatus.DONE,
}


def _workflow_md(tmp_path: Path, *, project_slug: str = PROJECT_SLUG) -> Path:
    """Write a temp WORKFLOW.md whose front matter mirrors the canonical
    contract's tracker block (the only part the preflight reads)."""
    path = tmp_path / "WORKFLOW.md"
    path.write_text(
        "---\n"
        "tracker:\n"
        f'  project_slug: "{project_slug}"\n'
        "  active_states:\n"
        "    - Ready for Agent\n"
        "    - In Progress\n"
        "    - PR Open\n"
        "    - Changes Requested\n"
        "  terminal_states:\n"
        "    - Done\n"
        "    - Cancelled\n"
        "    - Canceled\n"
        "    - Duplicate\n"
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
    project_slug: str = PROJECT_SLUG,
    allow_assignee: bool = False,
) -> tuple[Finding, ...]:
    report = run_preflight(
        workflow_md_path=_workflow_md(tmp_path, project_slug=project_slug),
        client=client if client is not None else _client(),
        status_map=status_map
        if status_map is not None
        else LinearStatusMap(_BASELINE_MAP),
        project_id=PROJECT_ID,
        allow_assignee=allow_assignee,
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


# --- the DELTA: C1 covers the handoff names (Review Required / Needs Human) ----


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


# --- AC3.7: the CLI exit code is the two-bucket contract ----------------------


def _cli_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the env the preflight CLI reads (status map + project id). The client
    is injected, so no API key is needed."""
    state_map = {sid: status.value for sid, status in _BASELINE_MAP.items()}
    monkeypatch.setenv(STATE_MAP_ENV, json.dumps(state_map))
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

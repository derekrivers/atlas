"""Acceptance-chain driver tests (ATLAS-040M)."""

from __future__ import annotations

import importlib.util
import json
from argparse import Namespace
from collections.abc import Callable
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace
from typing import Any, cast

import pytest

from atlas.github import GitHubAPIError, GitHubCompareStatus
from atlas.orchestration import (
    PRAncestryStatus,
    PRIntegrationAssessment,
    PRIntegrationEligibility,
    PRIntegrationStatus,
    PRMergeabilityStatus,
)

SCRIPT = Path(__file__).parents[1] / "scripts" / "close_ticket.py"
SPEC = importlib.util.spec_from_file_location("close_ticket", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
close_ticket = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(close_ticket)

HEAD = "a" * 40
BASE = "b" * 40
NEW_HEAD = "c" * 40
NEW_BASE = "d" * 40


class Runner:
    def __init__(
        self,
        *,
        dirty: bool = False,
        failure: tuple[str, ...] | None = None,
        verified_head: str = HEAD,
    ) -> None:
        self.calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []
        self.dirty = dirty
        self.failure = failure
        self.verified_head = verified_head

    def __call__(
        self, command: tuple[str, ...], **kwargs: Any
    ) -> CompletedProcess[str]:
        command = tuple(command)
        self.calls.append((command, kwargs))
        if command == ("git", "status", "--porcelain"):
            return CompletedProcess(command, 0, "dirty.py\n" if self.dirty else "", "")
        if command == ("git", "remote", "get-url", "origin"):
            return CompletedProcess(command, 0, "git@github.com:acme/atlas.git\n", "")
        if command == self.failure:
            return CompletedProcess(command, 7, "partial\n", "failed\n")
        if "verify" in command and "--json" in command:
            return CompletedProcess(
                command,
                0,
                '{"status": "passed", "head_commit": "' + self.verified_head + '"}\n',
                "",
            )
        if command[-4:] == ("pm", "sync", "--once", "-v"):
            return CompletedProcess(
                command,
                0,
                "many skip lines\npm sync: completed=1\n",
                "",
            )
        return CompletedProcess(command, 0, "", "")


def args(**overrides: object) -> Namespace:
    values: dict[str, object] = {
        "pr": 248,
        "repo": "acme/atlas",
        "operator": "operator",
    }
    values.update(overrides)
    return Namespace(**values)


def merged(*, is_merged: bool = True, head_commit: str = HEAD) -> SimpleNamespace:
    return SimpleNamespace(
        pull_request={
            "merged": is_merged,
            "title": "Close ATLAS-200",
            "body": None,
        },
        head_commit=head_commit,
    )


def assessment(
    *,
    head_sha: str = HEAD,
    base_sha: str = BASE,
    head_ref: str = "feature/close",
    base_ref: str = "main",
    head_repository: str = "acme/atlas",
    base_repository: str = "acme/atlas",
    pr_state: str = "open",
    pr_draft: bool = False,
    pr_merged: bool = False,
    eligibility: PRIntegrationEligibility = PRIntegrationEligibility.ELIGIBLE,
    ancestry: PRAncestryStatus = PRAncestryStatus.CURRENT,
    mergeability: PRMergeabilityStatus = PRMergeabilityStatus.MERGEABLE,
    integration_status: PRIntegrationStatus = PRIntegrationStatus.CURRENT,
    ahead_by: int | None = 1,
    behind_by: int | None = 0,
    compare_status: GitHubCompareStatus | None = GitHubCompareStatus.AHEAD,
    merge_base_sha: str | None = BASE,
) -> PRIntegrationAssessment:
    return PRIntegrationAssessment(
        owner="acme",
        repo="atlas",
        pr_number=248,
        pr_title="Close ATLAS-200",
        pr_body=None,
        pr_state=pr_state,
        pr_draft=pr_draft,
        pr_merged=pr_merged,
        head_ref=head_ref,
        head_sha=head_sha,
        head_repository=head_repository,
        base_ref=base_ref,
        base_sha=base_sha,
        base_repository=base_repository,
        merge_base_sha=merge_base_sha,
        ahead_by=ahead_by,
        behind_by=behind_by,
        compare_status=compare_status,
        mergeability=mergeability,
        ancestry=ancestry,
        eligibility=eligibility,
        integration_status=integration_status,
    )


def ineligible_assessment(
    eligibility: PRIntegrationEligibility,
    *,
    pr_state: str = "open",
    pr_draft: bool = False,
    pr_merged: bool = False,
    head_repository: str = "acme/atlas",
    base_repository: str = "acme/atlas",
    base_ref: str = "main",
) -> PRIntegrationAssessment:
    return assessment(
        pr_state=pr_state,
        pr_draft=pr_draft,
        pr_merged=pr_merged,
        head_repository=head_repository,
        base_repository=base_repository,
        base_ref=base_ref,
        eligibility=eligibility,
        ancestry=PRAncestryStatus.INDETERMINATE,
        integration_status=PRIntegrationStatus.INELIGIBLE,
        ahead_by=None,
        behind_by=None,
        compare_status=None,
        merge_base_sha=None,
    )


class AssessmentSequence:
    def __init__(self, *results: PRIntegrationAssessment | GitHubAPIError) -> None:
        self.results = list(results or [assessment(), assessment()])
        self.calls: list[tuple[str, int]] = []

    def __call__(self, repo: str, pr: int) -> PRIntegrationAssessment:
        self.calls.append((repo, pr))
        result = self.results.pop(0) if self.results else assessment()
        if isinstance(result, GitHubAPIError):
            raise result
        return result


def assert_no_acceptance_actions(runner: Runner, pauses: list[str]) -> None:
    commands = [call[0] for call in runner.calls]
    assert pauses == []
    assert not any("evidence" in command for command in commands)
    assert not any("confirm" in command for command in commands)
    assert not any("verify" in command for command in commands)
    assert ("git", "checkout", "main") not in commands
    assert ("git", "pull") not in commands
    assert not any("alembic" in command for command in commands)
    assert not any("pm" in command for command in commands)


def assert_no_post_merge_actions(runner: Runner) -> None:
    commands = [call[0] for call in runner.calls]
    assert ("git", "checkout", "main") not in commands
    assert ("git", "pull") not in commands
    assert not any("alembic" in command for command in commands)
    assert not any("pm" in command for command in commands)


def recording_pause(pauses: list[str]) -> Callable[[str], str]:
    def pause(prompt: str) -> str:
        pauses.append(prompt)
        return ""

    return pause


def drive(
    runner: Runner,
    *,
    context: SimpleNamespace | None = None,
    statuses: list[tuple[str, str]] | None = None,
    assessments: AssessmentSequence | None = None,
) -> int:
    resolved_assessments = assessments or AssessmentSequence()
    return cast(
        int,
        close_ticket.drive(
            args(),
            environ={"GITHUB_TOKEN": "secret"},
            run_command=runner,
            pause=lambda _: "",
            resolve_assessment=resolved_assessments,
            resolve_context=lambda _repo, _pr: context or merged(),
            read_statuses=lambda _keys: statuses or [("ATLAS-200", "done")],
        ),
    )


@pytest.mark.parametrize(
    ("arguments", "environment", "dirty", "message"),
    [
        (args(), {}, False, "GITHUB_TOKEN"),
        (
            args(operator=None),
            {"GITHUB_TOKEN": "secret"},
            False,
            "operator identity",
        ),
        (
            args(),
            {"GITHUB_TOKEN": "secret"},
            True,
            "working tree is dirty",
        ),
    ],
)
def test_preconditions_fail_before_any_chain_command(
    arguments: Namespace,
    environment: dict[str, str],
    dirty: bool,
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = Runner(dirty=dirty)
    code = close_ticket.drive(
        arguments,
        environ=environment,
        run_command=runner,
    )
    assert code == 2
    assert message in capsys.readouterr().err
    assert not any(call[0][:3] == ("uv", "run", "atlas") for call in runner.calls)


def test_initial_freshness_runs_after_local_preflight_before_evidence() -> None:
    runner = Runner()
    events: list[tuple[str, tuple[str, ...] | str]] = []

    def run_command(command: tuple[str, ...], **kwargs: Any) -> CompletedProcess[str]:
        events.append(("command", command))
        return runner(command, **kwargs)

    def resolve(repo: str, pr: int) -> PRIntegrationAssessment:
        events.append(("assessment", f"{repo}#{pr}"))
        if sum(event[0] == "assessment" for event in events) == 1:
            assert runner.calls == [
                (
                    ("git", "status", "--porcelain"),
                    {
                        "cwd": close_ticket.REPO_ROOT,
                        "text": True,
                        "capture_output": True,
                        "check": False,
                    },
                )
            ]
            assert not any("evidence" in call[0] for call in runner.calls)
        return assessment()

    code = close_ticket.drive(
        args(),
        environ={"GITHUB_TOKEN": "secret"},
        run_command=run_command,
        pause=lambda _: "",
        resolve_assessment=resolve,
        resolve_context=lambda _repo, _pr: merged(),
        read_statuses=lambda _keys: [("ATLAS-200", "done")],
    )

    assert code == 0
    assert events[:2] == [
        ("command", ("git", "status", "--porcelain")),
        ("assessment", "acme/atlas#248"),
    ]
    evidence_index = next(
        index
        for index, event in enumerate(events)
        if event[0] == "command"
        and isinstance(event[1], tuple)
        and "evidence" in event[1]
    )
    assert evidence_index > 1


@pytest.mark.parametrize(
    ("initial", "status", "eligibility", "rebase_command"),
    [
        (
            assessment(
                ancestry=PRAncestryStatus.BEHIND,
                integration_status=PRIntegrationStatus.BEHIND,
                ahead_by=0,
                behind_by=2,
                compare_status=GitHubCompareStatus.BEHIND,
                merge_base_sha=HEAD,
            ),
            "behind",
            "eligible",
            True,
        ),
        (
            assessment(
                ancestry=PRAncestryStatus.DIVERGED,
                integration_status=PRIntegrationStatus.DIVERGED,
                ahead_by=2,
                behind_by=3,
                compare_status=GitHubCompareStatus.DIVERGED,
                merge_base_sha="1" * 40,
            ),
            "diverged",
            "eligible",
            True,
        ),
        (
            assessment(
                mergeability=PRMergeabilityStatus.CONFLICTED,
                integration_status=PRIntegrationStatus.CONFLICTED,
            ),
            "conflicted",
            "eligible",
            True,
        ),
        (
            assessment(
                mergeability=PRMergeabilityStatus.INDETERMINATE,
                integration_status=PRIntegrationStatus.INDETERMINATE,
            ),
            "indeterminate",
            "eligible",
            False,
        ),
        (
            ineligible_assessment(
                PRIntegrationEligibility.DRAFT,
                pr_draft=True,
            ),
            "ineligible",
            "draft",
            False,
        ),
        (
            ineligible_assessment(
                PRIntegrationEligibility.FORK,
                head_repository="contributor/atlas",
            ),
            "ineligible",
            "fork_head",
            False,
        ),
        (
            ineligible_assessment(
                PRIntegrationEligibility.NON_MAIN,
                base_ref="develop",
            ),
            "ineligible",
            "non_main",
            False,
        ),
        (
            ineligible_assessment(
                PRIntegrationEligibility.CLOSED,
                pr_state="closed",
            ),
            "ineligible",
            "closed",
            False,
        ),
        (
            ineligible_assessment(
                PRIntegrationEligibility.MERGED,
                pr_state="closed",
                pr_merged=True,
            ),
            "ineligible",
            "merged",
            False,
        ),
    ],
)
def test_initial_fail_closed_classes_block_before_writes_or_prompt(
    initial: PRIntegrationAssessment,
    status: str,
    eligibility: str,
    rebase_command: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = Runner()
    pauses: list[str] = []
    assessments = AssessmentSequence(initial)

    code = close_ticket.drive(
        args(),
        environ={"GITHUB_TOKEN": "secret"},
        run_command=runner,
        pause=recording_pause(pauses),
        resolve_assessment=assessments,
    )

    assert code == 1
    assert assessments.calls == [("acme/atlas", 248)]
    assert_no_acceptance_actions(runner, pauses)
    error = capsys.readouterr().err
    assert f"integration_status: {status}" in error
    assert f"eligibility: {eligibility}" in error
    recovery = "atlas pr rebase prepare --pr 248 --repo acme/atlas"
    assert (recovery in error) is rebase_command


def test_initial_assessment_api_failure_blocks_before_writes_or_prompt(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = Runner()
    pauses: list[str] = []

    code = close_ticket.drive(
        args(),
        environ={"GITHUB_TOKEN": "secret"},
        run_command=runner,
        pause=recording_pause(pauses),
        resolve_assessment=AssessmentSequence(
            GitHubAPIError("GitHub API request failed: timeout")
        ),
    )

    assert code == 1
    assert_no_acceptance_actions(runner, pauses)
    error = capsys.readouterr().err
    assert "integration_status: indeterminate" in error
    assert "timeout" in error


def test_origin_repo_is_defaulted() -> None:
    runner = Runner()
    arguments = args(repo=None)
    code = close_ticket.drive(
        arguments,
        environ={"GITHUB_TOKEN": "secret"},
        run_command=runner,
        pause=lambda _: "",
        resolve_assessment=AssessmentSequence(),
        resolve_context=lambda _repo, _pr: merged(),
        read_statuses=lambda _keys: [("ATLAS-200", "done")],
    )
    assert code == 0
    evidence = next(call[0] for call in runner.calls if "evidence" in call[0])
    assert evidence[-1] == "acme/atlas"


def test_unmerged_pr_refuses_verify_after_affirmative_pause(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = Runner()
    pauses: list[str] = []

    def affirmative_pause(prompt: str) -> str:
        pauses.append(prompt)
        return "yes"

    code = close_ticket.drive(
        args(),
        environ={"GITHUB_TOKEN": "secret"},
        run_command=runner,
        pause=affirmative_pause,
        resolve_assessment=AssessmentSequence(),
        resolve_context=lambda _repo, _pr: merged(is_merged=False),
    )
    assert code == 1
    assert pauses
    verify_calls = [call[0] for call in runner.calls if "verify" in call[0]]
    assert len(verify_calls) == 1
    assert "--json" in verify_calls[0]
    assert "is not merged" in capsys.readouterr().err


def test_confirm_inherits_parent_stdio() -> None:
    runner = Runner()
    assert drive(runner) == 0
    command, kwargs = next(call for call in runner.calls if "confirm" in call[0])
    assert "confirm" in command
    assert kwargs["capture_output"] is False
    assert "stdin" not in kwargs
    assert "stdout" not in kwargs
    assert "stderr" not in kwargs


def test_nonzero_step_aborts_and_names_resume_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    verify = (
        "uv",
        "run",
        "atlas",
        "verify",
        "--pr",
        "248",
        "--repo",
        "acme/atlas",
    )
    runner = Runner(failure=verify)
    assert drive(runner) == 1
    error = capsys.readouterr().err
    assert "Verify merged PR failed" in error
    assert f"Resume with: {' '.join(verify)}" in error
    assert ("git", "checkout", "main") not in [call[0] for call in runner.calls]


def test_chain_order_and_sync_output_is_compact(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = Runner()
    assert drive(runner) == 0
    chain = [
        call[0] for call in runner.calls if call[0] != ("git", "status", "--porcelain")
    ]
    assert [
        (
            command[3]
            if command[:3] == ("uv", "run", "atlas")
            else command[2]
            if command[:2] == ("uv", "run")
            else command[1]
        )
        for command in chain
    ] == [
        "evidence",
        "confirm",
        "verify",
        "verify",
        "checkout",
        "pull",
        "alembic",
        "pm",
        "pm",
    ]
    output = capsys.readouterr().out
    assert "many skip lines" not in output
    assert "Tick 1: pm sync: completed=1" in output
    assert "Tick 2: pm sync: completed=1" in output


def test_non_passing_pre_merge_verdict_blocks_merge(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class PendingRunner(Runner):
        def __call__(
            self, command: tuple[str, ...], **kwargs: Any
        ) -> CompletedProcess[str]:
            result = super().__call__(command, **kwargs)
            if "verify" in command and "--json" in command:
                return CompletedProcess(command, 0, '{"status": "pending"}\n', "")
            return result

    runner = PendingRunner()
    pauses: list[str] = []

    def pause(prompt: str) -> str:
        pauses.append(prompt)
        return ""

    code = close_ticket.drive(
        args(),
        environ={"GITHUB_TOKEN": "secret"},
        run_command=runner,
        pause=pause,
        resolve_assessment=AssessmentSequence(),
    )

    assert code == 1
    assert pauses == []
    assert "not passed; merge is blocked" in capsys.readouterr().err


def test_non_passing_pre_merge_verdict_names_one_blocking_check(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class PendingRunner(Runner):
        def __call__(
            self, command: tuple[str, ...], **kwargs: Any
        ) -> CompletedProcess[str]:
            result = super().__call__(command, **kwargs)
            if "verify" in command and "--json" in command:
                payload = {
                    "head_commit": HEAD,
                    "status": "pending",
                    "blocking_checks": [
                        {
                            "ticket_id": "00000000-0000-0000-0000-000000000200",
                            "ticket_key": "ATLAS-200",
                            "head_commit": HEAD,
                            "check_type": "documentation",
                            "required": True,
                            "status": "pending",
                            "evidence_ids": [],
                            "reason": (
                                "documentation: a system-tier documentation_update "
                                f"record exists at {HEAD} but covers none of the "
                                "required paths; PENDING."
                            ),
                        }
                    ],
                    "tickets": [],
                }
                return CompletedProcess(command, 0, json.dumps(payload) + "\n", "")
            return result

    runner = PendingRunner()
    pauses: list[str] = []

    code = close_ticket.drive(
        args(),
        environ={"GITHUB_TOKEN": "secret"},
        run_command=runner,
        pause=recording_pause(pauses),
        resolve_assessment=AssessmentSequence(),
    )

    assert code == 1
    assert pauses == []
    assert_no_post_merge_actions(runner)
    error = capsys.readouterr().err
    assert "Blocking verification checks:" in error
    assert (
        f"- ATLAS-200 documentation pending at {HEAD}: documentation: "
        "a system-tier documentation_update record exists"
    ) in error
    assert "covers none of the required paths; PENDING." in error


def test_non_passing_pre_merge_verdict_names_several_blocking_checks_in_order(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FailedRunner(Runner):
        def __call__(
            self, command: tuple[str, ...], **kwargs: Any
        ) -> CompletedProcess[str]:
            result = super().__call__(command, **kwargs)
            if "verify" in command and "--json" in command:
                payload = {
                    "head_commit": HEAD,
                    "status": "failed",
                    "blocking_checks": [
                        {
                            "ticket_id": "00000000-0000-0000-0000-000000000200",
                            "ticket_key": "ATLAS-200",
                            "head_commit": HEAD,
                            "check_type": "tests",
                            "required": True,
                            "status": "failed",
                            "evidence_ids": [],
                            "reason": f"tests: folded status failed at {HEAD}.",
                        },
                        {
                            "ticket_id": "00000000-0000-0000-0000-000000000201",
                            "ticket_key": "ATLAS-201",
                            "head_commit": HEAD,
                            "check_type": "acceptance_criteria",
                            "required": True,
                            "status": "pending",
                            "evidence_ids": [],
                            "reason": (
                                f"acceptance_criteria: 1 of 2 criteria confirmed "
                                f"at {HEAD}; 1 unconfirmed; PENDING."
                            ),
                        },
                    ],
                    "tickets": [],
                }
                return CompletedProcess(command, 0, json.dumps(payload) + "\n", "")
            return result

    runner = FailedRunner()

    code = close_ticket.drive(
        args(),
        environ={"GITHUB_TOKEN": "secret"},
        run_command=runner,
        pause=lambda _: "",
        resolve_assessment=AssessmentSequence(),
    )

    assert code == 1
    error = capsys.readouterr().err
    first = f"- ATLAS-200 tests failed at {HEAD}: tests: folded status failed"
    second = (
        f"- ATLAS-201 acceptance_criteria pending at {HEAD}: "
        "acceptance_criteria: 1 of 2 criteria confirmed"
    )
    assert first in error
    assert second in error
    assert error.index(first) < error.index(second)


@pytest.mark.parametrize(
    "verdict",
    [
        '{"status": "passed"}\n',
        '{"status": "passed", "head_commit": ""}\n',
        '{"status": "passed", "head_commit": 123}\n',
        "not-json\n",
        "[]\n",
    ],
)
def test_invalid_pre_merge_head_blocks_before_merge_prompt(
    verdict: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class VerdictRunner(Runner):
        def __call__(
            self, command: tuple[str, ...], **kwargs: Any
        ) -> CompletedProcess[str]:
            result = super().__call__(command, **kwargs)
            if "verify" in command and "--json" in command:
                return CompletedProcess(command, 0, verdict, "")
            return result

    runner = VerdictRunner()
    pauses: list[str] = []

    def pause(prompt: str) -> str:
        pauses.append(prompt)
        return ""

    code = close_ticket.drive(
        args(),
        environ={"GITHUB_TOKEN": "secret"},
        run_command=runner,
        pause=pause,
        resolve_assessment=AssessmentSequence(),
    )

    assert code == 1
    assert pauses == []
    assert "merge is blocked" in capsys.readouterr().err


def test_second_head_race_blocks_merge_prompt_and_completion_tail(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = Runner()
    pauses: list[str] = []
    assessments = AssessmentSequence(assessment(), assessment(head_sha=NEW_HEAD))

    code = close_ticket.drive(
        args(),
        environ={"GITHUB_TOKEN": "secret"},
        run_command=runner,
        pause=recording_pause(pauses),
        resolve_assessment=assessments,
        resolve_context=lambda _repo, _pr: merged(),
    )

    assert code == 1
    assert assessments.calls == [("acme/atlas", 248), ("acme/atlas", 248)]
    assert pauses == []
    commands = [call[0] for call in runner.calls]
    assert any("evidence" in command for command in commands)
    assert any("confirm" in command for command in commands)
    assert len([command for command in commands if "verify" in command]) == 1
    assert_no_post_merge_actions(runner)
    assert "PR head moved" in capsys.readouterr().err


def test_second_base_race_blocks_merge_prompt_and_completion_tail(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = Runner()
    pauses: list[str] = []
    assessments = AssessmentSequence(assessment(), assessment(base_sha=NEW_BASE))

    code = close_ticket.drive(
        args(),
        environ={"GITHUB_TOKEN": "secret"},
        run_command=runner,
        pause=recording_pause(pauses),
        resolve_assessment=assessments,
        resolve_context=lambda _repo, _pr: merged(),
    )

    assert code == 1
    assert pauses == []
    assert_no_post_merge_actions(runner)
    assert "base moved" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("live", "message"),
    [
        (
            assessment(
                mergeability=PRMergeabilityStatus.INDETERMINATE,
                integration_status=PRIntegrationStatus.INDETERMINATE,
            ),
            "integration_status: indeterminate",
        ),
        (
            ineligible_assessment(
                PRIntegrationEligibility.DRAFT,
                pr_draft=True,
            ),
            "eligibility: draft",
        ),
    ],
)
def test_second_eligibility_or_mergeability_change_restarts_before_prompt(
    live: PRIntegrationAssessment,
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = Runner()
    pauses: list[str] = []

    code = close_ticket.drive(
        args(),
        environ={"GITHUB_TOKEN": "secret"},
        run_command=runner,
        pause=recording_pause(pauses),
        resolve_assessment=AssessmentSequence(assessment(), live),
        resolve_context=lambda _repo, _pr: merged(),
    )

    assert code == 1
    assert pauses == []
    assert_no_post_merge_actions(runner)
    assert message in capsys.readouterr().err


def test_second_assessment_api_failure_blocks_merge_prompt_and_tail(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = Runner()
    pauses: list[str] = []

    code = close_ticket.drive(
        args(),
        environ={"GITHUB_TOKEN": "secret"},
        run_command=runner,
        pause=recording_pause(pauses),
        resolve_assessment=AssessmentSequence(
            assessment(),
            GitHubAPIError("GitHub API request failed: compare timeout"),
        ),
        resolve_context=lambda _repo, _pr: merged(),
    )

    assert code == 1
    assert pauses == []
    assert_no_post_merge_actions(runner)
    error = capsys.readouterr().err
    assert "integration_status: indeterminate" in error
    assert "compare timeout" in error


def test_verified_head_mismatch_restarts_before_merge_prompt(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = Runner(verified_head=NEW_HEAD)
    pauses: list[str] = []

    code = close_ticket.drive(
        args(),
        environ={"GITHUB_TOKEN": "secret"},
        run_command=runner,
        pause=recording_pause(pauses),
        resolve_assessment=AssessmentSequence(assessment(), assessment()),
        resolve_context=lambda _repo, _pr: merged(),
    )

    assert code == 1
    assert pauses == []
    assert_no_post_merge_actions(runner)
    error = capsys.readouterr().err
    assert NEW_HEAD in error
    assert HEAD in error
    assert "verification evaluated" in error


def test_interrupted_old_head_run_cannot_skip_new_head_gates() -> None:
    first = Runner()
    first_pauses: list[str] = []
    first_code = close_ticket.drive(
        args(),
        environ={"GITHUB_TOKEN": "secret"},
        run_command=first,
        pause=recording_pause(first_pauses),
        resolve_assessment=AssessmentSequence(
            assessment(),
            assessment(head_sha=NEW_HEAD),
        ),
        resolve_context=lambda _repo, _pr: merged(),
    )

    assert first_code == 1
    assert first_pauses == []

    second = Runner(verified_head=NEW_HEAD)
    second_pauses: list[str] = []
    second_assessments = AssessmentSequence(
        assessment(head_sha=NEW_HEAD),
        assessment(head_sha=NEW_HEAD),
    )
    second_code = close_ticket.drive(
        args(),
        environ={"GITHUB_TOKEN": "secret"},
        run_command=second,
        pause=recording_pause(second_pauses),
        resolve_assessment=second_assessments,
        resolve_context=lambda _repo, _pr: merged(head_commit=NEW_HEAD),
        read_statuses=lambda _keys: [("ATLAS-200", "done")],
    )

    assert second_code == 0
    assert second_assessments.calls == [("acme/atlas", 248), ("acme/atlas", 248)]
    second_commands = [call[0] for call in second.calls]
    assert sum("evidence" in command for command in second_commands) == 1
    assert sum("confirm" in command for command in second_commands) == 1
    assert sum("verify" in command for command in second_commands) == 2
    assert len(second_pauses) == 1


def test_verified_head_matching_merged_head_continues() -> None:
    runner = Runner()
    assessments = AssessmentSequence()

    assert drive(runner, context=merged(), assessments=assessments) == 0
    assert assessments.calls == [("acme/atlas", 248), ("acme/atlas", 248)]
    verify_calls = [call[0] for call in runner.calls if "verify" in call[0]]
    assert len(verify_calls) == 2


def test_verified_head_mismatch_blocks_all_post_merge_actions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = Runner()
    context = merged()
    context.head_commit = "b" * 40

    assert drive(runner, context=context) == 1

    commands = [call[0] for call in runner.calls]
    verify_calls = [command for command in commands if "verify" in command]
    assert len(verify_calls) == 1
    assert ("git", "checkout", "main") not in commands
    assert ("git", "pull") not in commands
    assert not any("alembic" in command for command in commands)
    assert not any("pm" in command for command in commands)
    error = capsys.readouterr().err
    assert "a" * 40 in error
    assert "b" * 40 in error


def test_final_status_is_read_and_non_done_is_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = Runner()
    assert drive(runner, statuses=[("ATLAS-200", "review_required")]) == 1
    captured = capsys.readouterr()
    assert "ATLAS-200: review_required" in captured.out
    assert "Closure incomplete" in captured.err


def test_rerun_keeps_confirm_and_defers_exact_head_deduplication() -> None:
    """The driver never heuristically skips confirm; confirm owns exact-C dedupe."""
    runner = Runner()
    assert drive(runner) == 0
    assert sum("confirm" in call[0] for call in runner.calls) == 1

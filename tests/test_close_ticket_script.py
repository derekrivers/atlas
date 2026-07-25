"""Acceptance-chain driver tests (ATLAS-040M)."""

from __future__ import annotations

import importlib.util
from argparse import Namespace
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace
from typing import Any, cast

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "close_ticket.py"
SPEC = importlib.util.spec_from_file_location("close_ticket", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
close_ticket = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(close_ticket)


class Runner:
    def __init__(
        self,
        *,
        dirty: bool = False,
        failure: tuple[str, ...] | None = None,
    ) -> None:
        self.calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []
        self.dirty = dirty
        self.failure = failure

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


def merged(*, is_merged: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        pull_request={
            "merged": is_merged,
            "title": "Close ATLAS-200",
            "body": None,
        },
        head_commit="a" * 40,
    )


def drive(
    runner: Runner,
    *,
    context: SimpleNamespace | None = None,
    statuses: list[tuple[str, str]] | None = None,
) -> int:
    return cast(
        int,
        close_ticket.drive(
            args(),
            environ={"GITHUB_TOKEN": "secret"},
            run_command=runner,
            pause=lambda _: "",
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


def test_origin_repo_is_defaulted() -> None:
    runner = Runner()
    arguments = args(repo=None)
    code = close_ticket.drive(
        arguments,
        environ={"GITHUB_TOKEN": "secret"},
        run_command=runner,
        pause=lambda _: "",
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
        resolve_context=lambda _repo, _pr: merged(is_merged=False),
    )
    assert code == 1
    assert pauses
    assert not any("verify" in call[0] for call in runner.calls)
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
        command[3] if command[:3] == ("uv", "run", "atlas") else command[1]
        for command in chain
    ] == [
        "evidence",
        "confirm",
        "verify",
        "checkout",
        "pull",
        "pm",
        "pm",
    ]
    output = capsys.readouterr().out
    assert "many skip lines" not in output
    assert "Tick 1: pm sync: completed=1" in output
    assert "Tick 2: pm sync: completed=1" in output


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

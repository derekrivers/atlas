"""Lease-guarded operator PR rebase lane."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from github_fakes import FakeGitHubClient

from atlas.core.enums import ActorType, RiskLevel
from atlas.core.models import Ticket, TicketStatus, TicketType
from atlas.github import GitHubCompare, GitHubCompareStatus
from atlas.orchestration.pr_rebase import (
    MANIFEST_FILENAME,
    PRRebaseOutcome,
    PRRebasePreconditionError,
    PRRebaseRefusal,
    PRRebaseResult,
    PRRebaseState,
    abort_pr_rebase,
    continue_pr_rebase,
    prepare_pr_rebase,
    publish_pr_rebase,
    run_git,
)

OWNER = "atlas"
REPO = "atlas"
REPO_SLUG = f"{OWNER}/{REPO}"
PR_NUMBER = 229
TICKET_KEY = "ATLAS-229"
BRANCH = "feature/rebase-lane"
NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
PRODUCT_ID = UUID("00000000-0000-0000-0000-000000000001")
EPIC_ID = UUID("00000000-0000-0000-0000-000000000002")
TICKET_ID = UUID("00000000-0000-0000-0000-000000000003")
RERERE_DISABLED_PREFIX = (
    "-c",
    "rerere.enabled=false",
    "-c",
    "rerere.autoupdate=false",
    "rebase",
)


def _git(cwd: Path, *argv: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *argv],
        cwd=cwd,
        capture_output=True,
        text=True,
        shell=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(argv)} failed in {cwd}:\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    return result


def _git_stdout(cwd: Path, *argv: str) -> str:
    return _git(cwd, *argv).stdout.strip()


def _config_identity(repo: Path) -> None:
    _git(repo, "config", "user.email", "operator@example.com")
    _git(repo, "config", "user.name", "Atlas Operator")


def _commit_paths(repo: Path, changes: Mapping[str, str], message: str) -> str:
    for relative, contents in changes.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
        _git(repo, "add", relative)
    _git(repo, "commit", "-m", message)
    return _git_stdout(repo, "rev-parse", "HEAD")


@dataclass(frozen=True)
class RepoFixture:
    remote: Path
    seed: Path
    primary: Path
    branch: str
    base_sha: str
    main_sha: str
    feature_sha: str


def _repo_fixture(tmp_path: Path, *, mode: str) -> RepoFixture:
    remote = tmp_path / OWNER / f"{REPO}.git"
    seed = tmp_path / "seed"
    primary = tmp_path / "primary"
    remote.parent.mkdir()
    _git(tmp_path, "init", "--bare", str(remote))
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    _config_identity(seed)
    base_files = {".gitignore": ".atlas/\n"}
    if mode == "multiple_conflicts":
        base_sha = _commit_paths(
            seed,
            base_files | {"one.txt": "base one\n", "two.txt": "base two\n"},
            "base",
        )
    elif mode == "conflict":
        base_sha = _commit_paths(seed, base_files | {"shared.txt": "base\n"}, "base")
    else:
        base_sha = _commit_paths(seed, base_files | {"README.md": "base\n"}, "base")
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "origin", "main")
    _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")

    _git(seed, "checkout", "-b", BRANCH, base_sha)
    if mode == "multiple_conflicts":
        _commit_paths(seed, {"one.txt": "feature one\n"}, "feature one")
        feature_sha = _commit_paths(seed, {"two.txt": "feature two\n"}, "feature two")
    elif mode == "conflict":
        feature_sha = _commit_paths(seed, {"shared.txt": "feature\n"}, "feature")
    else:
        feature_sha = _commit_paths(seed, {"feature.txt": "feature\n"}, "feature")
    _git(seed, "push", "origin", f"{BRANCH}:{BRANCH}")

    _git(seed, "checkout", "main")
    if mode == "multiple_conflicts":
        main_sha = _commit_paths(
            seed,
            {"one.txt": "main one\n", "two.txt": "main two\n"},
            "main conflicts",
        )
    elif mode == "conflict":
        main_sha = _commit_paths(seed, {"shared.txt": "main\n"}, "main conflict")
    else:
        main_sha = _commit_paths(seed, {"main.txt": "main\n"}, "main")
    _git(seed, "push", "origin", "main")

    _git(tmp_path, "clone", "--branch", "main", str(remote), str(primary))
    _config_identity(primary)
    _git(primary, "checkout", "-b", "operator")
    return RepoFixture(
        remote=remote,
        seed=seed,
        primary=primary,
        branch=BRANCH,
        base_sha=base_sha,
        main_sha=main_sha,
        feature_sha=feature_sha,
    )


class LocalRemoteGitHubClient:
    def __init__(
        self,
        fixture: RepoFixture,
        *,
        title: str = f"{TICKET_KEY}: stale PR",
        body: str = "",
        mergeable: bool | None = True,
        state: str = "open",
        draft: bool = False,
        merged: bool = False,
        head_ref: str | None = None,
        head_repo: str = REPO_SLUG,
        base_repo: str = REPO_SLUG,
        base_ref: str = "main",
        head_sha_override: str | None = None,
        base_sha_override: str | None = None,
    ) -> None:
        self.fixture = fixture
        self.title = title
        self.body = body
        self.mergeable = mergeable
        self.state = state
        self.draft = draft
        self.merged = merged
        self.head_ref = head_ref or fixture.branch
        self.head_repo = head_repo
        self.base_repo = base_repo
        self.base_ref = base_ref
        self.head_sha_override = head_sha_override
        self.base_sha_override = base_sha_override
        self.calls: list[tuple[str, str, str, str | int]] = []

    def fetch_workflow_runs(
        self, owner: str, repo: str, head_sha: str
    ) -> list[dict[str, Any]]:
        self.calls.append(("workflow_runs", owner, repo, head_sha))
        return []

    def fetch_check_runs(
        self, owner: str, repo: str, head_sha: str
    ) -> list[dict[str, Any]]:
        self.calls.append(("check_runs", owner, repo, head_sha))
        return []

    def fetch_pr_reviews(
        self, owner: str, repo: str, pr_number: int
    ) -> list[dict[str, Any]]:
        self.calls.append(("pr_reviews", owner, repo, pr_number))
        return []

    def fetch_pr_files(
        self, owner: str, repo: str, pr_number: int
    ) -> list[dict[str, Any]]:
        self.calls.append(("pr_files", owner, repo, pr_number))
        return []

    def fetch_pull_request(
        self, owner: str, repo: str, pr_number: int
    ) -> dict[str, Any]:
        self.calls.append(("pull_request", owner, repo, pr_number))
        return {
            "number": pr_number,
            "title": self.title,
            "body": self.body,
            "state": self.state,
            "draft": self.draft,
            "merged": self.merged,
            "mergeable": self.mergeable,
            "head": {
                "ref": self.head_ref,
                "sha": self.head_sha_override
                or _git_stdout(
                    self.fixture.remote, "rev-parse", f"refs/heads/{BRANCH}"
                ),
                "repo": {"full_name": self.head_repo},
            },
            "base": {
                "ref": self.base_ref,
                "sha": self.base_sha_override
                or _git_stdout(
                    self.fixture.remote, "rev-parse", f"refs/heads/{self.base_ref}"
                ),
                "repo": {"full_name": self.base_repo},
            },
        }

    def fetch_branch_head(self, owner: str, repo: str, branch: str) -> str:
        self.calls.append(("branch_head", owner, repo, branch))
        return self.fixture.main_sha

    def compare_commits(
        self, owner: str, repo: str, base_sha: str, head_sha: str
    ) -> GitHubCompare:
        self.calls.append(("compare", owner, repo, f"{base_sha}...{head_sha}"))
        merge_base = _git_stdout(self.fixture.remote, "merge-base", base_sha, head_sha)
        ahead_by = int(
            _git_stdout(
                self.fixture.remote, "rev-list", "--count", f"{base_sha}..{head_sha}"
            )
        )
        behind_by = int(
            _git_stdout(
                self.fixture.remote, "rev-list", "--count", f"{head_sha}..{base_sha}"
            )
        )
        if ahead_by and behind_by:
            status = GitHubCompareStatus.DIVERGED
        elif ahead_by:
            status = GitHubCompareStatus.AHEAD
        elif behind_by:
            status = GitHubCompareStatus.BEHIND
        else:
            status = GitHubCompareStatus.IDENTICAL
        return GitHubCompare(
            status=status,
            ahead_by=ahead_by,
            behind_by=behind_by,
            merge_base_sha=merge_base,
        )


class FakeTicketLookup:
    def __init__(self, statuses: Mapping[str, TicketStatus]) -> None:
        self.statuses = dict(statuses)
        self.calls: list[str] = []

    def get_by_key(self, key: str) -> Ticket | None:
        self.calls.append(key)
        status = self.statuses.get(key)
        if status is None:
            return None
        return Ticket(
            id=TICKET_ID,
            product_id=PRODUCT_ID,
            epic_id=EPIC_ID,
            key=key,
            title="Ticket",
            objective="Objective",
            context="Context",
            status=status,
            ticket_type=TicketType.FEATURE,
            risk_level=RiskLevel.LOW,
            priority=1,
            source_anchor="docs/atlas/symphony-integration.md#test",
            created_by_type=ActorType.SYSTEM,
            created_by_id="test",
            created_at=NOW,
            updated_at=NOW,
        )


class RecordingGitRunner:
    def __init__(
        self,
        *,
        before_push: Callable[[], None] | None = None,
        after_push: Callable[[], None] | None = None,
    ) -> None:
        self.calls: list[tuple[Path, tuple[str, ...]]] = []
        self.before_push = before_push
        self.after_push = after_push
        self._pushed = False
        self._after_pushed = False

    def __call__(
        self,
        cwd: Path,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        args = tuple(argv)
        self.calls.append((cwd, args))
        if (
            args
            and args[0] == "push"
            and self.before_push is not None
            and not self._pushed
        ):
            self._pushed = True
            self.before_push()
        result = run_git(cwd, argv, env=env)
        if (
            args
            and args[0] == "push"
            and result.returncode == 0
            and self.after_push is not None
            and not self._after_pushed
        ):
            self._after_pushed = True
            self.after_push()
        return result


def _minimal_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _config_identity(repo)
    _commit_paths(repo, {"README.md": "base\n"}, "base")
    return repo


def _primary_snapshot(repo: Path) -> dict[str, str]:
    return {
        "branch": _git_stdout(repo, "branch", "--show-current"),
        "head": _git_stdout(repo, "rev-parse", "HEAD"),
        "status": _git_stdout(repo, "status", "--short"),
        "cached": _git_stdout(repo, "diff", "--cached", "--name-status"),
        "worktree": _git_stdout(repo, "diff", "--name-status"),
        "refs": _git_stdout(
            repo,
            "for-each-ref",
            "--format=%(refname):%(objectname)",
            "refs/heads",
        ),
    }


def _ready_lookup() -> FakeTicketLookup:
    return FakeTicketLookup({TICKET_KEY: TicketStatus.REVIEW_REQUIRED})


def _prepare_ready(
    fixture: RepoFixture,
    *,
    runner: RecordingGitRunner | None = None,
    client: LocalRemoteGitHubClient | None = None,
) -> tuple[PRRebaseResult, RecordingGitRunner, LocalRemoteGitHubClient]:
    resolved_runner = runner or RecordingGitRunner()
    resolved_client = client or LocalRemoteGitHubClient(fixture)
    result = prepare_pr_rebase(
        repo_slug=REPO_SLUG,
        pr_number=PR_NUMBER,
        repo_root=fixture.primary,
        github_client=resolved_client,
        ticket_lookup=_ready_lookup(),
        git_runner=resolved_runner,
        now=NOW,
    )
    assert result.outcome is PRRebaseOutcome.READY_TO_PUBLISH
    assert result.workspace_path is not None
    return result, resolved_runner, resolved_client


def _manifest_payload(workspace: Path) -> dict[str, Any]:
    payload = json.loads((workspace / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return cast(dict[str, Any], payload)


def _write_manifest_payload(workspace: Path, payload: Mapping[str, Any]) -> None:
    (workspace / MANIFEST_FILENAME).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _push_calls(runner: RecordingGitRunner) -> list[tuple[str, ...]]:
    return [argv for _cwd, argv in runner.calls if argv and argv[0] == "push"]


def test_prepare_gate_refuses_before_local_git_mutation_for_non_rebaseable_prs(
    tmp_path: Path,
) -> None:
    repo = _minimal_repo(tmp_path)
    base_sha = "1" * 40
    head_sha = "2" * 40

    def payload(
        *, title: str = f"{TICKET_KEY}: stale PR", mergeable: bool | None = True
    ) -> dict[str, Any]:
        return {
            "number": PR_NUMBER,
            "title": title,
            "body": "",
            "state": "open",
            "draft": False,
            "merged": False,
            "mergeable": mergeable,
            "head": {"ref": BRANCH, "sha": head_sha, "repo": {"full_name": REPO_SLUG}},
            "base": {"ref": "main", "sha": base_sha, "repo": {"full_name": REPO_SLUG}},
        }

    cases = [
        (
            FakeGitHubClient(
                pull_request=payload(),
                compare=GitHubCompare(
                    status=GitHubCompareStatus.AHEAD,
                    ahead_by=1,
                    behind_by=0,
                    merge_base_sha=base_sha,
                ),
            ),
            _ready_lookup(),
            PRRebaseOutcome.NOOP_CURRENT,
        ),
        (
            FakeGitHubClient(
                pull_request=payload(mergeable=None),
                compare=GitHubCompare(
                    status=GitHubCompareStatus.AHEAD,
                    ahead_by=1,
                    behind_by=0,
                    merge_base_sha=base_sha,
                ),
            ),
            _ready_lookup(),
            PRRebaseOutcome.REFUSED,
        ),
        (
            FakeGitHubClient(
                pull_request=payload(title="No ticket key"),
                compare=GitHubCompare(
                    status=GitHubCompareStatus.DIVERGED,
                    ahead_by=1,
                    behind_by=1,
                    merge_base_sha="3" * 40,
                ),
            ),
            _ready_lookup(),
            PRRebaseOutcome.REFUSED,
        ),
        (
            FakeGitHubClient(
                pull_request=payload(title="ATLAS-999: stale PR"),
                compare=GitHubCompare(
                    status=GitHubCompareStatus.DIVERGED,
                    ahead_by=1,
                    behind_by=1,
                    merge_base_sha="3" * 40,
                ),
            ),
            _ready_lookup(),
            PRRebaseOutcome.REFUSED,
        ),
        (
            FakeGitHubClient(
                pull_request=payload(),
                compare=GitHubCompare(
                    status=GitHubCompareStatus.DIVERGED,
                    ahead_by=1,
                    behind_by=1,
                    merge_base_sha="3" * 40,
                ),
            ),
            FakeTicketLookup({TICKET_KEY: TicketStatus.IN_PROGRESS}),
            PRRebaseOutcome.REFUSED,
        ),
    ]

    for client, lookup, outcome in cases:
        runner = RecordingGitRunner()
        result = prepare_pr_rebase(
            repo_slug=REPO_SLUG,
            pr_number=PR_NUMBER,
            repo_root=repo,
            github_client=client,
            ticket_lookup=lookup,
            git_runner=runner,
            now=NOW,
        )

        assert result.outcome is outcome
        assert not (repo / ".atlas" / "rebase-workspaces").exists()
        mutating = [
            argv[0]
            for _cwd, argv in runner.calls
            if argv and argv[0] in {"fetch", "worktree", "rebase", "push"}
        ]
        assert mutating == []


def test_prepare_clean_rebase_creates_detached_manifest_and_preserves_primary_checkout(
    tmp_path: Path,
) -> None:
    fixture = _repo_fixture(tmp_path, mode="clean")
    (fixture.primary / "README.md").write_text("operator dirty\n", encoding="utf-8")
    before = _primary_snapshot(fixture.primary)

    result, _runner, _client = _prepare_ready(fixture)

    assert result.workspace_path is not None
    assert result.manifest_path == result.workspace_path / MANIFEST_FILENAME
    assert _git_stdout(result.workspace_path, "branch", "--show-current") == ""
    assert (
        _git_stdout(result.workspace_path, "rev-parse", "HEAD") == result.new_head_sha
    )
    manifest = _manifest_payload(result.workspace_path)
    assert manifest["schema_version"] == 1
    assert manifest["kind"] == "atlas_pr_rebase_manifest"
    assert manifest["repo_slug"] == REPO_SLUG
    assert manifest["repo_root"] == str(fixture.primary)
    assert manifest["pr_number"] == PR_NUMBER
    assert manifest["head_ref"] == BRANCH
    assert manifest["head_branch_ref"] == f"refs/heads/{BRANCH}"
    assert manifest["original_head_sha"] == fixture.feature_sha
    assert manifest["pinned_base_sha"] == fixture.main_sha
    assert manifest["merge_base_sha"] == fixture.base_sha
    assert manifest["workspace_path"] == str(result.workspace_path)
    assert manifest["state"] == PRRebaseState.READY_TO_PUBLISH.value
    assert manifest["rebased_head_sha"] == result.new_head_sha
    assert manifest["transitions"][0]["state"] == PRRebaseState.PREPARED.value
    assert _primary_snapshot(fixture.primary) == before


def test_prepare_conflict_records_exact_unmerged_paths_and_preserves_stopped_rebase(
    tmp_path: Path,
) -> None:
    fixture = _repo_fixture(tmp_path, mode="conflict")
    client = LocalRemoteGitHubClient(fixture, mergeable=False)
    runner = RecordingGitRunner()

    result = prepare_pr_rebase(
        repo_slug=REPO_SLUG,
        pr_number=PR_NUMBER,
        repo_root=fixture.primary,
        github_client=client,
        ticket_lookup=_ready_lookup(),
        git_runner=runner,
        now=NOW,
    )

    assert result.outcome is PRRebaseOutcome.CONFLICTS_PENDING
    assert result.conflict_paths == ("shared.txt",)
    assert result.workspace_path is not None
    assert (
        _git_stdout(result.workspace_path, "diff", "--name-only", "--diff-filter=U")
        == "shared.txt"
    )
    manifest = _manifest_payload(result.workspace_path)
    assert manifest["state"] == PRRebaseState.CONFLICTS_PENDING.value
    assert manifest["conflict_sets"] == [
        {"timestamp": NOW.isoformat().replace("+00:00", "Z"), "paths": ["shared.txt"]}
    ]
    assert (
        _git_stdout(fixture.remote, "rev-parse", f"refs/heads/{BRANCH}")
        == fixture.feature_sha
    )
    assert _push_calls(runner) == []


def test_prepare_disables_repository_rerere_autoupdate_for_rebase(
    tmp_path: Path,
) -> None:
    fixture = _repo_fixture(tmp_path, mode="conflict")
    _git(fixture.primary, "config", "rerere.enabled", "true")
    _git(fixture.primary, "config", "rerere.autoupdate", "true")

    _git(fixture.primary, "checkout", "--detach", fixture.feature_sha)
    seeded = _git(fixture.primary, "rebase", fixture.main_sha, check=False)
    assert seeded.returncode != 0
    (fixture.primary / "shared.txt").write_text(
        "previous manual resolution\n", encoding="utf-8"
    )
    _git(fixture.primary, "add", "shared.txt")
    _git(fixture.primary, "-c", "core.editor=true", "rebase", "--continue")
    _git(fixture.primary, "checkout", "operator")

    client = LocalRemoteGitHubClient(fixture, mergeable=False)
    runner = RecordingGitRunner()
    result = prepare_pr_rebase(
        repo_slug=REPO_SLUG,
        pr_number=PR_NUMBER,
        repo_root=fixture.primary,
        github_client=client,
        ticket_lookup=_ready_lookup(),
        git_runner=runner,
        now=NOW,
    )

    assert result.outcome is PRRebaseOutcome.CONFLICTS_PENDING
    assert result.conflict_paths == ("shared.txt",)
    assert any(
        argv == (*RERERE_DISABLED_PREFIX, fixture.main_sha)
        for _cwd, argv in runner.calls
    )


def test_continue_refuses_unresolved_entries_and_records_later_conflict_sets(
    tmp_path: Path,
) -> None:
    fixture = _repo_fixture(tmp_path, mode="multiple_conflicts")
    client = LocalRemoteGitHubClient(fixture, mergeable=False)
    runner = RecordingGitRunner()
    result = prepare_pr_rebase(
        repo_slug=REPO_SLUG,
        pr_number=PR_NUMBER,
        repo_root=fixture.primary,
        github_client=client,
        ticket_lookup=_ready_lookup(),
        git_runner=runner,
        now=NOW,
    )
    assert result.workspace_path is not None
    workspace = result.workspace_path
    assert result.conflict_paths == ("one.txt",)

    refused = continue_pr_rebase(
        workspace_path=workspace,
        repo_root=fixture.primary,
        git_runner=runner,
        now=NOW,
    )
    assert refused.outcome is PRRebaseOutcome.REFUSED
    assert refused.conflict_paths == ("one.txt",)

    (workspace / "one.txt").write_text("resolved one\n", encoding="utf-8")
    _git(workspace, "add", "one.txt")
    stopped_again = continue_pr_rebase(
        workspace_path=workspace,
        repo_root=fixture.primary,
        git_runner=runner,
        now=NOW,
    )
    assert stopped_again.outcome is PRRebaseOutcome.CONFLICTS_PENDING
    assert stopped_again.conflict_paths == ("two.txt",)
    manifest = _manifest_payload(workspace)
    assert [entry["paths"] for entry in manifest["conflict_sets"]] == [
        ["one.txt"],
        ["two.txt"],
    ]

    (workspace / "two.txt").write_text("resolved two\n", encoding="utf-8")
    _git(workspace, "add", "two.txt")
    ready = continue_pr_rebase(
        workspace_path=workspace,
        repo_root=fixture.primary,
        git_runner=runner,
        now=NOW,
    )
    assert ready.outcome is PRRebaseOutcome.READY_TO_PUBLISH
    assert _manifest_payload(workspace)["state"] == PRRebaseState.READY_TO_PUBLISH.value
    assert any(
        argv == (*RERERE_DISABLED_PREFIX, "--continue") for _cwd, argv in runner.calls
    )


@pytest.mark.parametrize("moved_ref", ["head", "main"])
def test_publish_refetches_live_refs_and_refuses_moved_head_or_main_before_push(
    tmp_path: Path,
    moved_ref: str,
) -> None:
    fixture = _repo_fixture(tmp_path, mode="clean")
    result, runner, client = _prepare_ready(fixture)
    assert result.workspace_path is not None
    workspace = result.workspace_path

    if moved_ref == "head":
        _git(fixture.seed, "checkout", BRANCH)
        moved_sha = _commit_paths(
            fixture.seed, {"race.txt": "head moved\n"}, "head moved"
        )
        _git(fixture.seed, "push", "origin", f"{BRANCH}:{BRANCH}")
        assert moved_sha == _git_stdout(
            fixture.remote, "rev-parse", f"refs/heads/{BRANCH}"
        )
    else:
        _git(fixture.seed, "checkout", "main")
        moved_sha = _commit_paths(
            fixture.seed, {"main-race.txt": "main moved\n"}, "main moved"
        )
        _git(fixture.seed, "push", "origin", "main")
        assert moved_sha == _git_stdout(fixture.remote, "rev-parse", "refs/heads/main")

    with pytest.raises(PRRebaseRefusal, match="moved"):
        publish_pr_rebase(
            workspace_path=workspace,
            repo_root=fixture.primary,
            github_client=client,
            git_runner=runner,
            now=NOW,
            sleep=lambda _seconds: None,
        )

    assert workspace.exists()
    assert _push_calls(runner) == []


def test_publish_uses_explicit_expected_value_lease_and_writes_receipt_cleanup(
    tmp_path: Path,
) -> None:
    fixture = _repo_fixture(tmp_path, mode="clean")
    result, runner, client = _prepare_ready(fixture)
    assert result.workspace_path is not None
    workspace = result.workspace_path

    published = publish_pr_rebase(
        workspace_path=workspace,
        repo_root=fixture.primary,
        github_client=client,
        git_runner=runner,
        now=NOW,
        sleep=lambda _seconds: None,
    )

    assert published.outcome is PRRebaseOutcome.PUBLISHED
    assert published.receipt_path is not None
    assert published.receipt_path.is_file()
    assert not workspace.exists()
    assert (
        _git_stdout(fixture.remote, "rev-parse", f"refs/heads/{BRANCH}")
        == published.new_head_sha
    )
    push_calls = _push_calls(runner)
    assert push_calls == [
        (
            "push",
            f"--force-with-lease=refs/heads/{BRANCH}:{fixture.feature_sha}",
            str(fixture.remote),
            f"{published.new_head_sha}:refs/heads/{BRANCH}",
        )
    ]
    assert "--force" not in push_calls[0]
    assert "--force-with-lease" not in push_calls[0]
    receipt = json.loads(published.receipt_path.read_text(encoding="utf-8"))
    assert receipt["old_head_sha"] == fixture.feature_sha
    assert receipt["pinned_base_sha"] == fixture.main_sha
    assert receipt["merge_base_sha"] == fixture.base_sha
    assert receipt["new_head_sha"] == published.new_head_sha
    assert receipt["branch"] == BRANCH
    assert receipt["remote_name"] == "origin"
    assert receipt["remote_repo_slug"] == REPO_SLUG
    assert receipt["remote_url_kind"] == "local_path"
    assert receipt["conflict_paths"] == []


def test_publish_uses_live_branch_head_not_historical_pr_base_sha(
    tmp_path: Path,
) -> None:
    fixture = _repo_fixture(tmp_path, mode="clean")
    result, runner, _client = _prepare_ready(fixture)
    assert result.workspace_path is not None

    client = LocalRemoteGitHubClient(
        fixture,
        base_sha_override=fixture.base_sha,
    )
    published = publish_pr_rebase(
        workspace_path=result.workspace_path,
        repo_root=fixture.primary,
        github_client=client,
        git_runner=runner,
        now=NOW,
        sleep=lambda _seconds: None,
    )

    assert published.outcome is PRRebaseOutcome.PUBLISHED
    assert ("branch_head", OWNER, REPO, "main") in client.calls
    assert _git_stdout(fixture.remote, "rev-parse", f"refs/heads/{BRANCH}") == (
        published.new_head_sha
    )


def test_publish_pending_retry_pushes_when_remote_still_equals_old_head(
    tmp_path: Path,
) -> None:
    fixture = _repo_fixture(tmp_path, mode="clean")
    result, runner, client = _prepare_ready(fixture)
    assert result.workspace_path is not None
    workspace = result.workspace_path
    payload = _manifest_payload(workspace)
    payload["state"] = PRRebaseState.LEASE_PUSH_PENDING.value
    payload["remote_name"] = "origin"
    payload["remote_repo_slug"] = REPO_SLUG
    payload["remote_url_kind"] = "local_path"
    payload["pending_push_expected_old_head_sha"] = fixture.feature_sha
    payload["pending_push_rebased_head_sha"] = result.new_head_sha
    _write_manifest_payload(workspace, payload)

    published = publish_pr_rebase(
        workspace_path=workspace,
        repo_root=fixture.primary,
        github_client=client,
        git_runner=runner,
        now=NOW,
        sleep=lambda _seconds: None,
    )

    assert published.outcome is PRRebaseOutcome.PUBLISHED
    assert _push_calls(runner) == [
        (
            "push",
            f"--force-with-lease=refs/heads/{BRANCH}:{fixture.feature_sha}",
            str(fixture.remote),
            f"{result.new_head_sha}:refs/heads/{BRANCH}",
        )
    ]


def test_publish_recovers_lease_pending_after_crash_immediately_after_push(
    tmp_path: Path,
) -> None:
    fixture = _repo_fixture(tmp_path, mode="clean")

    def crash() -> None:
        raise RuntimeError("crash after push")

    runner = RecordingGitRunner(after_push=crash)
    result, runner, client = _prepare_ready(fixture, runner=runner)
    assert result.workspace_path is not None
    workspace = result.workspace_path

    with pytest.raises(RuntimeError, match="crash after push"):
        publish_pr_rebase(
            workspace_path=workspace,
            repo_root=fixture.primary,
            github_client=client,
            git_runner=runner,
            now=NOW,
            sleep=lambda _seconds: None,
        )

    assert (
        _git_stdout(fixture.remote, "rev-parse", f"refs/heads/{BRANCH}")
        == result.new_head_sha
    )
    payload = _manifest_payload(workspace)
    assert payload["state"] == PRRebaseState.LEASE_PUSH_PENDING.value
    assert payload["pending_push_expected_old_head_sha"] == fixture.feature_sha
    assert payload["pending_push_rebased_head_sha"] == result.new_head_sha
    assert payload["remote_repo_slug"] == REPO_SLUG

    published = publish_pr_rebase(
        workspace_path=workspace,
        repo_root=fixture.primary,
        github_client=client,
        git_runner=runner,
        now=NOW,
        sleep=lambda _seconds: None,
    )

    assert published.outcome is PRRebaseOutcome.PUBLISHED
    assert not workspace.exists()
    assert len(_push_calls(runner)) == 1


def test_publish_refuses_origin_push_repository_mismatch_before_push(
    tmp_path: Path,
) -> None:
    fixture = _repo_fixture(tmp_path, mode="clean")
    result, runner, client = _prepare_ready(fixture)
    assert result.workspace_path is not None
    wrong_remote = tmp_path / "fork" / f"{REPO}.git"
    wrong_remote.parent.mkdir()
    _git(tmp_path, "init", "--bare", str(wrong_remote))
    _git(fixture.primary, "remote", "set-url", "--push", "origin", str(wrong_remote))

    with pytest.raises(PRRebaseRefusal, match="origin push URL targets fork/atlas"):
        publish_pr_rebase(
            workspace_path=result.workspace_path,
            repo_root=fixture.primary,
            github_client=client,
            git_runner=runner,
            now=NOW,
            sleep=lambda _seconds: None,
        )

    assert _push_calls(runner) == []
    assert (
        _git_stdout(fixture.remote, "rev-parse", f"refs/heads/{BRANCH}")
        == fixture.feature_sha
    )


def test_publish_refuses_additional_unvalidated_origin_pushurl_before_push(
    tmp_path: Path,
) -> None:
    fixture = _repo_fixture(tmp_path, mode="clean")
    result, runner, client = _prepare_ready(fixture)
    assert result.workspace_path is not None
    wrong_remote = tmp_path / "fork" / f"{REPO}.git"
    wrong_remote.parent.mkdir()
    _git(tmp_path, "init", "--bare", str(wrong_remote))
    _git(
        fixture.seed,
        "push",
        str(wrong_remote),
        "main:main",
        f"{BRANCH}:{BRANCH}",
    )
    _git(
        fixture.primary,
        "remote",
        "set-url",
        "--add",
        "--push",
        "origin",
        str(fixture.remote),
    )
    _git(
        fixture.primary,
        "remote",
        "set-url",
        "--add",
        "--push",
        "origin",
        str(wrong_remote),
    )

    with pytest.raises(PRRebaseRefusal, match="origin push URL targets fork/atlas"):
        publish_pr_rebase(
            workspace_path=result.workspace_path,
            repo_root=fixture.primary,
            github_client=client,
            git_runner=runner,
            now=NOW,
            sleep=lambda _seconds: None,
        )

    assert _push_calls(runner) == []
    assert (
        _git_stdout(fixture.remote, "rev-parse", f"refs/heads/{BRANCH}")
        == fixture.feature_sha
    )
    assert (
        _git_stdout(wrong_remote, "rev-parse", f"refs/heads/{BRANCH}")
        == fixture.feature_sha
    )


def test_publish_from_unverified_state_reverifies_without_repeating_old_head_push(
    tmp_path: Path,
) -> None:
    fixture = _repo_fixture(tmp_path, mode="clean")
    result, runner, client = _prepare_ready(fixture)
    assert result.workspace_path is not None
    workspace = result.workspace_path

    unverified = publish_pr_rebase(
        workspace_path=workspace,
        repo_root=fixture.primary,
        github_client=client,
        git_runner=runner,
        now=NOW,
        sleep=lambda _seconds: None,
        verify_attempts=0,
    )
    assert unverified.outcome is PRRebaseOutcome.PUSH_SUCCEEDED_UNVERIFIED
    assert (
        _manifest_payload(workspace)["state"]
        == PRRebaseState.PUSH_SUCCEEDED_UNVERIFIED.value
    )

    published = publish_pr_rebase(
        workspace_path=workspace,
        repo_root=fixture.primary,
        github_client=client,
        git_runner=runner,
        now=NOW,
        sleep=lambda _seconds: None,
    )

    assert published.outcome is PRRebaseOutcome.PUBLISHED
    assert not workspace.exists()
    assert len(_push_calls(runner)) == 1


def test_last_moment_remote_head_race_lease_rejection_changes_no_remote_ref(
    tmp_path: Path,
) -> None:
    fixture = _repo_fixture(tmp_path, mode="clean")
    race_sha = ""

    def race() -> None:
        nonlocal race_sha
        _git(fixture.seed, "checkout", BRANCH)
        race_sha = _commit_paths(fixture.seed, {"last-moment.txt": "race\n"}, "race")
        _git(fixture.seed, "push", "origin", f"{BRANCH}:{BRANCH}")

    runner = RecordingGitRunner(before_push=race)
    result, runner, client = _prepare_ready(fixture, runner=runner)
    assert result.workspace_path is not None

    refused = publish_pr_rebase(
        workspace_path=result.workspace_path,
        repo_root=fixture.primary,
        github_client=client,
        git_runner=runner,
        now=NOW,
        sleep=lambda _seconds: None,
    )

    assert refused.outcome is PRRebaseOutcome.REFUSED
    assert _git_stdout(fixture.remote, "rev-parse", f"refs/heads/{BRANCH}") == race_sha
    assert (
        _manifest_payload(result.workspace_path)["state"]
        == PRRebaseState.LEASE_PUSH_PENDING.value
    )
    with pytest.raises(PRRebaseRefusal, match="expected one of"):
        publish_pr_rebase(
            workspace_path=result.workspace_path,
            repo_root=fixture.primary,
            github_client=client,
            git_runner=runner,
            now=NOW,
            sleep=lambda _seconds: None,
        )
    assert len(_push_calls(runner)) == 1


def test_abort_aborts_in_progress_rebase_and_removes_named_worktree_through_git(
    tmp_path: Path,
) -> None:
    fixture = _repo_fixture(tmp_path, mode="conflict")
    client = LocalRemoteGitHubClient(fixture, mergeable=False)
    runner = RecordingGitRunner()
    result = prepare_pr_rebase(
        repo_slug=REPO_SLUG,
        pr_number=PR_NUMBER,
        repo_root=fixture.primary,
        github_client=client,
        ticket_lookup=_ready_lookup(),
        git_runner=runner,
        now=NOW,
    )
    assert result.workspace_path is not None
    workspace = result.workspace_path

    aborted = abort_pr_rebase(
        workspace_path=workspace,
        repo_root=fixture.primary,
        git_runner=runner,
        now=NOW,
    )

    assert aborted.outcome is PRRebaseOutcome.ABORTED
    assert not workspace.exists()
    assert (
        _git_stdout(fixture.remote, "rev-parse", f"refs/heads/{BRANCH}")
        == fixture.feature_sha
    )
    assert ("rebase", "--abort") in [argv for _cwd, argv in runner.calls]
    assert any(
        argv[:3] == ("worktree", "remove", "--force") for _cwd, argv in runner.calls
    )


def test_abort_refuses_traversal_symlink_foreign_manifest_and_publish_receipts(
    tmp_path: Path,
) -> None:
    fixture = _repo_fixture(tmp_path, mode="clean")
    result, runner, _client = _prepare_ready(fixture)
    assert result.workspace_path is not None
    workspace = result.workspace_path
    workspace_root = fixture.primary / ".atlas" / "rebase-workspaces"

    with pytest.raises(PRRebasePreconditionError):
        abort_pr_rebase(
            workspace_path=fixture.primary,
            repo_root=fixture.primary,
            git_runner=runner,
            now=NOW,
        )

    with pytest.raises(PRRebasePreconditionError):
        abort_pr_rebase(
            workspace_path=workspace_root / "..",
            repo_root=fixture.primary,
            git_runner=runner,
            now=NOW,
        )

    escaped = tmp_path / "escaped"
    escaped.mkdir()
    symlink = workspace_root / "escape-link"
    symlink.symlink_to(escaped, target_is_directory=True)
    with pytest.raises(PRRebasePreconditionError):
        abort_pr_rebase(
            workspace_path=symlink,
            repo_root=fixture.primary,
            git_runner=runner,
            now=NOW,
        )
    assert symlink.exists()

    missing_manifest = workspace_root / "missing-manifest"
    missing_manifest.mkdir()
    with pytest.raises(PRRebasePreconditionError):
        abort_pr_rebase(
            workspace_path=missing_manifest,
            repo_root=fixture.primary,
            git_runner=runner,
            now=NOW,
        )
    assert missing_manifest.exists()

    foreign = workspace_root / "foreign"
    foreign.mkdir()
    (foreign / MANIFEST_FILENAME).write_text(
        json.dumps({"schema_version": 1, "kind": "foreign"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(PRRebasePreconditionError):
        abort_pr_rebase(
            workspace_path=foreign,
            repo_root=fixture.primary,
            git_runner=runner,
            now=NOW,
        )
    assert foreign.exists()

    receipt = fixture.primary / ".atlas" / "rebase-receipts" / "published.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text("{}\n", encoding="utf-8")
    payload = _manifest_payload(workspace)
    payload["receipt_path"] = str(receipt)
    _write_manifest_payload(workspace, payload)
    with pytest.raises(PRRebaseRefusal, match="receipt"):
        abort_pr_rebase(
            workspace_path=workspace,
            repo_root=fixture.primary,
            git_runner=runner,
            now=NOW,
        )
    assert workspace.exists()


def test_publish_refuses_changed_branch_repo_identity_and_incomplete_snapshot(
    tmp_path: Path,
) -> None:
    fixture = _repo_fixture(tmp_path, mode="clean")
    result, runner, _client = _prepare_ready(fixture)
    assert result.workspace_path is not None
    workspace = result.workspace_path

    cases = [
        LocalRemoteGitHubClient(fixture, head_ref="renamed/rebase-lane"),
        LocalRemoteGitHubClient(fixture, head_repo="fork/atlas"),
        LocalRemoteGitHubClient(fixture, base_repo="other/atlas"),
        LocalRemoteGitHubClient(fixture, state="closed"),
        LocalRemoteGitHubClient(fixture, draft=True),
    ]
    for client in cases:
        with pytest.raises(PRRebaseRefusal):
            publish_pr_rebase(
                workspace_path=workspace,
                repo_root=fixture.primary,
                github_client=client,
                git_runner=runner,
                now=NOW,
                sleep=lambda _seconds: None,
            )

    class IncompleteClient(LocalRemoteGitHubClient):
        def fetch_pull_request(
            self, owner: str, repo: str, pr_number: int
        ) -> dict[str, Any]:
            payload = super().fetch_pull_request(owner, repo, pr_number)
            del payload["head"]["sha"]
            return payload

    with pytest.raises(PRRebaseRefusal):
        publish_pr_rebase(
            workspace_path=workspace,
            repo_root=fixture.primary,
            github_client=IncompleteClient(fixture),
            git_runner=runner,
            now=NOW,
            sleep=lambda _seconds: None,
        )
    assert _push_calls(runner) == []
    assert workspace.exists()

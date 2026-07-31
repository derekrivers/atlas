"""Operator-owned, lease-guarded PR rebase lane.

The command family built on this module is deliberately narrow: it creates a
detached linked worktree for one mechanically stale same-repository PR, lets the
operator resolve conflicts there, and performs one explicit lease-protected push
only after the live PR head and base still match the originally assessed SHAs.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlparse

from atlas.core.models import Ticket, TicketStatus
from atlas.github import GitHubAPIError, GitHubClient
from atlas.orchestration.pr_integration import (
    PRIntegrationAssessment,
    PRIntegrationStatus,
    assess_pr_integration,
)
from atlas.verification import parse_close_set

MANIFEST_FILENAME = ".atlas-rebase-manifest.json"
MANIFEST_KIND = "atlas_pr_rebase_manifest"
MANIFEST_VERSION = 1
WORKSPACE_ROOT = Path(".atlas") / "rebase-workspaces"
RECEIPT_ROOT = Path(".atlas") / "rebase-receipts"
REMOTE_NAME = "origin"
DEFAULT_VERIFY_ATTEMPTS = 3
VERIFY_BACKOFF_SECONDS = 1.0

_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class PRRebaseState(StrEnum):
    PREPARED = "prepared"
    CONFLICTS_PENDING = "conflicts_pending"
    READY_TO_PUBLISH = "ready_to_publish"
    LEASE_PUSH_PENDING = "lease_push_pending"
    PUSH_SUCCEEDED_UNVERIFIED = "push_succeeded_unverified"
    PUBLISHED = "published"


class PRRebaseOutcome(StrEnum):
    READY_TO_PUBLISH = "ready_to_publish"
    CONFLICTS_PENDING = "conflicts_pending"
    NOOP_CURRENT = "noop_current"
    REFUSED = "refused"
    PUSH_SUCCEEDED_UNVERIFIED = "push_succeeded_unverified"
    PUBLISHED = "published"
    ABORTED = "aborted"


class PRRebaseError(RuntimeError):
    """Base class for clean PR rebase command failures."""


class PRRebasePreconditionError(PRRebaseError):
    """A setup or path-safety precondition failed before command work."""


class PRRebaseRefusal(PRRebaseError):
    """A live safety gate refused the requested PR rebase action."""


class GitRunner(Protocol):
    def __call__(
        self,
        cwd: Path,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run one argv-based Git command and return the completed process."""


class TicketLookup(Protocol):
    def get_by_key(self, key: str) -> Ticket | None:
        """Return one stored ticket by key, or None when absent."""


class _Unchanged:
    pass


_UNCHANGED = _Unchanged()


@dataclass(frozen=True)
class ConflictSet:
    timestamp: str
    paths: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        return {"timestamp": self.timestamp, "paths": list(self.paths)}

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> ConflictSet:
        timestamp = _required_str(payload, "timestamp", label="conflict set")
        raw_paths = payload.get("paths")
        if not isinstance(raw_paths, list) or not all(
            isinstance(path, str) for path in raw_paths
        ):
            raise PRRebasePreconditionError("rebase manifest conflict paths invalid")
        return cls(timestamp=timestamp, paths=tuple(raw_paths))


@dataclass(frozen=True)
class StateTransition:
    timestamp: str
    state: PRRebaseState
    reason: str

    def to_json(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "state": self.state.value,
            "reason": self.reason,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> StateTransition:
        timestamp = _required_str(payload, "timestamp", label="state transition")
        state = _state_from_str(
            _required_str(payload, "state", label="state transition")
        )
        reason = _required_str(payload, "reason", label="state transition")
        return cls(timestamp=timestamp, state=state, reason=reason)


@dataclass(frozen=True)
class RebaseManifest:
    schema_version: int
    kind: str
    repo_slug: str
    repo_root: Path
    pr_number: int
    head_ref: str
    head_branch_ref: str
    original_head_sha: str
    base_ref: str
    pinned_base_sha: str
    merge_base_sha: str
    workspace_path: Path
    state: PRRebaseState
    created_at: str
    updated_at: str
    conflict_sets: tuple[ConflictSet, ...]
    transitions: tuple[StateTransition, ...]
    rebased_head_sha: str | None = None
    receipt_path: Path | None = None
    remote_name: str | None = None
    remote_repo_slug: str | None = None
    remote_url_kind: str | None = None
    pending_push_expected_old_head_sha: str | None = None
    pending_push_rebased_head_sha: str | None = None

    @property
    def manifest_path(self) -> Path:
        return self.workspace_path / MANIFEST_FILENAME

    @property
    def branch_name(self) -> str:
        return self.head_ref

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "repo_slug": self.repo_slug,
            "repo_root": str(self.repo_root),
            "pr_number": self.pr_number,
            "head_ref": self.head_ref,
            "head_branch_ref": self.head_branch_ref,
            "original_head_sha": self.original_head_sha,
            "base_ref": self.base_ref,
            "pinned_base_sha": self.pinned_base_sha,
            "merge_base_sha": self.merge_base_sha,
            "workspace_path": str(self.workspace_path),
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "conflict_sets": [entry.to_json() for entry in self.conflict_sets],
            "transitions": [entry.to_json() for entry in self.transitions],
            "rebased_head_sha": self.rebased_head_sha,
            "receipt_path": (
                None if self.receipt_path is None else str(self.receipt_path)
            ),
            "remote_name": self.remote_name,
            "remote_repo_slug": self.remote_repo_slug,
            "remote_url_kind": self.remote_url_kind,
            "pending_push_expected_old_head_sha": (
                self.pending_push_expected_old_head_sha
            ),
            "pending_push_rebased_head_sha": self.pending_push_rebased_head_sha,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> RebaseManifest:
        schema_version = _required_int(payload, "schema_version", label="manifest")
        if schema_version != MANIFEST_VERSION:
            raise PRRebasePreconditionError(
                f"unsupported rebase manifest version {schema_version}"
            )
        kind = _required_str(payload, "kind", label="manifest")
        if kind != MANIFEST_KIND:
            raise PRRebasePreconditionError(
                "workspace does not contain an Atlas PR rebase manifest"
            )
        raw_conflicts = payload.get("conflict_sets")
        if not isinstance(raw_conflicts, list):
            raise PRRebasePreconditionError("rebase manifest conflict_sets invalid")
        raw_transitions = payload.get("transitions")
        if not isinstance(raw_transitions, list):
            raise PRRebasePreconditionError("rebase manifest transitions invalid")
        rebased_head = _optional_str(payload, "rebased_head_sha", label="manifest")
        if rebased_head is not None:
            _require_sha_value(rebased_head, label="manifest rebased_head_sha")
        receipt = _optional_str(payload, "receipt_path", label="manifest")
        remote_name = _optional_str(payload, "remote_name", label="manifest")
        remote_repo_slug = _optional_str(payload, "remote_repo_slug", label="manifest")
        remote_url_kind = _optional_str(payload, "remote_url_kind", label="manifest")
        pending_old_head = _optional_str(
            payload,
            "pending_push_expected_old_head_sha",
            label="manifest",
        )
        if pending_old_head is not None:
            _require_sha_value(
                pending_old_head,
                label="manifest pending_push_expected_old_head_sha",
            )
        pending_rebased_head = _optional_str(
            payload,
            "pending_push_rebased_head_sha",
            label="manifest",
        )
        if pending_rebased_head is not None:
            _require_sha_value(
                pending_rebased_head,
                label="manifest pending_push_rebased_head_sha",
            )
        merge_base_sha = _required_str(payload, "merge_base_sha", label="manifest")
        if not _is_sha(merge_base_sha):
            raise PRRebasePreconditionError("manifest merge_base_sha was not a SHA")
        conflict_sets = tuple(
            ConflictSet.from_json(cast(Mapping[str, Any], entry))
            for entry in raw_conflicts
            if isinstance(entry, dict)
        )
        transitions = tuple(
            StateTransition.from_json(cast(Mapping[str, Any], entry))
            for entry in raw_transitions
            if isinstance(entry, dict)
        )
        if len(conflict_sets) != len(raw_conflicts):
            raise PRRebasePreconditionError("rebase manifest conflict_sets invalid")
        if len(transitions) != len(raw_transitions):
            raise PRRebasePreconditionError("rebase manifest transitions invalid")
        state = _state_from_str(_required_str(payload, "state", label="manifest"))
        manifest = cls(
            schema_version=schema_version,
            kind=kind,
            repo_slug=_required_str(payload, "repo_slug", label="manifest"),
            repo_root=Path(_required_str(payload, "repo_root", label="manifest")),
            pr_number=_required_positive_int(payload, "pr_number", label="manifest"),
            head_ref=_required_str(payload, "head_ref", label="manifest"),
            head_branch_ref=_required_str(payload, "head_branch_ref", label="manifest"),
            original_head_sha=_required_sha(payload, "original_head_sha"),
            base_ref=_required_str(payload, "base_ref", label="manifest"),
            pinned_base_sha=_required_sha(payload, "pinned_base_sha"),
            merge_base_sha=merge_base_sha,
            workspace_path=Path(
                _required_str(payload, "workspace_path", label="manifest")
            ),
            state=state,
            created_at=_required_str(payload, "created_at", label="manifest"),
            updated_at=_required_str(payload, "updated_at", label="manifest"),
            conflict_sets=conflict_sets,
            transitions=transitions,
            rebased_head_sha=rebased_head,
            receipt_path=None if receipt is None else Path(receipt),
            remote_name=remote_name,
            remote_repo_slug=remote_repo_slug,
            remote_url_kind=remote_url_kind,
            pending_push_expected_old_head_sha=pending_old_head,
            pending_push_rebased_head_sha=pending_rebased_head,
        )
        if manifest.state is PRRebaseState.LEASE_PUSH_PENDING and (
            manifest.pending_push_expected_old_head_sha != manifest.original_head_sha
            or manifest.pending_push_rebased_head_sha != manifest.rebased_head_sha
            or manifest.remote_name is None
            or manifest.remote_repo_slug is None
            or manifest.remote_url_kind is None
        ):
            raise PRRebasePreconditionError(
                "lease-pending manifest is missing expected push identity"
            )
        return manifest

    def transition(
        self,
        state: PRRebaseState,
        *,
        timestamp: str,
        reason: str,
        rebased_head_sha: str | None | object = _UNCHANGED,
        receipt_path: Path | None | object = _UNCHANGED,
        remote_identity: _RemoteIdentity | None | object = _UNCHANGED,
        pending_push_expected_old_head_sha: str | None | object = _UNCHANGED,
        pending_push_rebased_head_sha: str | None | object = _UNCHANGED,
        conflict_paths: Sequence[str] | None = None,
    ) -> RebaseManifest:
        conflicts = self.conflict_sets
        if conflict_paths is not None:
            conflicts = (
                *conflicts,
                ConflictSet(timestamp=timestamp, paths=tuple(conflict_paths)),
            )
        return replace(
            self,
            state=state,
            updated_at=timestamp,
            transitions=(
                *self.transitions,
                StateTransition(timestamp=timestamp, state=state, reason=reason),
            ),
            conflict_sets=conflicts,
            rebased_head_sha=(
                self.rebased_head_sha
                if rebased_head_sha is _UNCHANGED
                else cast(str | None, rebased_head_sha)
            ),
            receipt_path=(
                self.receipt_path
                if receipt_path is _UNCHANGED
                else cast(Path | None, receipt_path)
            ),
            remote_name=(
                self.remote_name
                if remote_identity is _UNCHANGED
                else (
                    None
                    if remote_identity is None
                    else cast(_RemoteIdentity, remote_identity).remote_name
                )
            ),
            remote_repo_slug=(
                self.remote_repo_slug
                if remote_identity is _UNCHANGED
                else (
                    None
                    if remote_identity is None
                    else cast(_RemoteIdentity, remote_identity).repo_slug
                )
            ),
            remote_url_kind=(
                self.remote_url_kind
                if remote_identity is _UNCHANGED
                else (
                    None
                    if remote_identity is None
                    else cast(_RemoteIdentity, remote_identity).url_kind
                )
            ),
            pending_push_expected_old_head_sha=(
                self.pending_push_expected_old_head_sha
                if pending_push_expected_old_head_sha is _UNCHANGED
                else cast(str | None, pending_push_expected_old_head_sha)
            ),
            pending_push_rebased_head_sha=(
                self.pending_push_rebased_head_sha
                if pending_push_rebased_head_sha is _UNCHANGED
                else cast(str | None, pending_push_rebased_head_sha)
            ),
        )


@dataclass(frozen=True)
class PRRebaseResult:
    outcome: PRRebaseOutcome
    message: str
    workspace_path: Path | None
    manifest_path: Path | None
    state: PRRebaseState | None
    old_head_sha: str | None = None
    pinned_base_sha: str | None = None
    merge_base_sha: str | None = None
    new_head_sha: str | None = None
    branch: str | None = None
    tickets: tuple[str, ...] = ()
    conflict_paths: tuple[str, ...] = ()
    receipt_path: Path | None = None


@dataclass(frozen=True)
class _LivePRSnapshot:
    number: int
    state: str
    draft: bool
    merged: bool
    head_ref: str
    head_sha: str
    head_repository: str
    base_ref: str
    base_sha: str
    base_repository: str


@dataclass(frozen=True)
class _RemoteIdentity:
    remote_name: str
    push_url: str
    repo_slug: str
    url_kind: str


@dataclass(frozen=True)
class _PublishPreconditions:
    remote_identity: _RemoteIdentity
    remote_head_sha: str
    remote_base_sha: str


def run_git(
    cwd: Path,
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run Git with argv and ``shell=False``."""
    merged_env = os.environ.copy()
    merged_env.setdefault("GIT_TERMINAL_PROMPT", "0")
    if env is not None:
        merged_env.update(env)
    return subprocess.run(
        ["git", *argv],
        cwd=cwd,
        env=merged_env,
        capture_output=True,
        text=True,
        shell=False,
    )


def prepare_pr_rebase(
    *,
    repo_slug: str,
    pr_number: int,
    repo_root: Path,
    github_client: GitHubClient,
    ticket_lookup: TicketLookup,
    git_runner: GitRunner = run_git,
    now: datetime | None = None,
) -> PRRebaseResult:
    """Assess, gate, create a detached worktree, and start the pinned rebase."""
    owner, repo = _split_repo_slug(repo_slug)
    canonical_root = _canonical_repo_root(repo_root, git_runner=git_runner)
    timestamp = _timestamp(now)

    try:
        assessment = assess_pr_integration(github_client, owner, repo, pr_number)
    except GitHubAPIError as error:
        raise PRRebasePreconditionError(str(error)) from error

    close_set = parse_close_set(assessment.pr_title, assessment.pr_body)
    refusal = _prepare_gate_refusal(assessment, close_set, ticket_lookup)
    if refusal == "current":
        return PRRebaseResult(
            outcome=PRRebaseOutcome.NOOP_CURRENT,
            message=(
                f"PR #{pr_number} is already current with main; no rebase "
                "workspace created."
            ),
            workspace_path=None,
            manifest_path=None,
            state=None,
            old_head_sha=assessment.head_sha,
            pinned_base_sha=assessment.base_sha,
            merge_base_sha=assessment.merge_base_sha,
            branch=assessment.head_ref,
            tickets=close_set,
        )
    if refusal is not None:
        return PRRebaseResult(
            outcome=PRRebaseOutcome.REFUSED,
            message=refusal,
            workspace_path=None,
            manifest_path=None,
            state=None,
            old_head_sha=assessment.head_sha,
            pinned_base_sha=assessment.base_sha,
            merge_base_sha=assessment.merge_base_sha,
            branch=assessment.head_ref,
            tickets=close_set,
        )

    branch = _validate_head_ref(assessment.head_ref)
    workspace_root = _ensure_workspace_root(canonical_root, create=True)
    workspace = _workspace_path(
        workspace_root,
        repo_slug=repo_slug,
        pr_number=pr_number,
        head_sha=assessment.head_sha,
    )
    if workspace.exists():
        return PRRebaseResult(
            outcome=PRRebaseOutcome.REFUSED,
            message=(
                f"rebase workspace already exists at {workspace}; continue, "
                "publish, or abort it before preparing another."
            ),
            workspace_path=workspace,
            manifest_path=workspace / MANIFEST_FILENAME,
            state=None,
            old_head_sha=assessment.head_sha,
            pinned_base_sha=assessment.base_sha,
            merge_base_sha=assessment.merge_base_sha,
            branch=branch,
            tickets=close_set,
        )

    _fetch_branch_refs(canonical_root, branch, git_runner=git_runner)
    remote_head = _rev_parse(
        canonical_root,
        f"refs/remotes/{REMOTE_NAME}/{branch}",
        git_runner=git_runner,
    )
    remote_base = _rev_parse(
        canonical_root,
        f"refs/remotes/{REMOTE_NAME}/{assessment.base_ref}",
        git_runner=git_runner,
    )
    if remote_head != assessment.head_sha:
        return PRRebaseResult(
            outcome=PRRebaseOutcome.REFUSED,
            message=(
                f"remote PR head moved before workspace creation: expected "
                f"{assessment.head_sha}, got {remote_head}."
            ),
            workspace_path=None,
            manifest_path=None,
            state=None,
            old_head_sha=assessment.head_sha,
            pinned_base_sha=assessment.base_sha,
            merge_base_sha=assessment.merge_base_sha,
            branch=branch,
            tickets=close_set,
        )
    if remote_base != assessment.base_sha:
        return PRRebaseResult(
            outcome=PRRebaseOutcome.REFUSED,
            message=(
                f"remote main moved before workspace creation: expected "
                f"{assessment.base_sha}, got {remote_base}."
            ),
            workspace_path=None,
            manifest_path=None,
            state=None,
            old_head_sha=assessment.head_sha,
            pinned_base_sha=assessment.base_sha,
            merge_base_sha=assessment.merge_base_sha,
            branch=branch,
            tickets=close_set,
        )

    add_result = git_runner(
        canonical_root,
        ["worktree", "add", "--detach", str(workspace), assessment.head_sha],
    )
    if add_result.returncode != 0:
        raise PRRebasePreconditionError(_git_failed("worktree add", add_result))

    manifest = RebaseManifest(
        schema_version=MANIFEST_VERSION,
        kind=MANIFEST_KIND,
        repo_slug=repo_slug,
        repo_root=canonical_root,
        pr_number=pr_number,
        head_ref=branch,
        head_branch_ref=f"refs/heads/{branch}",
        original_head_sha=assessment.head_sha,
        base_ref=assessment.base_ref,
        pinned_base_sha=assessment.base_sha,
        merge_base_sha=assessment.merge_base_sha or assessment.head_sha,
        workspace_path=workspace,
        state=PRRebaseState.PREPARED,
        created_at=timestamp,
        updated_at=timestamp,
        conflict_sets=(),
        transitions=(
            StateTransition(
                timestamp=timestamp,
                state=PRRebaseState.PREPARED,
                reason="detached worktree created at assessed PR head",
            ),
        ),
        rebased_head_sha=None,
        receipt_path=None,
    )
    _write_manifest(manifest)

    rebase_result = git_runner(workspace, _rebase_argv(assessment.base_sha))
    if rebase_result.returncode == 0:
        new_head = _rev_parse(workspace, "HEAD", git_runner=git_runner)
        ready = manifest.transition(
            PRRebaseState.READY_TO_PUBLISH,
            timestamp=_timestamp(now),
            reason="clean rebase completed",
            rebased_head_sha=new_head,
        )
        _write_manifest(ready)
        return _manifest_result(
            ready,
            outcome=PRRebaseOutcome.READY_TO_PUBLISH,
            message=f"rebase workspace is ready to publish at {new_head}.",
            tickets=close_set,
        )

    conflicts = _unmerged_paths(workspace, git_runner=git_runner)
    if conflicts:
        pending = manifest.transition(
            PRRebaseState.CONFLICTS_PENDING,
            timestamp=_timestamp(now),
            reason="rebase stopped on conflicts",
            conflict_paths=conflicts,
        )
        _write_manifest(pending)
        return _manifest_result(
            pending,
            outcome=PRRebaseOutcome.CONFLICTS_PENDING,
            message="rebase stopped with conflicts; resolve and stage them.",
            tickets=close_set,
            conflict_paths=conflicts,
        )

    raise PRRebasePreconditionError(_git_failed("rebase", rebase_result))


def continue_pr_rebase(
    *,
    workspace_path: Path,
    repo_root: Path,
    git_runner: GitRunner = run_git,
    now: datetime | None = None,
) -> PRRebaseResult:
    """Continue a stopped rebase after the operator has staged resolutions."""
    manifest = load_manifest(
        workspace_path,
        repo_root=repo_root,
        git_runner=git_runner,
    )
    if manifest.state is not PRRebaseState.CONFLICTS_PENDING:
        raise PRRebaseRefusal(
            f"workspace is {manifest.state.value}, not conflicts_pending."
        )

    unresolved = _unmerged_paths(manifest.workspace_path, git_runner=git_runner)
    if unresolved:
        return _manifest_result(
            manifest,
            outcome=PRRebaseOutcome.REFUSED,
            message="continue refused: unresolved index entries remain.",
            conflict_paths=unresolved,
        )

    result = git_runner(
        manifest.workspace_path,
        _rebase_argv("--continue"),
        env={
            "GIT_EDITOR": "true",
            "GIT_SEQUENCE_EDITOR": "true",
            "GIT_TERMINAL_PROMPT": "0",
        },
    )
    if result.returncode == 0:
        new_head = _rev_parse(manifest.workspace_path, "HEAD", git_runner=git_runner)
        ready = manifest.transition(
            PRRebaseState.READY_TO_PUBLISH,
            timestamp=_timestamp(now),
            reason="rebase continue completed",
            rebased_head_sha=new_head,
        )
        _write_manifest(ready)
        return _manifest_result(
            ready,
            outcome=PRRebaseOutcome.READY_TO_PUBLISH,
            message=f"rebase workspace is ready to publish at {new_head}.",
        )

    conflicts = _unmerged_paths(manifest.workspace_path, git_runner=git_runner)
    if conflicts:
        pending = manifest.transition(
            PRRebaseState.CONFLICTS_PENDING,
            timestamp=_timestamp(now),
            reason="rebase continue stopped on conflicts",
            conflict_paths=conflicts,
        )
        _write_manifest(pending)
        return _manifest_result(
            pending,
            outcome=PRRebaseOutcome.CONFLICTS_PENDING,
            message="rebase stopped again with conflicts; resolve and stage them.",
            conflict_paths=conflicts,
        )

    raise PRRebasePreconditionError(_git_failed("rebase --continue", result))


def publish_pr_rebase(
    *,
    workspace_path: Path,
    repo_root: Path,
    github_client: GitHubClient,
    git_runner: GitRunner = run_git,
    now: datetime | None = None,
    sleep: Callable[[float], None] = time.sleep,
    verify_attempts: int = DEFAULT_VERIFY_ATTEMPTS,
) -> PRRebaseResult:
    """Publish a completed rebase with an explicit expected-SHA lease."""
    manifest = load_manifest(
        workspace_path,
        repo_root=repo_root,
        git_runner=git_runner,
    )
    if manifest.state is PRRebaseState.PUBLISHED:
        if manifest.receipt_path is None or not manifest.receipt_path.exists():
            raise PRRebaseRefusal("published manifest has no durable receipt.")
        _remove_worktree(manifest, git_runner=git_runner)
        return _manifest_result(
            manifest,
            outcome=PRRebaseOutcome.PUBLISHED,
            message=f"published receipt already exists at {manifest.receipt_path}.",
            receipt_path=manifest.receipt_path,
        )
    if manifest.state is PRRebaseState.PUSH_SUCCEEDED_UNVERIFIED:
        return _verify_and_cleanup(
            manifest,
            github_client=github_client,
            git_runner=git_runner,
            now=now,
            sleep=sleep,
            verify_attempts=verify_attempts,
        )
    if manifest.state is PRRebaseState.LEASE_PUSH_PENDING:
        return _resume_pending_publish(
            manifest,
            github_client=github_client,
            git_runner=git_runner,
            now=now,
            sleep=sleep,
            verify_attempts=verify_attempts,
        )
    if manifest.state is not PRRebaseState.READY_TO_PUBLISH:
        raise PRRebaseRefusal(
            f"workspace is {manifest.state.value}, not ready_to_publish."
        )
    if manifest.rebased_head_sha is None:
        raise PRRebaseRefusal("manifest has no rebased head to publish.")

    current_head = _rev_parse(manifest.workspace_path, "HEAD", git_runner=git_runner)
    if current_head != manifest.rebased_head_sha:
        raise PRRebaseRefusal(
            f"workspace HEAD changed from manifest rebased head: expected "
            f"{manifest.rebased_head_sha}, got {current_head}."
        )

    preconditions = _publish_preconditions(
        manifest,
        github_client=github_client,
        git_runner=git_runner,
        allowed_head_shas=(manifest.original_head_sha,),
    )
    pending = manifest.transition(
        PRRebaseState.LEASE_PUSH_PENDING,
        timestamp=_timestamp(now),
        reason="lease-protected push about to start",
        remote_identity=preconditions.remote_identity,
        pending_push_expected_old_head_sha=manifest.original_head_sha,
        pending_push_rebased_head_sha=manifest.rebased_head_sha,
    )
    _write_manifest(pending)

    push_argv = [
        "push",
        f"--force-with-lease={pending.head_branch_ref}:{pending.original_head_sha}",
        preconditions.remote_identity.push_url,
        f"{pending.rebased_head_sha}:{pending.head_branch_ref}",
    ]
    push_result = git_runner(pending.repo_root, push_argv)
    if push_result.returncode != 0:
        return _manifest_result(
            pending,
            outcome=PRRebaseOutcome.REFUSED,
            message=_git_failed("lease-protected push", push_result),
        )

    pushed = pending.transition(
        PRRebaseState.PUSH_SUCCEEDED_UNVERIFIED,
        timestamp=_timestamp(now),
        reason="lease-protected push succeeded",
    )
    _write_manifest(pushed)
    return _verify_and_cleanup(
        pushed,
        github_client=github_client,
        git_runner=git_runner,
        now=now,
        sleep=sleep,
        verify_attempts=verify_attempts,
    )


def abort_pr_rebase(
    *,
    workspace_path: Path,
    repo_root: Path,
    git_runner: GitRunner = run_git,
    now: datetime | None = None,
) -> PRRebaseResult:
    """Abort and remove one safe, manifest-matching managed worktree."""
    manifest = load_manifest(
        workspace_path,
        repo_root=repo_root,
        git_runner=git_runner,
    )
    if manifest.state in {
        PRRebaseState.LEASE_PUSH_PENDING,
        PRRebaseState.PUSH_SUCCEEDED_UNVERIFIED,
        PRRebaseState.PUBLISHED,
    }:
        raise PRRebaseRefusal(
            f"abort refused: workspace has recorded state {manifest.state.value}."
        )
    if manifest.receipt_path is not None and manifest.receipt_path.exists():
        raise PRRebaseRefusal("abort refused: a publish receipt already exists.")

    if _rebase_in_progress(manifest.workspace_path, git_runner=git_runner):
        result = git_runner(manifest.workspace_path, ["rebase", "--abort"])
        if result.returncode != 0:
            raise PRRebasePreconditionError(_git_failed("rebase --abort", result))

    aborted = manifest.transition(
        manifest.state,
        timestamp=_timestamp(now),
        reason="operator abort removed managed worktree",
    )
    _write_manifest(aborted)
    _remove_worktree(aborted, git_runner=git_runner)
    return PRRebaseResult(
        outcome=PRRebaseOutcome.ABORTED,
        message=f"removed rebase workspace {manifest.workspace_path}.",
        workspace_path=manifest.workspace_path,
        manifest_path=manifest.manifest_path,
        state=manifest.state,
        old_head_sha=manifest.original_head_sha,
        pinned_base_sha=manifest.pinned_base_sha,
        merge_base_sha=manifest.merge_base_sha,
        new_head_sha=manifest.rebased_head_sha,
        branch=manifest.branch_name,
        conflict_paths=_flatten_conflict_paths(manifest),
        receipt_path=manifest.receipt_path,
    )


def load_manifest(
    workspace_path: Path,
    *,
    repo_root: Path,
    git_runner: GitRunner = run_git,
) -> RebaseManifest:
    """Load a manifest only after proving the path belongs to this repo root."""
    canonical_root = _canonical_repo_root(repo_root, git_runner=git_runner)
    workspace_root = _ensure_workspace_root(canonical_root, create=False)
    try:
        canonical_workspace = workspace_path.resolve(strict=True)
    except FileNotFoundError as error:
        raise PRRebasePreconditionError(
            f"workspace does not exist: {workspace_path}"
        ) from error
    if canonical_workspace == workspace_root or not canonical_workspace.is_relative_to(
        workspace_root
    ):
        raise PRRebasePreconditionError(
            f"workspace path must be beneath {workspace_root}: {workspace_path}"
        )

    manifest_path = canonical_workspace / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise PRRebasePreconditionError(
            f"workspace is missing {MANIFEST_FILENAME}: {canonical_workspace}"
        )
    try:
        raw_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise PRRebasePreconditionError("rebase manifest is not valid JSON") from error
    if not isinstance(raw_payload, dict):
        raise PRRebasePreconditionError("rebase manifest must be a JSON object")

    manifest = RebaseManifest.from_json(raw_payload)
    manifest_root = manifest.repo_root.resolve(strict=False)
    manifest_workspace = manifest.workspace_path.resolve(strict=False)
    if manifest_root != canonical_root:
        raise PRRebasePreconditionError(
            "rebase manifest repo_root does not match this repository"
        )
    if manifest_workspace != canonical_workspace:
        raise PRRebasePreconditionError(
            "rebase manifest workspace_path does not match the requested path"
        )
    if manifest.manifest_path.resolve(strict=False) != manifest_path.resolve(
        strict=False
    ):
        raise PRRebasePreconditionError("rebase manifest path did not round-trip")
    if (
        manifest.head_branch_ref
        != f"refs/heads/{_validate_head_ref(manifest.head_ref)}"
    ):
        raise PRRebasePreconditionError("manifest head ref is not a normal branch")
    return manifest


def _verify_and_cleanup(
    manifest: RebaseManifest,
    *,
    github_client: GitHubClient,
    git_runner: GitRunner,
    now: datetime | None,
    sleep: Callable[[float], None],
    verify_attempts: int,
) -> PRRebaseResult:
    if manifest.rebased_head_sha is None:
        raise PRRebaseRefusal("manifest has no rebased head to verify.")
    if _verify_published(
        manifest,
        github_client=github_client,
        sleep=sleep,
        verify_attempts=verify_attempts,
    ):
        receipt_path = _write_receipt(manifest, now=now)
        published = manifest.transition(
            PRRebaseState.PUBLISHED,
            timestamp=_timestamp(now),
            reason="GitHub confirmed rebased head current with pinned main",
            receipt_path=receipt_path,
        )
        _write_manifest(published)
        _remove_worktree(published, git_runner=git_runner)
        return _manifest_result(
            published,
            outcome=PRRebaseOutcome.PUBLISHED,
            message=f"published and verified; receipt written to {receipt_path}.",
            receipt_path=receipt_path,
        )
    return _manifest_result(
        manifest,
        outcome=PRRebaseOutcome.PUSH_SUCCEEDED_UNVERIFIED,
        message=(
            "push succeeded, but GitHub has not yet confirmed the exact rebased "
            "head as current; rerun publish to verify and clean up."
        ),
    )


def _resume_pending_publish(
    manifest: RebaseManifest,
    *,
    github_client: GitHubClient,
    git_runner: GitRunner,
    now: datetime | None,
    sleep: Callable[[float], None],
    verify_attempts: int,
) -> PRRebaseResult:
    if manifest.rebased_head_sha is None:
        raise PRRebaseRefusal("manifest has no rebased head to publish.")
    if (
        manifest.pending_push_expected_old_head_sha != manifest.original_head_sha
        or manifest.pending_push_rebased_head_sha != manifest.rebased_head_sha
    ):
        raise PRRebaseRefusal("lease-pending manifest does not match push SHAs.")

    current_head = _rev_parse(manifest.workspace_path, "HEAD", git_runner=git_runner)
    if current_head != manifest.rebased_head_sha:
        raise PRRebaseRefusal(
            f"workspace HEAD changed from manifest rebased head: expected "
            f"{manifest.rebased_head_sha}, got {current_head}."
        )

    preconditions = _publish_preconditions(
        manifest,
        github_client=github_client,
        git_runner=git_runner,
        allowed_head_shas=(
            manifest.original_head_sha,
            manifest.rebased_head_sha,
        ),
    )
    _require_same_remote_identity(manifest, preconditions.remote_identity)
    if preconditions.remote_head_sha == manifest.rebased_head_sha:
        recovered = manifest.transition(
            PRRebaseState.PUSH_SUCCEEDED_UNVERIFIED,
            timestamp=_timestamp(now),
            reason="lease-pending retry found rebased head on remote",
        )
        _write_manifest(recovered)
        return _verify_and_cleanup(
            recovered,
            github_client=github_client,
            git_runner=git_runner,
            now=now,
            sleep=sleep,
            verify_attempts=verify_attempts,
        )
    if preconditions.remote_head_sha != manifest.original_head_sha:
        raise PRRebaseRefusal(
            "remote PR head is neither the expected old head nor the rebased head: "
            f"{preconditions.remote_head_sha}."
        )

    push_argv = [
        "push",
        f"--force-with-lease={manifest.head_branch_ref}:{manifest.original_head_sha}",
        preconditions.remote_identity.push_url,
        f"{manifest.rebased_head_sha}:{manifest.head_branch_ref}",
    ]
    push_result = git_runner(manifest.repo_root, push_argv)
    if push_result.returncode != 0:
        return _manifest_result(
            manifest,
            outcome=PRRebaseOutcome.REFUSED,
            message=_git_failed("lease-protected push", push_result),
        )

    pushed = manifest.transition(
        PRRebaseState.PUSH_SUCCEEDED_UNVERIFIED,
        timestamp=_timestamp(now),
        reason="lease-pending retry completed lease-protected push",
    )
    _write_manifest(pushed)
    return _verify_and_cleanup(
        pushed,
        github_client=github_client,
        git_runner=git_runner,
        now=now,
        sleep=sleep,
        verify_attempts=verify_attempts,
    )


def _verify_published(
    manifest: RebaseManifest,
    *,
    github_client: GitHubClient,
    sleep: Callable[[float], None],
    verify_attempts: int,
) -> bool:
    if verify_attempts < 0:
        raise PRRebasePreconditionError("verify_attempts must be non-negative")
    owner, repo = _split_repo_slug(manifest.repo_slug)
    for attempt in range(verify_attempts):
        try:
            assessment = assess_pr_integration(
                github_client,
                owner,
                repo,
                manifest.pr_number,
            )
        except GitHubAPIError:
            assessment = None
        if (
            assessment is not None
            and assessment.head_sha == manifest.rebased_head_sha
            and assessment.base_sha == manifest.pinned_base_sha
            and assessment.integration_status is PRIntegrationStatus.CURRENT
        ):
            return True
        if attempt + 1 < verify_attempts:
            sleep(VERIFY_BACKOFF_SECONDS)
    return False


def _publish_preconditions(
    manifest: RebaseManifest,
    *,
    github_client: GitHubClient,
    git_runner: GitRunner,
    allowed_head_shas: tuple[str, ...],
) -> _PublishPreconditions:
    remote_identity = _resolve_origin_push_identity(
        manifest.repo_root,
        expected_repo_slug=manifest.repo_slug,
        git_runner=git_runner,
    )
    owner, repo = _split_repo_slug(manifest.repo_slug)
    try:
        payload = github_client.fetch_pull_request(owner, repo, manifest.pr_number)
    except GitHubAPIError as error:
        raise PRRebaseRefusal(str(error)) from error
    snapshot = _live_snapshot_from_pull_request(payload, manifest.pr_number)
    if snapshot.state != "open" or snapshot.merged:
        raise PRRebaseRefusal("live PR is closed or merged; refusing to publish.")
    if snapshot.draft:
        raise PRRebaseRefusal("live PR is draft; refusing to publish.")
    if snapshot.head_repository != manifest.repo_slug:
        raise PRRebaseRefusal("live PR head repository changed; refusing to publish.")
    if snapshot.base_repository != manifest.repo_slug:
        raise PRRebaseRefusal("live PR base repository changed; refusing to publish.")
    if snapshot.head_ref != manifest.head_ref:
        raise PRRebaseRefusal("live PR head branch changed; refusing to publish.")
    if snapshot.base_ref != manifest.base_ref:
        raise PRRebaseRefusal("live PR base branch changed; refusing to publish.")
    if snapshot.head_sha not in allowed_head_shas:
        raise PRRebaseRefusal(
            f"live PR head moved: expected one of "
            f"{', '.join(allowed_head_shas)}, got {snapshot.head_sha}."
        )
    if snapshot.base_sha != manifest.pinned_base_sha:
        raise PRRebaseRefusal(
            f"live main moved: expected {manifest.pinned_base_sha}, "
            f"got {snapshot.base_sha}."
        )

    fetch_result = git_runner(
        manifest.repo_root,
        [
            "fetch",
            REMOTE_NAME,
            f"+refs/heads/{manifest.base_ref}:refs/remotes/{REMOTE_NAME}/{manifest.base_ref}",
            f"+{manifest.head_branch_ref}:refs/remotes/{REMOTE_NAME}/{manifest.head_ref}",
        ],
    )
    if fetch_result.returncode != 0:
        raise PRRebaseRefusal(_git_failed("remote ref fetch", fetch_result))
    remote_head = _rev_parse(
        manifest.repo_root,
        f"refs/remotes/{REMOTE_NAME}/{manifest.head_ref}",
        git_runner=git_runner,
    )
    remote_base = _rev_parse(
        manifest.repo_root,
        f"refs/remotes/{REMOTE_NAME}/{manifest.base_ref}",
        git_runner=git_runner,
    )
    if remote_head not in allowed_head_shas:
        if allowed_head_shas == (manifest.original_head_sha,):
            raise PRRebaseRefusal(
                f"remote PR head moved: expected {manifest.original_head_sha}, "
                f"got {remote_head}."
            )
        raise PRRebaseRefusal(
            f"remote PR head moved: expected one of "
            f"{', '.join(allowed_head_shas)}, got {remote_head}."
        )
    if remote_base != manifest.pinned_base_sha:
        raise PRRebaseRefusal(
            f"remote main moved: expected {manifest.pinned_base_sha}, "
            f"got {remote_base}."
        )
    return _PublishPreconditions(
        remote_identity=remote_identity,
        remote_head_sha=remote_head,
        remote_base_sha=remote_base,
    )


def _resolve_origin_push_identity(
    repo_root: Path,
    *,
    expected_repo_slug: str,
    git_runner: GitRunner,
) -> _RemoteIdentity:
    result = git_runner(
        repo_root,
        ["remote", "get-url", "--push", "--all", REMOTE_NAME],
    )
    if result.returncode != 0:
        raise PRRebaseRefusal(_git_failed("remote get-url --push --all origin", result))
    remote_urls = tuple(
        line.strip() for line in result.stdout.splitlines() if line.strip()
    )
    if not remote_urls:
        raise PRRebaseRefusal("origin has no push URL; refusing to publish.")

    valid_identities: list[_RemoteIdentity] = []
    for remote_url in remote_urls:
        repo_slug, url_kind = _parse_remote_repo_slug(remote_url, repo_root=repo_root)
        if repo_slug is None:
            raise PRRebaseRefusal(
                "origin push URL does not resolve to a supported repository identity."
            )
        if repo_slug.casefold() != expected_repo_slug.casefold():
            raise PRRebaseRefusal(
                f"origin push URL targets {repo_slug}, not {expected_repo_slug}; "
                "refusing to publish."
            )
        valid_identities.append(
            _RemoteIdentity(
                remote_name=REMOTE_NAME,
                push_url=remote_url,
                repo_slug=repo_slug,
                url_kind=url_kind,
            )
        )

    if len(valid_identities) != 1:
        raise PRRebaseRefusal(
            "origin must have exactly one validated push URL; found "
            f"{len(valid_identities)}."
        )
    return valid_identities[0]


def _parse_remote_repo_slug(
    remote_url: str,
    *,
    repo_root: Path,
) -> tuple[str | None, str]:
    scp_match = re.fullmatch(
        r"(?:[^@\s]+@)?github\.com:(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+)",
        remote_url,
    )
    if scp_match is not None:
        return (
            f"{scp_match.group('owner')}/{_strip_git_suffix(scp_match.group('repo'))}",
            "github_scp",
        )

    parsed = urlparse(remote_url)
    if parsed.scheme and parsed.hostname == "github.com":
        path_parts = _path_repo_parts(Path(parsed.path))
        if path_parts is None:
            return None, f"github_{parsed.scheme}"
        return path_parts, f"github_{parsed.scheme}"
    if parsed.scheme == "file":
        path_parts = _path_repo_parts(Path(parsed.path))
        return path_parts, "local_path"
    if not parsed.scheme:
        remote_path = Path(remote_url)
        if not remote_path.is_absolute():
            remote_path = repo_root / remote_path
        return _path_repo_parts(remote_path), "local_path"
    return None, parsed.scheme


def _path_repo_parts(path: Path) -> str | None:
    parts = [part for part in path.parts if part not in {path.anchor, ""}]
    if len(parts) < 2:
        return None
    owner = parts[-2]
    repo = _strip_git_suffix(parts[-1])
    if not owner or not repo or "/" in owner or "/" in repo:
        return None
    return f"{owner}/{repo}"


def _strip_git_suffix(value: str) -> str:
    return value[:-4] if value.endswith(".git") else value


def _require_same_remote_identity(
    manifest: RebaseManifest,
    remote_identity: _RemoteIdentity,
) -> None:
    if (
        manifest.remote_name != remote_identity.remote_name
        or manifest.remote_repo_slug != remote_identity.repo_slug
        or manifest.remote_url_kind != remote_identity.url_kind
    ):
        raise PRRebaseRefusal(
            "origin push identity changed since the lease-pending manifest was "
            "written; refusing to publish."
        )


def _rebase_argv(*args: str) -> list[str]:
    return [
        "-c",
        "rerere.enabled=false",
        "-c",
        "rerere.autoupdate=false",
        "rebase",
        *args,
    ]


def _prepare_gate_refusal(
    assessment: PRIntegrationAssessment,
    close_set: tuple[str, ...],
    ticket_lookup: TicketLookup,
) -> str | None:
    if assessment.integration_status is PRIntegrationStatus.CURRENT:
        return "current"
    if assessment.integration_status not in {
        PRIntegrationStatus.BEHIND,
        PRIntegrationStatus.DIVERGED,
        PRIntegrationStatus.CONFLICTED,
    }:
        return (
            f"prepare refused: PR integration status is "
            f"{assessment.integration_status.value}."
        )
    if not close_set:
        return "prepare refused: PR title/body does not resolve an Atlas ticket."
    tickets: list[Ticket] = []
    unknown_keys: list[str] = []
    for key in close_set:
        ticket = ticket_lookup.get_by_key(key)
        if ticket is None:
            unknown_keys.append(key)
        else:
            tickets.append(ticket)
    if unknown_keys:
        return "prepare refused: unknown Atlas ticket(s): " + ", ".join(unknown_keys)
    not_review_required = [
        f"{ticket.key}:{ticket.status.value}"
        for ticket in tickets
        if ticket.status is not TicketStatus.REVIEW_REQUIRED
    ]
    if not_review_required:
        return (
            "prepare refused: every ticket must be review_required; found "
            + ", ".join(not_review_required)
        )
    return None


def _live_snapshot_from_pull_request(
    payload: Mapping[str, Any], requested_number: int
) -> _LivePRSnapshot:
    number = _required_positive_int(payload, "number", label="live pull request")
    if number != requested_number:
        raise PRRebaseRefusal("live PR snapshot number mismatched.")
    head = _required_object(payload, "head", label="live pull request")
    base = _required_object(payload, "base", label="live pull request")
    return _LivePRSnapshot(
        number=number,
        state=_required_str(payload, "state", label="live pull request"),
        draft=_required_bool(payload, "draft", label="live pull request"),
        merged=_required_bool(payload, "merged", label="live pull request"),
        head_ref=_required_str(head, "ref", label="live PR head"),
        head_sha=_required_sha(head, "sha"),
        head_repository=_repo_full_name(head, label="live PR head"),
        base_ref=_required_str(base, "ref", label="live PR base"),
        base_sha=_required_sha(base, "sha"),
        base_repository=_repo_full_name(base, label="live PR base"),
    )


def _manifest_result(
    manifest: RebaseManifest,
    *,
    outcome: PRRebaseOutcome,
    message: str,
    tickets: tuple[str, ...] = (),
    conflict_paths: Sequence[str] | None = None,
    receipt_path: Path | None = None,
) -> PRRebaseResult:
    return PRRebaseResult(
        outcome=outcome,
        message=message,
        workspace_path=manifest.workspace_path,
        manifest_path=manifest.manifest_path,
        state=manifest.state,
        old_head_sha=manifest.original_head_sha,
        pinned_base_sha=manifest.pinned_base_sha,
        merge_base_sha=manifest.merge_base_sha,
        new_head_sha=manifest.rebased_head_sha,
        branch=manifest.branch_name,
        tickets=tickets,
        conflict_paths=(
            tuple(conflict_paths)
            if conflict_paths is not None
            else _flatten_conflict_paths(manifest)
        ),
        receipt_path=receipt_path or manifest.receipt_path,
    )


def _flatten_conflict_paths(manifest: RebaseManifest) -> tuple[str, ...]:
    paths: list[str] = []
    seen: set[str] = set()
    for conflict_set in manifest.conflict_sets:
        for path in conflict_set.paths:
            if path not in seen:
                seen.add(path)
                paths.append(path)
    return tuple(paths)


def _write_manifest(manifest: RebaseManifest) -> None:
    _write_json_atomic(manifest.manifest_path, manifest.to_json())


def _write_receipt(manifest: RebaseManifest, *, now: datetime | None) -> Path:
    if manifest.rebased_head_sha is None:
        raise PRRebaseRefusal("manifest has no rebased head for receipt.")
    receipt_root = _ensure_receipt_root(manifest.repo_root)
    receipt = receipt_root / (
        f"{_slug_for_path(manifest.repo_slug)}-pr-{manifest.pr_number}-"
        f"{manifest.original_head_sha[:12]}-{manifest.rebased_head_sha[:12]}.json"
    )
    payload = {
        "schema_version": 1,
        "kind": "atlas_pr_rebase_receipt",
        "repo_slug": manifest.repo_slug,
        "repo_root": str(manifest.repo_root),
        "pr_number": manifest.pr_number,
        "branch": manifest.branch_name,
        "head_branch_ref": manifest.head_branch_ref,
        "old_head_sha": manifest.original_head_sha,
        "pinned_base_sha": manifest.pinned_base_sha,
        "merge_base_sha": manifest.merge_base_sha,
        "new_head_sha": manifest.rebased_head_sha,
        "workspace_path": str(manifest.workspace_path),
        "remote_name": manifest.remote_name,
        "remote_repo_slug": manifest.remote_repo_slug,
        "remote_url_kind": manifest.remote_url_kind,
        "pending_push_expected_old_head_sha": (
            manifest.pending_push_expected_old_head_sha
        ),
        "pending_push_rebased_head_sha": manifest.pending_push_rebased_head_sha,
        "conflict_paths": list(_flatten_conflict_paths(manifest)),
        "conflict_sets": [entry.to_json() for entry in manifest.conflict_sets],
        "created_at": manifest.created_at,
        "push_succeeded_at": manifest.updated_at,
        "published_at": _timestamp(now),
    }
    _write_json_atomic(receipt, payload)
    return receipt


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)
    _fsync_dir(path.parent)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _remove_worktree(manifest: RebaseManifest, *, git_runner: GitRunner) -> None:
    result = git_runner(
        manifest.repo_root,
        ["worktree", "remove", "--force", str(manifest.workspace_path)],
    )
    if result.returncode != 0:
        raise PRRebasePreconditionError(_git_failed("worktree remove", result))


def _rebase_in_progress(workspace: Path, *, git_runner: GitRunner) -> bool:
    for marker in ("rebase-merge", "rebase-apply"):
        result = git_runner(workspace, ["rev-parse", "--git-path", marker])
        if result.returncode != 0:
            raise PRRebasePreconditionError(
                _git_failed(f"rev-parse --git-path {marker}", result)
            )
        raw_path = result.stdout.strip()
        marker_path = Path(raw_path)
        if not marker_path.is_absolute():
            marker_path = workspace / marker_path
        if marker_path.exists():
            return True
    return False


def _fetch_branch_refs(
    repo_root: Path,
    branch: str,
    *,
    git_runner: GitRunner,
) -> None:
    result = git_runner(
        repo_root,
        [
            "fetch",
            REMOTE_NAME,
            f"+refs/heads/main:refs/remotes/{REMOTE_NAME}/main",
            f"+refs/heads/{branch}:refs/remotes/{REMOTE_NAME}/{branch}",
        ],
    )
    if result.returncode != 0:
        raise PRRebasePreconditionError(_git_failed("fetch remote refs", result))


def _rev_parse(repo_root: Path, rev: str, *, git_runner: GitRunner) -> str:
    result = git_runner(repo_root, ["rev-parse", "--verify", rev])
    if result.returncode != 0:
        raise PRRebasePreconditionError(_git_failed(f"rev-parse {rev}", result))
    value = result.stdout.strip()
    if not _is_sha(value):
        raise PRRebasePreconditionError(f"git rev-parse {rev} did not return a SHA")
    return value


def _unmerged_paths(workspace: Path, *, git_runner: GitRunner) -> tuple[str, ...]:
    result = git_runner(workspace, ["diff", "--name-only", "--diff-filter=U"])
    if result.returncode != 0:
        raise PRRebasePreconditionError(_git_failed("diff unresolved paths", result))
    return tuple(line for line in result.stdout.splitlines() if line)


def _canonical_repo_root(repo_root: Path, *, git_runner: GitRunner) -> Path:
    result = git_runner(repo_root, ["rev-parse", "--show-toplevel"])
    if result.returncode != 0:
        raise PRRebasePreconditionError(
            _git_failed("rev-parse --show-toplevel", result)
        )
    return Path(result.stdout.strip()).resolve(strict=True)


def _ensure_workspace_root(repo_root: Path, *, create: bool) -> Path:
    root = repo_root / WORKSPACE_ROOT
    if create:
        root.mkdir(parents=True, exist_ok=True)
    elif not root.exists():
        raise PRRebasePreconditionError(f"rebase workspace root does not exist: {root}")
    resolved = root.resolve(strict=True)
    if not resolved.is_relative_to(repo_root):
        raise PRRebasePreconditionError(
            f"rebase workspace root escapes repository: {root}"
        )
    return resolved


def _ensure_receipt_root(repo_root: Path) -> Path:
    root = repo_root / RECEIPT_ROOT
    root.mkdir(parents=True, exist_ok=True)
    resolved = root.resolve(strict=True)
    if not resolved.is_relative_to(repo_root):
        raise PRRebasePreconditionError(
            f"rebase receipt root escapes repository: {root}"
        )
    return resolved


def _workspace_path(
    workspace_root: Path,
    *,
    repo_slug: str,
    pr_number: int,
    head_sha: str,
) -> Path:
    workspace = (
        workspace_root / f"{_slug_for_path(repo_slug)}-pr-{pr_number}-{head_sha[:12]}"
    )
    resolved = workspace.resolve(strict=False)
    if not resolved.is_relative_to(workspace_root):
        raise PRRebasePreconditionError("derived workspace path escaped workspace root")
    return resolved


def _split_repo_slug(repo_slug: str) -> tuple[str, str]:
    owner, sep, repo = repo_slug.partition("/")
    if not (owner and sep and repo) or "/" in repo:
        raise PRRebasePreconditionError("--repo must be OWNER/REPO (e.g. acme/atlas).")
    return owner, repo


def _validate_head_ref(head_ref: str) -> str:
    branch = head_ref.strip()
    if branch != head_ref or not branch:
        raise PRRebasePreconditionError("PR head ref is not a normal branch name")
    if branch.startswith(("refs/", "-", "/")):
        raise PRRebasePreconditionError("PR head ref is not a normal branch name")
    if branch.endswith(("/", ".")) or branch == "@":
        raise PRRebasePreconditionError("PR head ref is not a normal branch name")
    if ".." in branch or "//" in branch or "@{" in branch:
        raise PRRebasePreconditionError("PR head ref is not a normal branch name")
    if any(char in branch for char in " ~^:?*[\\"):
        raise PRRebasePreconditionError("PR head ref is not a normal branch name")
    if any(
        part.startswith(".") or part.endswith(".lock") for part in branch.split("/")
    ):
        raise PRRebasePreconditionError("PR head ref is not a normal branch name")
    return branch


def _slug_for_path(repo_slug: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", repo_slug).strip("_") or "repo"


def _state_from_str(value: str) -> PRRebaseState:
    try:
        return PRRebaseState(value)
    except ValueError as error:
        raise PRRebasePreconditionError(
            f"unknown rebase manifest state {value!r}"
        ) from error


def _timestamp(now: datetime | None) -> str:
    value = now if now is not None else datetime.now(UTC)
    if value.utcoffset() is None:
        raise PRRebasePreconditionError("rebase timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _required_str(payload: Mapping[str, Any], key: str, *, label: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PRRebaseRefusal(f"{label} missing string field {key!r}")
    return value


def _optional_str(payload: Mapping[str, Any], key: str, *, label: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise PRRebasePreconditionError(f"{label} field {key!r} was not a string")
    return value


def _required_int(payload: Mapping[str, Any], key: str, *, label: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise PRRebasePreconditionError(f"{label} missing integer field {key!r}")
    return value


def _required_positive_int(payload: Mapping[str, Any], key: str, *, label: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise PRRebaseRefusal(f"{label} missing positive integer field {key!r}")
    return value


def _required_bool(payload: Mapping[str, Any], key: str, *, label: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise PRRebaseRefusal(f"{label} missing boolean field {key!r}")
    return value


def _required_object(
    payload: Mapping[str, Any], key: str, *, label: str
) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise PRRebaseRefusal(f"{label} missing object field {key!r}")
    return cast(Mapping[str, Any], value)


def _repo_full_name(payload: Mapping[str, Any], *, label: str) -> str:
    repo = _required_object(payload, "repo", label=label)
    return _required_str(repo, "full_name", label=f"{label} repo")


def _required_sha(payload: Mapping[str, Any], key: str) -> str:
    value = _required_str(payload, key, label="SHA field")
    _require_sha_value(value, label=key)
    return value


def _require_sha_value(value: str, *, label: str) -> None:
    if not _is_sha(value):
        raise PRRebasePreconditionError(f"{label} was not a 40-hex SHA")


def _is_sha(value: str) -> bool:
    return bool(_SHA_RE.fullmatch(value))


def _git_failed(label: str, result: subprocess.CompletedProcess[str]) -> str:
    detail = result.stderr.strip() or result.stdout.strip()
    suffix = f": {detail}" if detail else ""
    return f"git {label} failed with exit {result.returncode}{suffix}"

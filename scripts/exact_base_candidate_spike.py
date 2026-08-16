"""ATLAS-259 exact-base synthetic-merge feasibility harness.

This is deliberately a spike artifact, not an acceptance implementation.  It
normalises two bounded provider observations, applies the exact candidate
identity algebra, and exercises Git object relationships in a disposable local
repository.  It has no GitHub/Linear writer and never runs push, fetch, merge,
or rebase.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, cast

SCHEMA_VERSION = 1
MAX_FIXTURE_BYTES = 64 * 1024
MAX_CASES = 32
MAX_CHECKS = 64
MAX_TEXT = 160
_SHA_LENGTH = 40


class Decision(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class Mergeability(StrEnum):
    MERGEABLE = "mergeable"
    CONFLICTED = "conflicted"
    INDETERMINATE = "indeterminate"


class ReasonCode(StrEnum):
    MALFORMED_OBSERVATION = "malformed_observation"
    PROVIDER_AMBIGUITY = "provider_ambiguity"
    HEAD_MOVED = "head_moved"
    BASE_MOVED = "base_moved"
    CANDIDATE_MISSING = "candidate_missing"
    CANDIDATE_CONFLICTED = "candidate_conflicted"
    MERGEABILITY_INDETERMINATE = "mergeability_indeterminate"
    CANDIDATE_PARENT_MISMATCH = "candidate_parent_mismatch"
    CANDIDATE_MOVED = "candidate_moved"
    REQUIRED_CHECK_SET_MOVED = "required_check_set_moved"
    REQUIRED_CHECK_MISSING = "required_check_missing"
    REQUIRED_CHECK_AMBIGUOUS = "required_check_ambiguous"
    REQUIRED_CHECK_NOT_CANDIDATE_PINNED = "required_check_not_candidate_pinned"
    REQUIRED_CHECK_NOT_PASSED = "required_check_not_passed"


@dataclass(frozen=True, order=True)
class CheckKey:
    name: str
    app_id: int

    def payload(self) -> dict[str, object]:
        return {"app_id": self.app_id, "name": self.name}


@dataclass(frozen=True)
class CheckResult:
    key: CheckKey
    external_id: str
    commit_sha: str
    status: str
    conclusion: str | None


@dataclass(frozen=True)
class CandidateObservation:
    repository: str
    pr_number: int
    head_sha: str
    base_ref: str
    base_sha: str
    mergeability: Mergeability
    candidate_sha: str | None
    candidate_tree_sha: str | None
    candidate_parents: tuple[str, ...]
    required_checks: tuple[CheckKey, ...]
    check_results: tuple[CheckResult, ...]

    @property
    def candidate_identity(self) -> tuple[object, ...]:
        return (
            self.candidate_sha,
            self.candidate_tree_sha,
            self.candidate_parents,
        )


@dataclass(frozen=True)
class Assessment:
    decision: Decision
    reasons: tuple[ReasonCode, ...]
    observation: CandidateObservation | None

    def payload(self) -> dict[str, object]:
        identity: dict[str, object] | None = None
        if self.observation is not None:
            observation = self.observation
            required = tuple(sorted(observation.required_checks))
            required_set = set(required)
            required_payload = [check.payload() for check in required]
            fingerprint = hashlib.sha256(
                json.dumps(
                    required_payload, separators=(",", ":"), sort_keys=True
                ).encode()
            ).hexdigest()
            identity = {
                "base": {"ref": observation.base_ref, "sha": observation.base_sha},
                "candidate": {
                    "parents": list(observation.candidate_parents),
                    "sha": observation.candidate_sha,
                    "tree_sha": observation.candidate_tree_sha,
                },
                "head_sha": observation.head_sha,
                "pr_number": observation.pr_number,
                "repository": observation.repository,
                "required_check_count": len(required),
                "required_check_set_fingerprint": fingerprint,
                "required_checks": required_payload,
                "required_results": [
                    {
                        "app_id": result.key.app_id,
                        "commit_sha": result.commit_sha,
                        "conclusion": result.conclusion,
                        "external_id": result.external_id,
                        "name": result.key.name,
                        "status": result.status,
                    }
                    for result in sorted(
                        (
                            result
                            for result in observation.check_results
                            if result.key in required_set
                        ),
                        key=lambda result: (
                            result.key,
                            result.external_id,
                            result.commit_sha,
                        ),
                    )
                ],
            }
        return {
            "decision": self.decision.value,
            "identity": identity,
            "reasons": [reason.value for reason in self.reasons],
        }


class GitRunner(Protocol):
    def __call__(
        self,
        cwd: Path,
        args: Sequence[str],
        *,
        input_text: str | None = None,
        check: bool = True,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]: ...


def _run_git(
    cwd: Path,
    args: Sequence[str],
    *,
    input_text: str | None = None,
    check: bool = True,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    process_env = os.environ.copy()
    if env is not None:
        process_env.update(env)
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=process_env,
        input=input_text,
        capture_output=True,
        text=True,
        shell=False,
        check=check,
    )


def _is_sha(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _SHA_LENGTH
        and all(char in "0123456789abcdef" for char in value)
    )


def _mapping(value: object, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return cast(Mapping[str, Any], value)


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT:
        raise ValueError(f"{field} must be bounded non-empty text")
    return value


def _positive_int(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _sha(value: object, *, field: str) -> str:
    if not _is_sha(value):
        raise ValueError(f"{field} must be a full lowercase SHA-1")
    return cast(str, value)


def _optional_sha(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    return _sha(value, field=field)


def _check_key(payload: object, *, field: str) -> CheckKey:
    item = _mapping(payload, field=field)
    return CheckKey(
        name=_text(item.get("name"), field=f"{field}.name"),
        app_id=_positive_int(item.get("app_id"), field=f"{field}.app_id"),
    )


def _observation(payload: object) -> CandidateObservation:
    item = _mapping(payload, field="observation")
    raw_parents = item.get("candidate_parents")
    raw_required = item.get("required_checks")
    raw_results = item.get("check_results")
    if not isinstance(raw_parents, list) or len(raw_parents) > 2:
        raise ValueError("candidate_parents must be a list of at most two SHAs")
    if not isinstance(raw_required, list) or not 0 < len(raw_required) <= MAX_CHECKS:
        raise ValueError("required_checks must be a bounded non-empty list")
    if not isinstance(raw_results, list) or len(raw_results) > MAX_CHECKS:
        raise ValueError("check_results must be a bounded list")

    required = tuple(
        sorted(
            _check_key(value, field=f"required_checks[{index}]")
            for index, value in enumerate(raw_required)
        )
    )
    if len(set(required)) != len(required):
        raise ValueError("required_checks contains a duplicate key")

    results: list[CheckResult] = []
    for index, value in enumerate(raw_results):
        result = _mapping(value, field=f"check_results[{index}]")
        conclusion = result.get("conclusion")
        if conclusion is not None:
            conclusion = _text(conclusion, field=f"check_results[{index}].conclusion")
        results.append(
            CheckResult(
                key=_check_key(result, field=f"check_results[{index}]"),
                external_id=_text(
                    result.get("external_id"),
                    field=f"check_results[{index}].external_id",
                ),
                commit_sha=_sha(
                    result.get("commit_sha"),
                    field=f"check_results[{index}].commit_sha",
                ),
                status=_text(
                    result.get("status"), field=f"check_results[{index}].status"
                ),
                conclusion=conclusion,
            )
        )

    try:
        mergeability = Mergeability(
            _text(item.get("mergeability"), field="mergeability")
        )
    except ValueError as error:
        raise ValueError("mergeability is unsupported") from error

    return CandidateObservation(
        repository=_text(item.get("repository"), field="repository"),
        pr_number=_positive_int(item.get("pr_number"), field="pr_number"),
        head_sha=_sha(item.get("head_sha"), field="head_sha"),
        base_ref=_text(item.get("base_ref"), field="base_ref"),
        base_sha=_sha(item.get("base_sha"), field="base_sha"),
        mergeability=mergeability,
        candidate_sha=_optional_sha(item.get("candidate_sha"), field="candidate_sha"),
        candidate_tree_sha=_optional_sha(
            item.get("candidate_tree_sha"), field="candidate_tree_sha"
        ),
        candidate_parents=tuple(
            _sha(value, field=f"candidate_parents[{index}]")
            for index, value in enumerate(raw_parents)
        ),
        required_checks=required,
        check_results=tuple(results),
    )


def _append(reasons: list[ReasonCode], reason: ReasonCode) -> None:
    if reason not in reasons:
        reasons.append(reason)


def assess_observations(first_payload: object, second_payload: object) -> Assessment:
    """Fail closed unless both reads reconstruct one candidate and its checks."""
    try:
        first = _observation(first_payload)
        second = _observation(second_payload)
    except (TypeError, ValueError):
        return Assessment(
            decision=Decision.FAIL,
            reasons=(ReasonCode.MALFORMED_OBSERVATION,),
            observation=None,
        )

    reasons: list[ReasonCode] = []
    if (first.repository, first.pr_number) != (second.repository, second.pr_number):
        _append(reasons, ReasonCode.PROVIDER_AMBIGUITY)
    if first.head_sha != second.head_sha:
        _append(reasons, ReasonCode.HEAD_MOVED)
    if (first.base_ref, first.base_sha) != (second.base_ref, second.base_sha):
        _append(reasons, ReasonCode.BASE_MOVED)
    if first.required_checks != second.required_checks:
        _append(reasons, ReasonCode.REQUIRED_CHECK_SET_MOVED)

    for observation in (first, second):
        if observation.mergeability is Mergeability.CONFLICTED:
            _append(reasons, ReasonCode.CANDIDATE_CONFLICTED)
        elif observation.mergeability is Mergeability.INDETERMINATE:
            _append(reasons, ReasonCode.MERGEABILITY_INDETERMINATE)

        if observation.candidate_sha is None or observation.candidate_tree_sha is None:
            _append(reasons, ReasonCode.CANDIDATE_MISSING)
        elif observation.candidate_parents != (
            observation.base_sha,
            observation.head_sha,
        ):
            _append(reasons, ReasonCode.CANDIDATE_PARENT_MISMATCH)

    if first.candidate_identity != second.candidate_identity:
        _append(reasons, ReasonCode.CANDIDATE_MOVED)

    if second.candidate_sha is not None:
        results_by_key: dict[CheckKey, list[CheckResult]] = {}
        for result in second.check_results:
            results_by_key.setdefault(result.key, []).append(result)
        for required in second.required_checks:
            results = results_by_key.get(required, [])
            if not results:
                _append(reasons, ReasonCode.REQUIRED_CHECK_MISSING)
                continue
            if len(results) != 1:
                _append(reasons, ReasonCode.REQUIRED_CHECK_AMBIGUOUS)
                continue
            result = results[0]
            if result.commit_sha != second.candidate_sha:
                _append(reasons, ReasonCode.REQUIRED_CHECK_NOT_CANDIDATE_PINNED)
            if result.status != "completed" or result.conclusion != "success":
                _append(reasons, ReasonCode.REQUIRED_CHECK_NOT_PASSED)

    return Assessment(
        decision=Decision.FAIL if reasons else Decision.PASS,
        reasons=tuple(reasons),
        observation=second,
    )


_COMMIT_ENV = {
    "GIT_AUTHOR_NAME": "Atlas spike",
    "GIT_AUTHOR_EMAIL": "atlas-spike@example.invalid",
    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
    "GIT_COMMITTER_NAME": "Atlas spike",
    "GIT_COMMITTER_EMAIL": "atlas-spike@example.invalid",
    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
}


def _git_stdout(
    runner: GitRunner,
    root: Path,
    args: Sequence[str],
    *,
    input_text: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    return runner(root, args, input_text=input_text, env=env).stdout.strip()


def _commit(runner: GitRunner, root: Path, message: str) -> str:
    runner(root, ("commit", "--quiet", "-m", message), env=_COMMIT_ENV)
    return _git_stdout(runner, root, ("rev-parse", "HEAD"))


def _commit_tree(
    runner: GitRunner,
    root: Path,
    tree: str,
    parents: Sequence[str],
    message: str,
) -> str:
    args: list[str] = ["commit-tree", tree]
    for parent in parents:
        args.extend(("-p", parent))
    return _git_stdout(
        runner,
        root,
        args,
        input_text=f"{message}\n",
        env=_COMMIT_ENV,
    )


def exercise_disposable_repository(
    root: Path, *, git_runner: GitRunner = _run_git
) -> dict[str, object]:
    """Create only unpushed local fixture objects and return bounded evidence."""
    root.mkdir(parents=True, exist_ok=True)
    git_runner(root, ("init", "--quiet", "."))
    git_runner(root, ("config", "user.name", "Atlas spike"))
    git_runner(root, ("config", "user.email", "atlas-spike@example.invalid"))

    (root / "shared.txt").write_text("root\n", encoding="utf-8")
    git_runner(root, ("add", "shared.txt"))
    root_sha = _commit(git_runner, root, "root")

    git_runner(root, ("checkout", "--quiet", "-b", "base", root_sha))
    (root / "main.txt").write_text("base\n", encoding="utf-8")
    git_runner(root, ("add", "main.txt"))
    base_sha = _commit(git_runner, root, "base")

    git_runner(root, ("checkout", "--quiet", "--detach", root_sha))
    (root / "feature.txt").write_text("head\n", encoding="utf-8")
    git_runner(root, ("add", "feature.txt"))
    head_sha = _commit(git_runner, root, "head")

    candidate_tree = _git_stdout(
        git_runner, root, ("merge-tree", "--write-tree", base_sha, head_sha)
    )
    candidate_sha = _commit_tree(
        git_runner,
        root,
        candidate_tree,
        (base_sha, head_sha),
        "provider candidate",
    )
    repeated_tree = _git_stdout(
        git_runner, root, ("merge-tree", "--write-tree", base_sha, head_sha)
    )
    repeated_candidate = _commit_tree(
        git_runner,
        root,
        repeated_tree,
        (base_sha, head_sha),
        "provider candidate",
    )
    final_merge = _commit_tree(
        git_runner,
        root,
        candidate_tree,
        (base_sha, head_sha),
        "final merge",
    )
    squash_merge = _commit_tree(
        git_runner, root, candidate_tree, (base_sha,), "squash merge"
    )

    git_runner(root, ("checkout", "--quiet", "base"))
    (root / "sibling.txt").write_text("sibling\n", encoding="utf-8")
    git_runner(root, ("add", "sibling.txt"))
    moved_base_sha = _commit(git_runner, root, "sibling main movement")
    moved_tree = _git_stdout(
        git_runner, root, ("merge-tree", "--write-tree", moved_base_sha, head_sha)
    )
    moved_candidate = _commit_tree(
        git_runner,
        root,
        moved_tree,
        (moved_base_sha, head_sha),
        "provider candidate",
    )

    git_runner(root, ("checkout", "--quiet", "--detach", root_sha))
    (root / "shared.txt").write_text("base conflict\n", encoding="utf-8")
    git_runner(root, ("add", "shared.txt"))
    conflict_base = _commit(git_runner, root, "conflict base")
    git_runner(root, ("checkout", "--quiet", "--detach", root_sha))
    (root / "shared.txt").write_text("head conflict\n", encoding="utf-8")
    git_runner(root, ("add", "shared.txt"))
    conflict_head = _commit(git_runner, root, "conflict head")
    conflict = git_runner(
        root,
        ("merge-tree", "--write-tree", conflict_base, conflict_head),
        check=False,
    )

    return {
        "base_move": {
            "candidate_changed": moved_candidate != candidate_sha,
            "head_unchanged": head_sha,
            "new_base_sha": moved_base_sha,
            "new_candidate_sha": moved_candidate,
            "new_tree_sha": moved_tree,
            "old_base_sha": base_sha,
            "old_candidate_sha": candidate_sha,
            "old_tree_sha": candidate_tree,
        },
        "clean_candidate": {
            "base_sha": base_sha,
            "head_sha": head_sha,
            "parents": [base_sha, head_sha],
            "repeated_sha": repeated_candidate,
            "sha": candidate_sha,
            "stable": (
                repeated_tree == candidate_tree and repeated_candidate == candidate_sha
            ),
            "tree_sha": candidate_tree,
        },
        "conflict": {
            "base_sha": conflict_base,
            "candidate_available": conflict.returncode == 0,
            "head_sha": conflict_head,
        },
        "merge_commit": {
            "parents": [base_sha, head_sha],
            "same_tree_as_candidate": True,
            "sha": final_merge,
            "tree_sha": candidate_tree,
        },
        "root_sha": root_sha,
        "squash_merge": {
            "different_commit_from_candidate": squash_merge != candidate_sha,
            "parents": [base_sha],
            "same_tree_as_candidate": True,
            "sha": squash_merge,
            "tree_sha": candidate_tree,
        },
    }


def _load_fixture(path: Path) -> Mapping[str, Any]:
    size = path.stat().st_size
    if size > MAX_FIXTURE_BYTES:
        raise ValueError("fixture exceeds the bounded input limit")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return _mapping(payload, field="fixture")


def run_fixture(path: Path, repository_root: Path) -> tuple[dict[str, object], bool]:
    fixture = _load_fixture(path)
    if fixture.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("fixture schema_version is unsupported")
    cases = fixture.get("cases")
    governed_case = _text(fixture.get("governed_case"), field="governed_case")
    expected_governed = Decision(
        _text(
            fixture.get("expected_governed_decision"),
            field="expected_governed_decision",
        )
    )
    if not isinstance(cases, list) or not 0 < len(cases) <= MAX_CASES:
        raise ValueError("cases must be a bounded non-empty list")

    summaries: list[dict[str, object]] = []
    governed: Assessment | None = None
    matched = True
    for index, raw_case in enumerate(cases):
        case = _mapping(raw_case, field=f"cases[{index}]")
        name = _text(case.get("name"), field=f"cases[{index}].name")
        observations = case.get("observations")
        if not isinstance(observations, list) or len(observations) != 2:
            raise ValueError(f"case {name} must contain exactly two observations")
        expected = Decision(
            _text(case.get("expected_decision"), field=f"cases[{index}].expected")
        )
        expected_reasons_raw = case.get("expected_reasons")
        if not isinstance(expected_reasons_raw, list):
            raise ValueError(f"case {name} expected_reasons must be a list")
        expected_reasons = tuple(ReasonCode(value) for value in expected_reasons_raw)
        assessment = assess_observations(observations[0], observations[1])
        case_matched = (
            assessment.decision is expected and assessment.reasons == expected_reasons
        )
        matched = matched and case_matched
        summary = assessment.payload()
        summary.update({"expected_matched": case_matched, "name": name})
        summaries.append(summary)
        if name == governed_case:
            governed = assessment

    if governed is None:
        raise ValueError("governed_case did not match a fixture case")

    repository_evidence = exercise_disposable_repository(repository_root)
    repository_passed = bool(
        cast(dict[str, Any], repository_evidence["clean_candidate"])["stable"]
        and cast(dict[str, Any], repository_evidence["base_move"])["candidate_changed"]
        and not cast(dict[str, Any], repository_evidence["conflict"])[
            "candidate_available"
        ]
    )
    matched = matched and repository_passed and governed.decision is expected_governed
    return (
        {
            "case_count": len(summaries),
            "cases": summaries,
            "fixture_contract_passed": matched,
            "governed_case": governed_case,
            "governed_decision": governed.decision.value,
            "repository_evidence": repository_evidence,
            "schema_version": SCHEMA_VERSION,
        },
        matched,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path, help="bounded provider fixture JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="atlas-259-") as temp_dir:
        payload, passed = run_fixture(args.fixture, Path(temp_dir) / "repository")
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

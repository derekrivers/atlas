"""One-time operator command for the ruled ATLAS-280 mirror recovery.

This executable is permanently bound to ATLAS-280/ATLAS-281.  It accepts no
ticket argument and is never imported by PM sync, admission, Symphony, CI
handoff, startup, or migrations.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from atlas.github.client import GitHubRESTClient
from atlas.linear.client import (
    PROJECT_ID_ENV,
    TEAM_ID_ENV,
    LinearGraphQLClient,
)
from atlas.linear.ownership import LinearStatusMap
from atlas.pm.atlas_280_bootstrap_recovery import (
    Atlas280BootstrapApplyResult,
    Atlas280BootstrapCheckResult,
    Atlas280BootstrapRecoveryService,
)
from atlas.storage import Database

CONFIRMATION = "ATLAS-280-LOCAL-PLANNED-TO-CI-PENDING"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _accepted_main(repo: Path) -> str:
    if _git(repo, "status", "--porcelain"):
        raise RuntimeError("repository working tree is not clean")
    branch = _git(repo, "branch", "--show-current")
    head = _git(repo, "rev-parse", "HEAD")
    origin_main = _git(repo, "rev-parse", "origin/main")
    top = Path(_git(repo, "rev-parse", "--show-toplevel")).resolve()
    remote = _git(repo, "remote", "get-url", "origin")
    if top != repo.resolve():
        raise RuntimeError("command must run from the repository root")
    if branch != "main" or head != origin_main:
        raise RuntimeError("command requires clean exact current main")
    if not (
        remote.rstrip("/").endswith("derekrivers/atlas.git")
        or remote.rstrip("/").endswith("derekrivers/atlas")
    ):
        raise RuntimeError("origin is not the ruled derekrivers/atlas repository")
    return head


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"required operator environment field {name} is missing")
    return value


def _service(*, database_url: str, repo: Path) -> Atlas280BootstrapRecoveryService:
    accepted_main = _accepted_main(repo)
    team_id = _required_env(TEAM_ID_ENV)
    project_id = _required_env(PROJECT_ID_ENV)
    return Atlas280BootstrapRecoveryService(
        db=Database(database_url),
        linear=LinearGraphQLClient(),
        github=GitHubRESTClient(),
        status_map=LinearStatusMap.from_env(),
        team_id=team_id,
        project_id=project_id,
        accepted_main_commit=accepted_main,
        clock=lambda: datetime.now(UTC),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Exact-pair ATLAS-280/ATLAS-281 bootstrap mirror recovery"
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)
    check = subparsers.add_parser("check", help="read-only eligibility proof")
    check.add_argument("--db", required=True, help="explicit Atlas database URL")

    apply = subparsers.add_parser("apply", help="perform the one local mirror edge")
    apply.add_argument("--db", required=True, help="explicit Atlas database URL")
    apply.add_argument("--operator-id", required=True)
    apply.add_argument(
        "--confirm",
        required=True,
        help=f"must equal {CONFIRMATION}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.mode == "apply" and args.confirm != CONFIRMATION:
            raise RuntimeError("apply confirmation phrase did not match")
        service = _service(database_url=args.db, repo=Path.cwd())
        result: Atlas280BootstrapCheckResult | Atlas280BootstrapApplyResult
        if args.mode == "check":
            result = service.check()
        else:
            result = service.apply(operator_id=args.operator_id)
    except Exception as error:
        safe = {
            "ELIGIBLE": False,
            "changed": False,
            "error_type": type(error).__name__,
        }
        print(json.dumps(safe, sort_keys=True))
        return 2

    payload = result.model_dump(mode="json")
    payload["ELIGIBLE"] = result.eligible
    print(json.dumps(payload, sort_keys=True))
    return 0 if result.eligible or result.already_recovered else 2


if __name__ == "__main__":  # pragma: no cover - exercised through main
    sys.exit(main())

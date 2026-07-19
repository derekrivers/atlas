"""Cross-layer assembly of the live sync-tick configuration."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from atlas.core.anchors import SourceDocument
from atlas.linear.client import (
    PROJECT_ID_ENV,
    TEAM_ID_ENV,
    LinearGraphQLClient,
    MissingLinearTokenError,
)
from atlas.linear.ownership import LinearStatusMap
from atlas.planning.client import AnthropicPlannerClient
from atlas.planning.ingestion import (
    collect_input_documents,
    collect_processed_documents,
)
from atlas.pm import TickConfig
from atlas.storage import Database, TicketRepo


def build_tick_config(args: argparse.Namespace, resolved_db: Database) -> TickConfig:
    """Build the real `sync_tick` injection from config (D3): the live
    `LinearGraphQLClient` (creds from env), the env-configured `LinearStatusMap`,
    the team id from `LINEAR_TEAM_ID`, the project id from `LINEAR_PROJECT_ID`,
    the inbox dir from `--inbox-dir`, the pack-inputs documents provider from
    `--repo` (ATLAS-164), and the operator-invoked `--repair-packs` flag
    (ATLAS-169). Each boundary fails loud on a missing
    precondition — `LinearGraphQLClient()` raises `MissingLinearTokenError` without
    a key, `from_env()` raises `LinearStatusMapError` on a missing/malformed map,
    and an unset team id OR project id raises `MissingLinearTokenError` — so a
    misconfigured live path exits cleanly (the caller maps these to
    EXIT_PRECONDITION) rather than crashing mid-loop. The project id (ATLAS-135) is
    the project's UUID, NOT its slug, and scopes issue creation so created issues
    are visible to Symphony's project-scoped poll. Reads the environment only; it
    makes no network call, so it is testable with fake creds set in the
    environment.

    The `documents` provider is the ATLAS-162 collector pair over `--repo` — the
    §2.1 corpus plus the committed `processed/` stubs under `--inbox-dir`, exactly
    what `load_context_inputs` feeds `atlas context render`. It is built HERE
    because the import spine places `atlas.pm` below `atlas.planning`: the tick
    cannot import the collectors, so the CLI (which may) injects the closure, and
    the tick invokes it lazily — only when a push will actually embed, so its
    dirty-tree `DirtyInputError` fail-closed contract surfaces per embedding tick,
    never at config-build time."""

    client = LinearGraphQLClient()  # raises MissingLinearTokenError without a key
    status_map = LinearStatusMap.from_env()  # raises LinearStatusMapError if unset
    team_id = os.environ.get(TEAM_ID_ENV)
    if not team_id:
        raise MissingLinearTokenError(
            f"{TEAM_ID_ENV} is not set; the scheduler needs the Linear team id to "
            "create issues"
        )
    project_id = os.environ.get(PROJECT_ID_ENV)
    if not project_id:
        raise MissingLinearTokenError(
            f"{PROJECT_ID_ENV} is not set; the scheduler needs the Linear project "
            "id (a UUID, not the slug) to create issues Symphony can see"
        )
    repo_root = Path(args.repo)
    inbox_dir = Path(args.inbox_dir)

    def documents() -> list[SourceDocument]:
        return collect_input_documents(repo_root) + collect_processed_documents(
            repo_root, inbox_dir
        )

    return TickConfig(
        tickets=TicketRepo(resolved_db),
        db=resolved_db,
        client=client,
        status_map=status_map,
        team_id=team_id,
        project_id=project_id,
        inbox_dir=inbox_dir,
        documents=documents,
        repair_packs=args.repair_packs,
        lesson_client=AnthropicPlannerClient(),
    )

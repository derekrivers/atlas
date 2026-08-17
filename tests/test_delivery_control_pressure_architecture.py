"""ATLAS-261 thin, observational delivery-pressure API boundary."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATH = REPO_ROOT / "atlas/orchestration/delivery_control.py"
SNAPSHOT_REPO_PATH = REPO_ROOT / "atlas/storage/delivery_control_snapshot.py"
ROUTER_PATH = REPO_ROOT / "atlas/api/routers/delivery_control.py"


def _imports(source: str) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported


def _called_names(source: str) -> set[str]:
    return {
        ast.unparse(node.func)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
    }


def test_ac3_service_has_no_provider_validation_or_command_dependency() -> None:
    source = SERVICE_PATH.read_text(encoding="utf-8")
    imports = _imports(source)
    calls = _called_names(source)

    assert not any(
        name.startswith(
            (
                "atlas.github",
                "atlas.linear",
                "atlas.orchestration.pr_integration",
            )
        )
        for name in imports
    )
    assert not ({"subprocess", "pathlib"} & imports)
    prohibited_calls = {
        "calculate_validation_plan",
        "assess_pr_integration",
        "fetch_project_issues",
        "fetch_pull_request",
        "compare_commits",
        "try_acquire",
        "revise_current",
        "record",
        "set_state",
        "mark_stale",
        "merge",
        "rebase",
        "push",
    }
    assert not any(call.rsplit(".", 1)[-1] in prohibited_calls for call in calls)


def test_ac2_snapshot_repo_selects_evidence_identity_not_retained_payload() -> None:
    source = SNAPSHOT_REPO_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    evidence_selector = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_evidence_identities"
    )
    selected_attributes = {
        node.attr
        for node in ast.walk(evidence_selector)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "EvidenceRow"
    }

    assert selected_attributes == {
        "id",
        "commit_sha",
        "external_run_id",
        "job_name",
        "payload_hash",
        "status",
        "source_event_at",
        "created_at",
    }
    assert selected_attributes.isdisjoint(
        {"raw_payload", "summary", "source_uri", "created_by_id"}
    )


def test_ac2_snapshot_repo_owns_one_transaction_and_no_repository_fanout() -> None:
    source = SNAPSHOT_REPO_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    read = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "read"
        and isinstance(node.args.args[0], ast.arg)
    )
    calls = {
        ast.unparse(node.func) for node in ast.walk(read) if isinstance(node, ast.Call)
    }

    assert "Session" in calls
    assert "session.begin" in calls
    assert not any(call.endswith("Repo") for call in calls)


def test_ac2_snapshot_repo_pins_ci_history_to_the_current_status_episode() -> None:
    source = SNAPSHOT_REPO_PATH.read_text(encoding="utf-8")
    selector = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_latest_ci_reconciliations"
    )
    expressions = {
        ast.unparse(node)
        for node in ast.walk(selector)
        if isinstance(node, (ast.Call, ast.Compare))
    }

    assert "TicketRow.status_entered_at.is_not(None)" in expressions
    assert (
        "CIHandoffReconciliationRow.observed_at >= TicketRow.status_entered_at"
        in expressions
    )
    assert "TicketRow.status == 'ci_pending'" in expressions


def test_ac6_delivery_control_router_still_has_exactly_one_read_and_one_write() -> None:
    source = ROUTER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    routes = [
        (node.decorator_list[0].func.attr, node.decorator_list[0].args[0].value)
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.decorator_list
        and isinstance(node.decorator_list[0], ast.Call)
        and isinstance(node.decorator_list[0].func, ast.Attribute)
        and node.decorator_list[0].func.attr
        in {"get", "post", "put", "patch", "delete"}
        and node.decorator_list[0].args
        and isinstance(node.decorator_list[0].args[0], ast.Constant)
    ]

    assert routes == [("get", ""), ("post", "/policy")]

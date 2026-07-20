"""Structural fitness guards for the HTTP transport adapter."""

from __future__ import annotations

import ast
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1] / "atlas" / "api"
ROUTERS_ROOT = API_ROOT / "routers"


def _is_route_handler(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr in {"get", "post", "put", "patch", "delete"}
        for decorator in node.decorator_list
    )


def test_route_handlers_are_single_return_statements() -> None:
    handlers: list[tuple[Path, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for path in ROUTERS_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        handlers.extend(
            (path, node)
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and _is_route_handler(node)
        )

    assert handlers, "at least one example route handler must exist"
    forbidden_logic = (
        ast.IfExp,
        ast.BoolOp,
        ast.Lambda,
        ast.NamedExpr,
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
    )
    for path, handler in handlers:
        assert len(handler.body) == 1 and isinstance(handler.body[0], ast.Return), (
            f"{path}:{handler.lineno} {handler.name} must contain one return "
            "statement; move logic into a domain or orchestration service"
        )
        assert not any(
            isinstance(node, forbidden_logic) for node in ast.walk(handler.body[0])
        ), (
            f"{path}:{handler.lineno} {handler.name} contains transport-layer "
            "logic; move it into a domain or orchestration service"
        )


def test_api_and_cli_remain_independent_siblings() -> None:
    for path in API_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = {alias.name for alias in node.names}
                assert "atlas.cli" not in imported, f"{path} imports atlas.cli"
            elif isinstance(node, ast.ImportFrom):
                assert node.module != "atlas.cli", f"{path} imports atlas.cli"
                if node.module == "atlas":
                    imported = {alias.name for alias in node.names}
                    assert "cli" not in imported, f"{path} imports atlas.cli"

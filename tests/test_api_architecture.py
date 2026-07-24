"""Structural fitness guards for the HTTP transport adapter."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

API_ROOT = Path(__file__).resolve().parents[1] / "atlas" / "api"
ROUTERS_ROOT = API_ROOT / "routers"
DEPENDENCIES_PATH = API_ROOT / "dependencies.py"
LOWER_LAYER_OPERATION_MODULES = ("atlas.orchestration",)


@dataclass(frozen=True)
class _OperationCall:
    node_id: int
    lineno: int
    resource: str
    expression: str


@dataclass(frozen=True)
class _OperationUnit:
    lineno: int
    resources: frozenset[str]
    description: str


@dataclass(frozen=True)
class _ApiNoLogicViolation:
    path: Path
    lineno: int
    function_name: str
    reason: str


def _is_route_handler(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr in {"get", "post", "put", "patch", "delete"}
        for decorator in node.decorator_list
    )


def _annotation_text(annotation: ast.expr | None) -> str:
    return ast.unparse(annotation) if annotation is not None else ""


def _resource_parameter_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    resources: set[str] = set()
    for arg in args:
        annotation = _annotation_text(arg.annotation)
        if any(marker in annotation for marker in ("Repo", "Service", "Database")):
            resources.add(arg.arg)
    return resources


def _imported_lower_layer_operations(tree: ast.Module) -> set[str]:
    operations: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        if not any(
            node.module == module or node.module.startswith(f"{module}.")
            for module in LOWER_LAYER_OPERATION_MODULES
        ):
            continue
        operations.update(alias.asname or alias.name for alias in node.names)
    return operations


def _imported_repository_classes(tree: ast.Module) -> set[str]:
    repositories: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module != "atlas.storage":
            continue
        repositories.update(
            alias.asname or alias.name
            for alias in node.names
            if (alias.asname or alias.name).endswith("Repo")
        )
    return repositories


def _call_resource_owner(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _operation_call_from(
    call: ast.Call,
    *,
    lower_layer_operations: set[str],
    repository_classes: set[str],
    resource_parameters: set[str],
) -> _OperationCall | None:
    if isinstance(call.func, ast.Name) and call.func.id in lower_layer_operations:
        return _OperationCall(
            node_id=id(call),
            lineno=call.lineno,
            resource=call.func.id,
            expression=ast.unparse(call),
        )
    if isinstance(call.func, ast.Attribute):
        owner = _call_resource_owner(call.func.value)
        if owner in resource_parameters or owner in repository_classes:
            return _OperationCall(
                node_id=id(call),
                lineno=call.lineno,
                resource=owner,
                expression=ast.unparse(call),
            )
    return None


def _operation_calls(
    node: ast.AST,
    *,
    lower_layer_operations: set[str],
    repository_classes: set[str],
    resource_parameters: set[str],
) -> dict[int, _OperationCall]:
    calls: dict[int, _OperationCall] = {}
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        operation = _operation_call_from(
            child,
            lower_layer_operations=lower_layer_operations,
            repository_classes=repository_classes,
            resource_parameters=resource_parameters,
        )
        if operation is not None:
            calls[operation.node_id] = operation
    return calls


def _is_none_presence_check(test: ast.expr, parameter_names: set[str]) -> bool:
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        return all(
            _is_none_presence_check(value, parameter_names) for value in test.values
        )
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return False
    if not isinstance(test.ops[0], ast.Is | ast.IsNot):
        return False
    if len(test.comparators) != 1:
        return False
    left, right = test.left, test.comparators[0]
    left_is_param = isinstance(left, ast.Name) and left.id in parameter_names
    right_is_param = isinstance(right, ast.Name) and right.id in parameter_names
    left_is_none = isinstance(left, ast.Constant) and left.value is None
    right_is_none = isinstance(right, ast.Constant) and right.value is None
    return (left_is_param and right_is_none) or (left_is_none and right_is_param)


def _operation_ids_in(
    nodes: ast.AST | list[ast.AST],
    calls: dict[int, _OperationCall],
) -> set[int]:
    roots: list[ast.AST] = nodes if isinstance(nodes, list) else [nodes]
    operation_ids: set[int] = set()
    for root in roots:
        operation_ids.update(
            id(child)
            for child in ast.walk(root)
            if isinstance(child, ast.Call) and id(child) in calls
        )
    return operation_ids


def _branch_alternatives(
    node: ast.If | ast.IfExp,
) -> tuple[list[ast.AST], list[ast.AST]]:
    if isinstance(node, ast.If):
        return list(node.body), list(node.orelse)
    return [node.body], [node.orelse]


def _branch_operation_unit(
    node: ast.If | ast.IfExp,
    calls: dict[int, _OperationCall],
) -> _OperationUnit | None:
    body, orelse = _branch_alternatives(node)
    operation_ids = _operation_ids_in(body, calls) | _operation_ids_in(orelse, calls)
    if not operation_ids:
        return None

    resources = frozenset(calls[node_id].resource for node_id in operation_ids)
    if len(resources) != 1:
        return None

    body_count = len(_operation_ids_in(body, calls))
    orelse_count = len(_operation_ids_in(orelse, calls))
    if body_count > 1 or orelse_count > 1:
        return None

    expressions = ", ".join(calls[node_id].expression for node_id in operation_ids)
    return _OperationUnit(
        lineno=node.lineno,
        resources=resources,
        description=f"parameter-selected operation ({expressions})",
    )


def _response_dependency_provider(
    path: Path,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    return (
        path.name == "dependencies.py"
        and node.name.startswith("get_")
        and _annotation_text(node.returns).endswith("Response")
    )


def _api_adapter_functions(
    path: Path,
    tree: ast.Module,
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    if path.name == "dependencies.py":
        return [
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and (
                _response_dependency_provider(path, node)
                or _operation_calls(
                    node,
                    lower_layer_operations=_imported_lower_layer_operations(tree),
                    repository_classes=_imported_repository_classes(tree),
                    resource_parameters=_resource_parameter_names(node),
                )
            )
        ]
    if path.parent.name == "routers":
        return [
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and _is_route_handler(node)
        ]
    return []


def _api_no_logic_violations(path: Path, source: str) -> list[_ApiNoLogicViolation]:
    tree = ast.parse(source)
    lower_layer_operations = _imported_lower_layer_operations(tree)
    repository_classes = _imported_repository_classes(tree)
    violations: list[_ApiNoLogicViolation] = []

    for function in _api_adapter_functions(path, tree):
        calls = _operation_calls(
            function,
            lower_layer_operations=lower_layer_operations,
            repository_classes=repository_classes,
            resource_parameters=_resource_parameter_names(function),
        )
        branch_operation_ids: set[int] = set()
        branch_units: list[_OperationUnit] = []
        for branch in (
            node for node in ast.walk(function) if isinstance(node, ast.If | ast.IfExp)
        ):
            parameter_names = set(_parameter_names(function))
            if not _is_none_presence_check(branch.test, parameter_names):
                violations.append(
                    _ApiNoLogicViolation(
                        path=path,
                        lineno=branch.lineno,
                        function_name=function.name,
                        reason=(
                            "branches on domain state; only parameter presence "
                            "may select an API operation"
                        ),
                    )
                )
                continue

            branch_unit = _branch_operation_unit(branch, calls)
            if branch_unit is not None:
                branch_units.append(branch_unit)
                branch_operation_ids.update(
                    _operation_ids_in(_branch_alternatives(branch)[0], calls)
                    | _operation_ids_in(_branch_alternatives(branch)[1], calls)
                )

        operation_units = [
            _OperationUnit(
                lineno=call.lineno,
                resources=frozenset({call.resource}),
                description=call.expression,
            )
            for call in calls.values()
            if call.node_id not in branch_operation_ids
        ]
        operation_units.extend(branch_units)

        resources = set().union(
            *(unit.resources for unit in operation_units),
            set(),
        )
        if len(resources) > 1:
            violations.append(
                _ApiNoLogicViolation(
                    path=path,
                    lineno=function.lineno,
                    function_name=function.name,
                    reason=(
                        "calls different services or repositories in one API adapter"
                    ),
                )
            )

        if len(operation_units) > 1:
            call_list = ", ".join(unit.description for unit in operation_units)
            violations.append(
                _ApiNoLogicViolation(
                    path=path,
                    lineno=function.lineno,
                    function_name=function.name,
                    reason=(
                        "must make exactly one service or repository call before "
                        f"presentation; found {len(operation_units)} ({call_list})"
                    ),
                )
            )

        if _response_dependency_provider(path, function) and not operation_units:
            violations.append(
                _ApiNoLogicViolation(
                    path=path,
                    lineno=function.lineno,
                    function_name=function.name,
                    reason=(
                        "response dependency must make exactly one service or "
                        "repository call before presentation"
                    ),
                )
            )

    return violations


def _parameter_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    return [arg.arg for arg in args]


def _scan_api_no_logic_rule() -> list[_ApiNoLogicViolation]:
    violations: list[_ApiNoLogicViolation] = []
    for path in [DEPENDENCIES_PATH, *ROUTERS_ROOT.glob("*.py")]:
        violations.extend(
            _api_no_logic_violations(path, path.read_text(encoding="utf-8"))
        )
    return violations


def _dependency_violations_for(source: str) -> list[_ApiNoLogicViolation]:
    return _api_no_logic_violations(
        DEPENDENCIES_PATH,
        dedent(source).strip() + "\n",
    )


def _format_api_no_logic_violations(
    violations: list[_ApiNoLogicViolation],
) -> str:
    return "\n".join(
        f"{violation.path}:{violation.lineno} {violation.function_name}: "
        f"{violation.reason}"
        for violation in violations
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


def test_api_dependencies_make_one_service_or_repository_call_then_present() -> None:
    """Cite docs/atlas/operator-api.md canonical rule.

    "The API contains no logic: a route dependency makes exactly one service
    or repository call, then presents. Anything requiring more than one call, a
    branch on domain state, or cross-layer assembly moves to atlas.orchestration."
    """
    violations = _scan_api_no_logic_rule()

    assert not violations, _format_api_no_logic_violations(violations)


def test_api_no_logic_sensor_allows_ticket_board_parameter_selection() -> None:
    """Existing get_ticket_board may select list_by_status or list by parameter."""
    source = DEPENDENCIES_PATH.read_text(encoding="utf-8")
    violations = [
        violation
        for violation in _api_no_logic_violations(DEPENDENCIES_PATH, source)
        if violation.function_name == "get_ticket_board"
    ]

    assert violations == []


def test_api_no_logic_sensor_fires_on_seeded_extra_service_call() -> None:
    # Seeded red first with `assert 1 == 2` (B011); the wrong answer was a
    # dependency quietly making a second lower-layer operation call.
    violations = _dependency_violations_for(
        """
        from atlas.api.dependencies import TicketRepoDependency
        from atlas.api.presenters import present_ticket_board
        from atlas.api.schemas import TicketBoardResponse

        def get_seeded_board(tickets: TicketRepoDependency) -> TicketBoardResponse:
            selected = tickets.list()
            total = tickets.count()
            assert 1 == 2  # type: ignore[comparison-overlap]
            return present_ticket_board(selected)
        """
    )

    assert any("must make exactly one" in violation.reason for violation in violations)


def test_api_no_logic_sensor_fires_on_seeded_two_repositories() -> None:
    # Seeded red first with `assert 1 == 2` (B011); the wrong answer was
    # cross-repository assembly inside the API dependency.
    violations = _dependency_violations_for(
        """
        from atlas.api.dependencies import TicketRepoDependency
        from atlas.api.presenters import present_ticket_board
        from atlas.api.schemas import TicketBoardResponse

        def get_seeded_board(
            tickets: TicketRepoDependency,
            epics: EpicRepoDependency,
        ) -> TicketBoardResponse:
            selected = tickets.list()
            epics.list()
            assert 1 == 2  # type: ignore[comparison-overlap]
            return present_ticket_board(selected)
        """
    )

    assert any(
        "different services or repositories" in violation.reason
        for violation in violations
    )


def test_api_no_logic_sensor_fires_on_seeded_domain_status_branch() -> None:
    # Seeded red first with `assert 1 == 2` (B011); the wrong answer was
    # treating a domain-status decision as HTTP operation selection.
    violations = _dependency_violations_for(
        """
        from atlas.api.dependencies import TicketRepoDependency
        from atlas.api.presenters import present_ticket_board
        from atlas.api.schemas import TicketBoardResponse
        from atlas.core.models import TicketStatus

        def get_seeded_board(
            tickets: TicketRepoDependency,
            status: TicketStatus | None = None,
        ) -> TicketBoardResponse:
            if status is TicketStatus.REVIEW_REQUIRED:
                selected = tickets.list_by_status(status)
            else:
                selected = tickets.list()
            assert 1 == 2  # type: ignore[comparison-overlap]
            return present_ticket_board(selected)
        """
    )

    assert any(
        "branches on domain state" in violation.reason for violation in violations
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

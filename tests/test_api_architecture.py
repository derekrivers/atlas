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


def _operation_result_names(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    calls: dict[int, _OperationCall],
) -> set[str]:
    """Names assigned directly from one lower-layer operation result."""
    names: set[str] = set()
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Call)
            and id(node.value) in calls
        ):
            names.add(node.targets[0].id)
    return names


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
        presence_check_names = set(
            _parameter_names(function)
        ) | _operation_result_names(
            function,
            calls,
        )
        for branch in (
            node for node in ast.walk(function) if isinstance(node, ast.If | ast.IfExp)
        ):
            if not _is_none_presence_check(branch.test, presence_check_names):
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


def test_api_no_logic_sensor_allows_ticket_board_coordinating_service() -> None:
    """The board dependency may call one coordinating service."""
    source = DEPENDENCIES_PATH.read_text(encoding="utf-8")
    violations = [
        violation
        for violation in _api_no_logic_violations(DEPENDENCIES_PATH, source)
        if violation.function_name == "get_ticket_board"
    ]

    assert violations == []


def test_api_no_logic_sensor_allows_lessons_parameter_selection() -> None:
    """Existing get_lessons may select list_by_status or list by parameter."""
    source = DEPENDENCIES_PATH.read_text(encoding="utf-8")
    violations = [
        violation
        for violation in _api_no_logic_violations(DEPENDENCIES_PATH, source)
        if violation.function_name == "get_lessons"
    ]

    assert violations == []


def test_api_no_logic_sensor_fires_on_seeded_extra_service_call() -> None:
    # Seeded red first with `assert 1 == 2` (B011); the wrong answer was a
    # dependency quietly making a second lower-layer operation call.
    violations = _dependency_violations_for(
        """
        from atlas.api.dependencies import DatabaseDependency
        from atlas.api.presenters import present_ticket_board
        from atlas.api.schemas import TicketBoardResponse
        from atlas.orchestration import review_queue, ticket_board

        def get_seeded_board(database: DatabaseDependency) -> TicketBoardResponse:
            selected = ticket_board(database)
            review_queue(database)
            assert 1 == 2  # type: ignore[comparison-overlap]
            return present_ticket_board(selected)
        """
    )

    assert any("must make exactly one" in violation.reason for violation in violations)


def test_api_no_logic_sensor_fires_on_seeded_lessons_extra_call() -> None:
    # Seeded red first with `assert 1 == 2` (B011); the wrong answer was a
    # lessons dependency quietly making a second repository operation call.
    violations = _dependency_violations_for(
        """
        from atlas.api.dependencies import LessonRepoDependency
        from atlas.api.presenters import present_lessons
        from atlas.api.schemas import LessonsResponse
        from atlas.core.enums import EntityStatus

        def get_seeded_lessons(
            lessons: LessonRepoDependency,
            status: EntityStatus | None = None,
        ) -> LessonsResponse:
            selected = (
                lessons.list_by_status(status)
                if status is not None
                else lessons.list()
            )
            lessons.list_drafts()
            assert 1 == 2  # type: ignore[comparison-overlap]
            return present_lessons(selected)
        """
    )

    assert any("must make exactly one" in violation.reason for violation in violations)


def test_api_no_logic_sensor_allows_single_read_not_found_mapping() -> None:
    """A keyed read may map its absent result to the transport-level 404."""
    source = DEPENDENCIES_PATH.read_text(encoding="utf-8")
    violations = [
        violation
        for violation in _api_no_logic_violations(DEPENDENCIES_PATH, source)
        if violation.function_name == "get_ticket_detail"
    ]

    assert violations == []


def test_api_no_logic_sensor_fires_on_seeded_ticket_detail_second_call() -> None:
    violations = _dependency_violations_for(
        """
        from fastapi import HTTPException

        from atlas.api.dependencies import TicketRepoDependency
        from atlas.api.presenters import present_ticket_detail
        from atlas.api.schemas import TicketDetailResponse

        def get_seeded_ticket_detail(
            key: str,
            tickets: TicketRepoDependency,
        ) -> TicketDetailResponse:
            ticket = tickets.get_by_key(key)
            tickets.count()
            if ticket is None:
                raise HTTPException(status_code=404)
            return present_ticket_detail(ticket)
        """
    )

    assert any("must make exactly one" in violation.reason for violation in violations)


def test_api_no_logic_sensor_fires_on_seeded_ticket_evidence_extra_call() -> None:
    violations = _dependency_violations_for(
        """
        from fastapi import HTTPException

        from atlas.api.dependencies import DatabaseDependency
        from atlas.api.presenters import present_ticket_evidence
        from atlas.api.schemas import TicketEvidenceResponse
        from atlas.orchestration import review_queue, ticket_evidence

        def get_seeded_ticket_evidence(
            key: str,
            database: DatabaseDependency,
        ) -> TicketEvidenceResponse:
            evidence = ticket_evidence(database, key)
            review_queue(database)
            assert 1 == 2  # type: ignore[comparison-overlap]
            if evidence is None:
                raise HTTPException(status_code=404)
            return present_ticket_evidence(evidence)
        """
    )

    assert any("must make exactly one" in violation.reason for violation in violations)


def test_api_no_logic_sensor_fires_on_seeded_ticket_dependencies_extra_call() -> None:
    violations = _dependency_violations_for(
        """
        from fastapi import HTTPException

        from atlas.api.dependencies import DatabaseDependency
        from atlas.api.presenters import present_ticket_dependencies
        from atlas.api.schemas import TicketDependenciesResponse
        from atlas.orchestration import review_queue, ticket_dependencies

        def get_seeded_ticket_dependencies(
            key: str,
            database: DatabaseDependency,
        ) -> TicketDependenciesResponse:
            dependencies = ticket_dependencies(database, key)
            review_queue(database)
            assert 1 == 2  # type: ignore[comparison-overlap]
            if dependencies is None:
                raise HTTPException(status_code=404)
            return present_ticket_dependencies(dependencies)
        """
    )

    assert any("must make exactly one" in violation.reason for violation in violations)


def test_api_no_logic_sensor_fires_on_seeded_critical_path_extra_call() -> None:
    violations = _dependency_violations_for(
        """
        from atlas.api.dependencies import DatabaseDependency
        from atlas.api.presenters import present_dependency_critical_path
        from atlas.api.schemas import DependencyCriticalPathResponse
        from atlas.orchestration import dependency_critical_path, review_queue

        def get_seeded_dependency_critical_path(
            database: DatabaseDependency,
        ) -> DependencyCriticalPathResponse:
            path = dependency_critical_path(database)
            review_queue(database)
            assert 1 == 2  # type: ignore[comparison-overlap]
            return present_dependency_critical_path(path)
        """
    )

    assert any("must make exactly one" in violation.reason for violation in violations)


def test_api_no_logic_sensor_fires_on_seeded_dependency_graph_extra_call() -> None:
    violations = _dependency_violations_for(
        """
        from atlas.api.dependencies import DatabaseDependency
        from atlas.api.presenters import present_dependency_graph
        from atlas.api.schemas import DependencyGraphResponse
        from atlas.orchestration import dependency_graph, review_queue

        def get_seeded_dependency_graph(
            database: DatabaseDependency,
        ) -> DependencyGraphResponse:
            graph = dependency_graph(database)
            review_queue(database)
            assert 1 == 2  # type: ignore[comparison-overlap]
            return present_dependency_graph(graph)
        """
    )

    assert any("must make exactly one" in violation.reason for violation in violations)


def test_api_no_logic_sensor_fires_on_seeded_system_status_extra_call() -> None:
    violations = _dependency_violations_for(
        """
        from atlas.api.dependencies import DatabaseDependency
        from atlas.api.presenters import present_system_status
        from atlas.api.schemas import SystemStatusResponse
        from atlas.orchestration import review_queue, system_status

        def get_seeded_system_status(
            database: DatabaseDependency,
        ) -> SystemStatusResponse:
            state = system_status(database)
            review_queue(database)
            assert 1 == 2  # type: ignore[comparison-overlap]
            return present_system_status(state)
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


def _route_methods(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    methods: set[str] = set()
    for decorator in node.decorator_list:
        if (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr in {"post", "put", "patch", "delete"}
        ):
            methods.add(decorator.func.attr.upper())
    return methods


def _annotation_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    return {
        _annotation_text(arg.annotation)
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    }


def _context_dependency_aliases() -> set[str]:
    tree = ast.parse(DEPENDENCIES_PATH.read_text(encoding="utf-8"))
    context_consuming_functions = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and any("MutationContextDependency" in name for name in _annotation_names(node))
    }
    aliases = {"MutationContextDependency"}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value_text = ast.unparse(node.value)
        if any(
            f"Depends({function_name})" in value_text
            for function_name in context_consuming_functions
        ):
            aliases.add(target.id)
    return aliases


def _writable_route_security_violations_for(
    source: str,
    *,
    path: Path,
    context_aliases: set[str],
) -> list[str]:
    tree = ast.parse(dedent(source).strip() + "\n")
    violations: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        methods = _route_methods(node)
        if not methods or (
            path.name == "session.py" and node.name == "create_operator_session"
        ):
            continue
        annotations = _annotation_names(node)
        if not any(
            context_alias in annotation
            for context_alias in context_aliases
            for annotation in annotations
        ):
            violations.append(
                f"{path}:{node.lineno} {node.name} {sorted(methods)} "
                "does not depend on MutationContextDependency"
            )
    return violations


def test_writable_routes_require_shared_security_dependency() -> None:
    context_aliases = _context_dependency_aliases()
    assert "RevokedOperatorSessionDependency" in context_aliases

    violations: list[str] = []
    for path in ROUTERS_ROOT.glob("*.py"):
        violations.extend(
            _writable_route_security_violations_for(
                path.read_text(encoding="utf-8"),
                path=path,
                context_aliases=context_aliases,
            )
        )

    assert violations == []


def test_writable_route_security_sensor_fires_on_seeded_bypass() -> None:
    # Seeded red first with `assert 1 == 2` (B011); the wrong answer was a
    # writable route calling service logic without resolving the shared
    # mutation context.
    violations = _writable_route_security_violations_for(
        """
        from fastapi import APIRouter

        router = APIRouter()

        @router.post("/seeded")
        def seeded_write() -> dict[str, str]:
            assert 1 == 2
            return {"status": "bad"}
        """,
        path=Path("seeded.py"),
        context_aliases=_context_dependency_aliases(),
    )

    assert violations

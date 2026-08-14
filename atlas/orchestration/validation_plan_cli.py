"""Read-only CLI adapter for deterministic local-validation plans."""

from __future__ import annotations

import argparse
import sys
from importlib import resources

from atlas.verification.validation_plan import (
    ValidationRegistry,
    calculate_validation_plan,
    load_registry_bytes,
)

REGISTRY_RESOURCE = "validation_registry_v1.json"


def add_parser(subcommands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = subcommands.add_parser(
        "validation-plan",
        help="Calculate bounded local checks from exact identities and diff paths",
    )
    parser.add_argument(
        "--base", required=True, help="exact full lowercase base Git object id"
    )
    parser.add_argument(
        "--head", required=True, help="exact full lowercase head Git object id"
    )
    parser.add_argument(
        "--changed-path",
        action="append",
        default=[],
        help="repository-relative path from the base...head diff (repeatable)",
    )
    parser.add_argument(
        "--ticket-requirement",
        action="append",
        default=[],
        help="registered explicit ticket validation requirement (repeatable)",
    )
    parser.add_argument(
        "--ticket-test",
        action="append",
        default=[],
        help="explicit repository-relative ticket test file (repeatable)",
    )
    parser.add_argument(
        "--expect-registry-version",
        default=None,
        help="fail closed if the caller's registry version differs",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit canonical bounded JSON"
    )


def _packaged_registry() -> tuple[ValidationRegistry | None, str | None]:
    try:
        content = (
            resources.files("atlas.verification")
            .joinpath(REGISTRY_RESOURCE)
            .read_bytes()
        )
    except (FileNotFoundError, OSError):
        return None, "validation registry is unavailable"
    loaded = load_registry_bytes(content)
    return loaded.registry, loaded.error


def run_command(args: argparse.Namespace) -> int:
    registry, registry_error = _packaged_registry()
    plan = calculate_validation_plan(
        base=args.base,
        head=args.head,
        changed_paths=tuple(args.changed_path),
        ticket_requirements=tuple(args.ticket_requirement),
        ticket_tests=tuple(args.ticket_test),
        registry=registry,
        registry_error=registry_error,
        expected_registry_version=args.expect_registry_version,
    )
    if args.json:
        sys.stdout.buffer.write(plan.json_bytes())
    else:
        print(plan.human_text(), end="")
    return 0


__all__ = ["add_parser", "run_command"]

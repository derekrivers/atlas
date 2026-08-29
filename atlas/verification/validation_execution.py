"""Governed execution topology and fail-closed validation aggregation."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Final, Literal

from atlas.verification.validation_plan import FULL_SWEEP_COMMANDS, ValidationPlan

ExecutionStatus = Literal["passed", "failed"]

# Indices deliberately layer topology over the existing authoritative command
# inventory.  Command strings are not duplicated here.
FULL_SWEEP_LANE_INDICES: Final = (
    ("python", (0,)),
    ("static-governance", (1, 2, 3, 4, 5)),
    ("operator-ui", (6, 7)),
)


class ValidationTopologyError(ValueError):
    """The planned inventory cannot be mapped to governed execution groups."""


@dataclass(frozen=True)
class ValidationExecutionGroup:
    """One repository-owned serial lane; separate lanes may run concurrently."""

    name: str
    commands: tuple[str, ...]

    def payload(self) -> dict[str, object]:
        return {"name": self.name, "commands": list(self.commands)}


@dataclass(frozen=True)
class ValidationCommandResult:
    """Evidence for one attempted repository-owned command."""

    lane: str
    command: str
    exit_code: int | None
    started_at: str
    finished_at: str
    duration_seconds: float
    start_error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and self.start_error is None

    def payload(self) -> dict[str, object]:
        return {
            "command": self.command,
            "duration_seconds": self.duration_seconds,
            "exit_code": self.exit_code,
            "finished_at": self.finished_at,
            "lane": self.lane,
            "start_error": self.start_error,
            "status": "passed" if self.succeeded else "failed",
            "started_at": self.started_at,
        }


@dataclass(frozen=True)
class ValidationLaneResult:
    """Evidence for one lane, including a lane-level executor failure."""

    name: str
    command_results: tuple[ValidationCommandResult, ...]
    started_at: str
    finished_at: str
    duration_seconds: float
    executor_error: str | None = None

    def payload(self) -> dict[str, object]:
        return {
            "command_results": [result.payload() for result in self.command_results],
            "duration_seconds": self.duration_seconds,
            "executor_error": self.executor_error,
            "finished_at": self.finished_at,
            "name": self.name,
            "started_at": self.started_at,
        }


@dataclass(frozen=True)
class ValidationExecutionResult:
    """Aggregate exact-candidate execution evidence."""

    plan: ValidationPlan
    groups: tuple[ValidationExecutionGroup, ...]
    lane_results: tuple[ValidationLaneResult, ...]
    status: ExecutionStatus
    started_at: str
    finished_at: str
    duration_seconds: float
    missing_results: tuple[tuple[str, str], ...]
    duplicate_results: tuple[tuple[str, str], ...]
    unexpected_results: tuple[tuple[str, str], ...]
    topology_errors: tuple[str, ...]

    def payload(self) -> dict[str, object]:
        return {
            "duration_seconds": self.duration_seconds,
            "duplicate_results": [
                {"command": command, "lane": lane}
                for lane, command in self.duplicate_results
            ],
            "finished_at": self.finished_at,
            "groups": [group.payload() for group in self.groups],
            "lane_results": [lane.payload() for lane in self.lane_results],
            "missing_results": [
                {"command": command, "lane": lane}
                for lane, command in self.missing_results
            ],
            "plan": self.plan.payload(),
            "started_at": self.started_at,
            "status": self.status,
            "topology_errors": list(self.topology_errors),
            "unexpected_results": [
                {"command": command, "lane": lane}
                for lane, command in self.unexpected_results
            ],
        }

    def human_text(self) -> str:
        lines = [
            f"Validation execution: {self.status.upper()}",
            f"Base: {self.plan.base or 'ambiguous'}",
            f"Head: {self.plan.head or 'ambiguous'}",
            f"Wall time: {self.duration_seconds:.3f}s",
        ]
        for lane_result in self.lane_results:
            lines.append(
                f"Lane {lane_result.name}: {lane_result.duration_seconds:.3f}s"
            )
            for result in lane_result.command_results:
                exit_value = (
                    "not-started" if result.exit_code is None else result.exit_code
                )
                lines.append(
                    f"  [{exit_value}] {result.duration_seconds:.3f}s {result.command}"
                )
        for error in self.topology_errors:
            lines.append(f"Topology error: {error}")
        for lane_name, command in self.missing_results:
            lines.append(f"Missing result: {lane_name}: {command}")
        for lane_name, command in self.duplicate_results:
            lines.append(f"Duplicate result: {lane_name}: {command}")
        for lane_name, command in self.unexpected_results:
            lines.append(f"Unexpected result: {lane_name}: {command}")
        return "\n".join(lines) + "\n"


def execution_groups_for_plan(
    plan: ValidationPlan,
) -> tuple[ValidationExecutionGroup, ...]:
    """Resolve deterministic lanes without granting grouping choice to callers."""

    if not plan.commands:
        raise ValidationTopologyError("validation plan contains no commands")
    if not plan.full_sweep:
        return (ValidationExecutionGroup("selected", plan.commands),)
    if plan.commands != FULL_SWEEP_COMMANDS:
        raise ValidationTopologyError(
            "full-sweep command inventory drifted from the reviewed baseline"
        )
    try:
        groups = tuple(
            ValidationExecutionGroup(
                name,
                tuple(plan.commands[index] for index in command_indices),
            )
            for name, command_indices in FULL_SWEEP_LANE_INDICES
        )
    except IndexError as error:
        raise ValidationTopologyError(
            "full-sweep topology references a missing command"
        ) from error
    flattened = tuple(command for group in groups for command in group.commands)
    if flattened != plan.commands or len(set(flattened)) != len(flattened):
        raise ValidationTopologyError(
            "full-sweep topology does not account for every command exactly once"
        )
    return groups


def aggregate_execution_result(
    *,
    plan: ValidationPlan,
    groups: tuple[ValidationExecutionGroup, ...],
    lane_results: tuple[ValidationLaneResult, ...],
    started_at: str,
    finished_at: str,
    duration_seconds: float,
) -> ValidationExecutionResult:
    """Fail closed unless every planned lane and command has one passing result."""

    expected_lanes = Counter(group.name for group in groups)
    observed_lanes = Counter(lane.name for lane in lane_results)
    topology_errors: list[str] = []
    for lane, count in sorted((expected_lanes - observed_lanes).items()):
        topology_errors.append(f"missing lane result: {lane} ({count})")
    for lane, count in sorted((observed_lanes - expected_lanes).items()):
        topology_errors.append(f"unexpected lane result: {lane} ({count})")
    for lane, count in sorted(observed_lanes.items()):
        if count > 1:
            topology_errors.append(f"duplicate lane result: {lane} ({count})")

    expected_commands = Counter(
        (group.name, command) for group in groups for command in group.commands
    )
    observed_commands = Counter(
        (result.lane, result.command)
        for lane in lane_results
        for result in lane.command_results
    )
    missing = tuple(sorted((expected_commands - observed_commands).elements()))
    unexpected = tuple(
        sorted(
            identity
            for identity, count in observed_commands.items()
            for _ in range(count)
            if not expected_commands[identity]
        )
    )
    duplicate = tuple(
        sorted(
            identity
            for identity, count in observed_commands.items()
            for _ in range(max(0, count - expected_commands[identity]))
            if expected_commands[identity]
        )
    )
    command_results = tuple(
        result for lane in lane_results for result in lane.command_results
    )
    failed = (
        bool(topology_errors)
        or bool(missing)
        or bool(unexpected)
        or bool(duplicate)
        or any(lane.executor_error is not None for lane in lane_results)
        or any(not result.succeeded for result in command_results)
    )
    return ValidationExecutionResult(
        plan=plan,
        groups=groups,
        lane_results=lane_results,
        status="failed" if failed else "passed",
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=duration_seconds,
        missing_results=missing,
        duplicate_results=duplicate,
        unexpected_results=unexpected,
        topology_errors=tuple(topology_errors),
    )


__all__ = [
    "FULL_SWEEP_LANE_INDICES",
    "ValidationCommandResult",
    "ValidationExecutionGroup",
    "ValidationExecutionResult",
    "ValidationLaneResult",
    "ValidationTopologyError",
    "aggregate_execution_result",
    "execution_groups_for_plan",
]

"""Pure, deterministic local-validation planning (ATLAS-254).

The caller supplies exact repository identities and a changed-path set.  This
module never discovers a diff, consults a store or executes a command.  Its
only policy input is the reviewed, digest-pinned validation registry.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from dataclasses import dataclass
from typing import Any, Final, cast

REGISTRY_VERSION: Final = "validation-registry/v1"
REGISTRY_SHA256: Final = (
    "119ee591ccd153155a4e675ad3941e0481514676d4c7c5f75b249beea1f21194"
)
MAX_CHANGED_PATHS: Final = 256
MAX_TICKET_REQUIREMENTS: Final = 32
MAX_TICKET_TESTS: Final = 64
MAX_PATH_LENGTH: Final = 240

FULL_SWEEP_COMMANDS: Final = (
    "uv run pytest",
    "uv run ruff check .",
    "uv run ruff format --check .",
    "uv run mypy atlas tests",
    "uv run python -m atlas.tools.doc_linter",
    "uv run lint-imports",
    "./apps/operator-ui/scripts/ci.sh",
    "./apps/operator-ui/scripts/ci-e2e.sh",
)

_EXACT_IDENTITY = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_REQUIREMENT_NAME = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")


@dataclass(frozen=True)
class ValidationProfile:
    """One ordered, registry-owned command group."""

    name: str
    commands: tuple[str, ...]


@dataclass(frozen=True)
class PathRule:
    """A narrow path matcher and its additive profile selection."""

    rule_id: str
    profiles: tuple[str, ...]
    reason: str
    exact: tuple[str, ...] = ()
    prefix: str | None = None
    suffixes: tuple[str, ...] = ()

    def matches(self, path: str) -> bool:
        exact_match = path in self.exact
        prefix_match = self.prefix is not None and path.startswith(self.prefix)
        if not (exact_match or prefix_match):
            return False
        return not self.suffixes or path.endswith(self.suffixes)


@dataclass(frozen=True)
class ProtectedRule:
    """A protected cross-cutting path classification."""

    rule_id: str
    lane: str
    reason: str
    exact: tuple[str, ...] = ()
    prefix: str | None = None

    def matches(self, path: str) -> bool:
        return path in self.exact or (
            self.prefix is not None and path.startswith(self.prefix)
        )


@dataclass(frozen=True)
class ValidationRegistry:
    """Parsed v1 registry, safe to pass into the pure classifier."""

    version: str
    profile_order: tuple[str, ...]
    profiles: tuple[ValidationProfile, ...]
    path_rules: tuple[PathRule, ...]
    ticket_requirements: tuple[tuple[str, tuple[str, ...]], ...]
    protected_rules: tuple[ProtectedRule, ...]

    def profile(self, name: str) -> ValidationProfile:
        return next(profile for profile in self.profiles if profile.name == name)

    def requirement_profiles(self, name: str) -> tuple[str, ...] | None:
        return next(
            (
                profiles
                for requirement, profiles in self.ticket_requirements
                if requirement == name
            ),
            None,
        )


@dataclass(frozen=True)
class RegistryLoadResult:
    """Fail-closed result of parsing the packaged registry bytes."""

    registry: ValidationRegistry | None
    error: str | None


@dataclass(frozen=True, order=True)
class SelectionReason:
    profile: str
    source_kind: str
    source: str
    rule_id: str
    detail: str


@dataclass(frozen=True, order=True)
class FallbackReason:
    code: str
    detail: str


@dataclass(frozen=True, order=True)
class ProtectedSurfaceReason:
    lane: str
    path: str
    rule_id: str
    detail: str


@dataclass(frozen=True)
class ValidationPlan:
    """Bounded deterministic result; rendering introduces no new data."""

    registry_version: str
    base: str | None
    head: str | None
    diff_verification: str
    changed_paths: tuple[str, ...]
    changed_path_count: int
    ticket_requirements: tuple[str, ...]
    ticket_tests: tuple[str, ...]
    test_targets: tuple[str, ...]
    profiles: tuple[str, ...]
    commands: tuple[str, ...]
    reasons: tuple[SelectionReason, ...]
    protected_surface_reasons: tuple[ProtectedSurfaceReason, ...]
    fallback_reasons: tuple[FallbackReason, ...]
    full_sweep: bool

    def payload(self) -> dict[str, object]:
        return {
            "base": self.base,
            "changed_path_count": self.changed_path_count,
            "changed_paths": list(self.changed_paths),
            "commands": list(self.commands),
            "diff_verification": self.diff_verification,
            "fallback_reasons": [
                {"code": reason.code, "detail": reason.detail}
                for reason in self.fallback_reasons
            ],
            "full_sweep": self.full_sweep,
            "head": self.head,
            "profiles": list(self.profiles),
            "protected_surface_reasons": [
                {
                    "detail": reason.detail,
                    "lane": reason.lane,
                    "path": reason.path,
                    "rule_id": reason.rule_id,
                }
                for reason in self.protected_surface_reasons
            ],
            "reasons": [
                {
                    "detail": reason.detail,
                    "profile": reason.profile,
                    "rule_id": reason.rule_id,
                    "source": reason.source,
                    "source_kind": reason.source_kind,
                }
                for reason in self.reasons
            ],
            "registry_version": self.registry_version,
            "test_targets": list(self.test_targets),
            "ticket_requirements": list(self.ticket_requirements),
            "ticket_tests": list(self.ticket_tests),
        }

    def json_bytes(self) -> bytes:
        return (
            json.dumps(
                self.payload(),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode()

    def human_text(self) -> str:
        lines = [
            f"Validation plan ({self.registry_version})",
            f"Base: {self.base or 'ambiguous'}",
            f"Head: {self.head or 'ambiguous'}",
            f"Changed-path proof: {self.diff_verification}",
            f"Complete local sweep: {'required' if self.full_sweep else 'no'}",
        ]
        if self.fallback_reasons:
            lines.append("Fallback reasons:")
            lines.extend(
                f"  - {reason.code}: {reason.detail}"
                for reason in self.fallback_reasons
            )
        if self.protected_surface_reasons:
            lines.append("Protected surfaces:")
            lines.extend(
                f"  - {reason.path} [{reason.lane}]: {reason.detail}"
                for reason in self.protected_surface_reasons
            )
        lines.append("Profiles:")
        lines.extend(f"  - {profile}" for profile in self.profiles)
        if self.test_targets:
            lines.append("Mandatory test files:")
            lines.extend(f"  - {target}" for target in self.test_targets)
        lines.append("Commands (run in order):")
        lines.extend(
            f"  {index}. {command}"
            for index, command in enumerate(self.commands, start=1)
        )
        lines.append("Selection reasons:")
        lines.extend(
            f"  - {reason.profile} <- {reason.source_kind} {reason.source}: "
            f"{reason.detail}"
            for reason in self.reasons
        )
        return "\n".join(lines) + "\n"


def _string_tuple(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"registry field {field} must be a string list")
    return tuple(cast(list[str], value))


def _matcher_fields(
    value: dict[str, Any], *, field: str
) -> tuple[tuple[str, ...], str | None]:
    exact = _string_tuple(value.get("exact", []), field=f"{field}.exact")
    prefix_value = value.get("prefix")
    if prefix_value is not None and not isinstance(prefix_value, str):
        raise ValueError(f"registry field {field}.prefix must be a string")
    prefix = prefix_value
    if not exact and prefix is None:
        raise ValueError(f"registry matcher {field} is empty")
    return exact, prefix


def _parse_registry(document: object) -> ValidationRegistry:
    if not isinstance(document, dict):
        raise ValueError("registry root must be an object")
    data = cast(dict[str, Any], document)
    if (
        data.get("schema_version") != 1
        or data.get("registry_version") != REGISTRY_VERSION
    ):
        raise ValueError("registry version is not validation-registry/v1")

    profile_order = _string_tuple(data.get("profile_order"), field="profile_order")
    profiles_value = data.get("profiles")
    if not isinstance(profiles_value, dict):
        raise ValueError("registry profiles must be an object")
    profiles_data = cast(dict[str, Any], profiles_value)
    if tuple(profiles_data) != profile_order or len(set(profile_order)) != len(
        profile_order
    ):
        raise ValueError("registry profile order drifted from profile declarations")
    profiles: list[ValidationProfile] = []
    for name in profile_order:
        profile_value = profiles_data.get(name)
        if not isinstance(profile_value, dict):
            raise ValueError(f"registry profile {name} must be an object")
        commands = _string_tuple(
            cast(dict[str, Any], profile_value).get("commands"),
            field=f"profiles.{name}.commands",
        )
        if (not commands and name != "python") or any(
            not command or len(command) > 200 for command in commands
        ):
            raise ValueError(f"registry profile {name} has invalid commands")
        profiles.append(ValidationProfile(name=name, commands=commands))
    if (
        profiles[-1].name != "full-sweep"
        or profiles[-1].commands != FULL_SWEEP_COMMANDS
    ):
        raise ValueError("registry complete local sweep drifted from safe baseline")

    path_rules_value = data.get("path_rules")
    if not isinstance(path_rules_value, list):
        raise ValueError("registry path_rules must be a list")
    path_rules: list[PathRule] = []
    for index, rule_value in enumerate(path_rules_value):
        if not isinstance(rule_value, dict):
            raise ValueError("registry path rule must be an object")
        rule = cast(dict[str, Any], rule_value)
        exact, prefix = _matcher_fields(rule, field=f"path_rules[{index}]")
        rule_profiles = _string_tuple(rule.get("profiles"), field="path rule profiles")
        if not rule_profiles or any(
            profile not in profile_order for profile in rule_profiles
        ):
            raise ValueError("registry path rule names an unknown profile")
        suffixes = _string_tuple(rule.get("suffixes", []), field="path rule suffixes")
        path_rules.append(
            PathRule(
                rule_id=str(rule.get("id", "")),
                profiles=rule_profiles,
                reason=str(rule.get("reason", "")),
                exact=exact,
                prefix=prefix,
                suffixes=suffixes,
            )
        )

    requirements_value = data.get("ticket_requirements")
    if not isinstance(requirements_value, dict):
        raise ValueError("registry ticket_requirements must be an object")
    requirements: list[tuple[str, tuple[str, ...]]] = []
    for name, requirement_value in sorted(
        cast(dict[str, Any], requirements_value).items()
    ):
        requirement_profiles = _string_tuple(
            requirement_value, field=f"ticket_requirements.{name}"
        )
        if not requirement_profiles or any(
            profile not in profile_order for profile in requirement_profiles
        ):
            raise ValueError("registry ticket requirement names an unknown profile")
        requirements.append((name, requirement_profiles))

    protected_value = data.get("protected_rules")
    if not isinstance(protected_value, list):
        raise ValueError("registry protected_rules must be a list")
    protected: list[ProtectedRule] = []
    for index, rule_value in enumerate(protected_value):
        if not isinstance(rule_value, dict):
            raise ValueError("registry protected rule must be an object")
        rule = cast(dict[str, Any], rule_value)
        exact, prefix = _matcher_fields(rule, field=f"protected_rules[{index}]")
        protected.append(
            ProtectedRule(
                rule_id=str(rule.get("id", "")),
                lane=str(rule.get("lane", "")),
                reason=str(rule.get("reason", "")),
                exact=exact,
                prefix=prefix,
            )
        )
    if (
        any(not item.rule_id or not item.reason for item in path_rules)
        or any(not item.rule_id or not item.reason for item in protected)
        or any(not item.lane for item in protected)
    ):
        raise ValueError("registry rules require stable ids, reasons and lanes")

    return ValidationRegistry(
        version=REGISTRY_VERSION,
        profile_order=profile_order,
        profiles=tuple(profiles),
        path_rules=tuple(path_rules),
        ticket_requirements=tuple(requirements),
        protected_rules=tuple(protected),
    )


def load_registry_bytes(content: bytes) -> RegistryLoadResult:
    """Validate the versioned registry without performing filesystem I/O."""

    if hashlib.sha256(content).hexdigest() != REGISTRY_SHA256:
        return RegistryLoadResult(None, "validation registry digest mismatch")
    try:
        document = json.loads(content)
        return RegistryLoadResult(_parse_registry(document), None)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        return RegistryLoadResult(None, "validation registry schema drift")


def _normalise_path(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > MAX_PATH_LENGTH:
        return None
    if value.startswith("/") or value.endswith("/") or "\\" in value:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    segments = value.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        return None
    return value


def runner_profiles_for_test_path(path: str) -> tuple[str, ...] | None:
    """Return the profiles whose configured runners execute ``path``."""

    if _normalise_path(path) is None:
        return None
    if path.startswith("tests/"):
        filename = path.rsplit("/", 1)[-1]
        if filename.startswith("test_") and filename.endswith(".py"):
            return ("python",)
        return None
    if path.startswith("apps/operator-ui/tests/acceptance/") and path.endswith(
        ".test.ts"
    ):
        return ("ui",)
    if path.startswith("apps/operator-ui/tests/component/") and path.endswith(
        ".test.tsx"
    ):
        return ("browser",)
    if path.startswith("apps/operator-ui/tests/e2e/") and path.endswith(
        (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")
    ):
        return ("browser",)
    return None


def _profile_order_key(registry: ValidationRegistry, profile: str) -> int:
    return registry.profile_order.index(profile)


def _quoted_targets(paths: tuple[str, ...]) -> str:
    return " ".join(shlex.quote(path) for path in paths)


def _ui_targets(paths: tuple[str, ...]) -> tuple[str, ...]:
    prefix = "apps/operator-ui/"
    return tuple(path.removeprefix(prefix) for path in paths)


def _target_commands(profile: str, test_targets: tuple[str, ...]) -> tuple[str, ...]:
    """Render exact runner commands for deterministic changed/ticket tests."""

    python_targets = tuple(
        path
        for path in test_targets
        if runner_profiles_for_test_path(path) == ("python",)
    )
    acceptance_targets = tuple(
        path
        for path in test_targets
        if path.startswith("apps/operator-ui/tests/acceptance/")
    )
    component_targets = tuple(
        path
        for path in test_targets
        if path.startswith("apps/operator-ui/tests/component/")
    )
    e2e_targets = tuple(
        path for path in test_targets if path.startswith("apps/operator-ui/tests/e2e/")
    )
    if profile == "python" and python_targets:
        return (f"uv run pytest {_quoted_targets(python_targets)}",)
    if profile == "ui" and acceptance_targets:
        return (
            "cd apps/operator-ui && ./node_modules/.bin/vitest run "
            "--config vitest.config.ts "
            f"{_quoted_targets(_ui_targets(acceptance_targets))}",
        )
    if profile == "browser":
        commands: list[str] = []
        if component_targets:
            commands.append(
                "cd apps/operator-ui && ./node_modules/.bin/vitest run "
                "--config vitest.browser.config.ts "
                f"{_quoted_targets(_ui_targets(component_targets))}"
            )
        if e2e_targets:
            commands.append(
                "cd apps/operator-ui && ./node_modules/.bin/playwright test "
                "--config playwright.config.ts "
                f"{_quoted_targets(_ui_targets(e2e_targets))}"
            )
        return tuple(commands)
    return ()


def _path_has_registry_focused_python_tests(
    registry: ValidationRegistry, path: str
) -> bool:
    return any(
        command.startswith("uv run pytest ")
        for rule in registry.path_rules
        if rule.matches(path)
        for profile in rule.profiles
        if profile != "python"
        for command in registry.profile(profile).commands
    )


def _deduplicate_commands(commands: tuple[str, ...]) -> tuple[str, ...]:
    """Deduplicate commands and coalesce safe focused Pytest file targets."""

    focused_pytest_targets = tuple(
        sorted(
            {
                target
                for command in commands
                if command.startswith("uv run pytest ")
                for target in shlex.split(command)[3:]
            }
        )
    )
    focused_pytest_command = (
        f"uv run pytest {_quoted_targets(focused_pytest_targets)}"
        if focused_pytest_targets
        else None
    )
    result: list[str] = []
    emitted_focused_pytest = False
    for command in commands:
        if command.startswith("uv run pytest "):
            if not emitted_focused_pytest and focused_pytest_command is not None:
                result.append(focused_pytest_command)
                emitted_focused_pytest = True
            continue
        if command not in result:
            result.append(command)
    return tuple(result)


def _safe_fallback_plan(
    *,
    registry_version: str,
    base: str | None,
    head: str | None,
    diff_verification: str,
    changed_path_count: int,
    fallbacks: set[FallbackReason],
) -> ValidationPlan:
    return ValidationPlan(
        registry_version=registry_version,
        base=base,
        head=head,
        diff_verification=diff_verification,
        changed_paths=(),
        changed_path_count=changed_path_count,
        ticket_requirements=(),
        ticket_tests=(),
        test_targets=(),
        profiles=("full-sweep",),
        commands=FULL_SWEEP_COMMANDS,
        reasons=(),
        protected_surface_reasons=(),
        fallback_reasons=tuple(sorted(fallbacks)),
        full_sweep=True,
    )


def calculate_validation_plan(
    *,
    base: str,
    head: str,
    changed_paths: tuple[str, ...],
    ticket_requirements: tuple[str, ...] = (),
    ticket_tests: tuple[str, ...] = (),
    registry: ValidationRegistry | None,
    registry_error: str | None = None,
    expected_registry_version: str | None = None,
    diff_verification: str = "unverified",
    unverified_ticket_tests: tuple[str, ...] = (),
) -> ValidationPlan:
    """Select the smallest safe profiles, conservatively falling back.

    Inputs are values, not discovery instructions: this function has no Git,
    repository, network, database, clock, UUID or model dependency.
    """

    base_identity = base if _EXACT_IDENTITY.fullmatch(base) else None
    head_identity = head if _EXACT_IDENTITY.fullmatch(head) else None
    fallbacks: set[FallbackReason] = set()
    if diff_verification == "mismatch":
        fallbacks.add(
            FallbackReason(
                "changed_path_mismatch",
                "Supplied changed paths do not match the exact base-to-head Git diff.",
            )
        )
    elif diff_verification == "unavailable":
        fallbacks.add(
            FallbackReason(
                "git_diff_unavailable",
                "The exact base-to-head Git diff could not be proven read-only.",
            )
        )
    elif diff_verification != "verified":
        diff_verification = "unverified"
        fallbacks.add(
            FallbackReason(
                "unverified_changed_paths",
                "Changed paths require proof from the exact base-to-head Git diff.",
            )
        )
    if base_identity is None:
        fallbacks.add(
            FallbackReason(
                "ambiguous_base_identity",
                "Base must be a full lowercase Git object id.",
            )
        )
    if head_identity is None:
        fallbacks.add(
            FallbackReason(
                "ambiguous_head_identity",
                "Head must be a full lowercase Git object id.",
            )
        )
    unique_changed_inputs = tuple(dict.fromkeys(changed_paths))
    unique_requirement_inputs = tuple(dict.fromkeys(ticket_requirements))
    unique_ticket_test_inputs = tuple(dict.fromkeys(ticket_tests))
    unique_unverified_tests = tuple(dict.fromkeys(unverified_ticket_tests))

    if (
        base_identity is not None
        and base_identity == head_identity
        and unique_changed_inputs
    ):
        fallbacks.add(
            FallbackReason(
                "inconsistent_identity",
                "A non-empty diff cannot use identical base and head identities.",
            )
        )
    if not unique_changed_inputs:
        fallbacks.add(
            FallbackReason(
                "empty_changed_path_set",
                "An omitted diff cannot suppress mandatory validation.",
            )
        )

    if len(unique_changed_inputs) > MAX_CHANGED_PATHS:
        fallbacks.add(
            FallbackReason(
                "changed_path_bound_exceeded",
                f"Changed paths exceed the {MAX_CHANGED_PATHS}-path planning bound.",
            )
        )
    if len(unique_requirement_inputs) > MAX_TICKET_REQUIREMENTS:
        fallbacks.add(
            FallbackReason(
                "ticket_requirement_bound_exceeded",
                f"Ticket requirements exceed the {MAX_TICKET_REQUIREMENTS}-item bound.",
            )
        )
    if len(unique_ticket_test_inputs) > MAX_TICKET_TESTS:
        fallbacks.add(
            FallbackReason(
                "ticket_test_bound_exceeded",
                f"Ticket tests exceed the {MAX_TICKET_TESTS}-item bound.",
            )
        )

    registry_version = registry.version if registry is not None else REGISTRY_VERSION
    if registry is None:
        del registry_error
        fallbacks.add(
            FallbackReason(
                "validation_registry_drift",
                "Reviewed validation registry is unavailable or drifted.",
            )
        )
        return _safe_fallback_plan(
            registry_version=registry_version,
            base=base_identity,
            head=head_identity,
            diff_verification=diff_verification,
            changed_path_count=len(unique_changed_inputs),
            fallbacks=fallbacks,
        )
    if expected_registry_version not in {None, registry.version}:
        fallbacks.add(
            FallbackReason(
                "validation_registry_drift",
                "Caller's expected registry version does not match the "
                "reviewed registry.",
            )
        )

    if (
        len(unique_changed_inputs) > MAX_CHANGED_PATHS
        or len(unique_requirement_inputs) > MAX_TICKET_REQUIREMENTS
        or len(unique_ticket_test_inputs) > MAX_TICKET_TESTS
    ):
        return _safe_fallback_plan(
            registry_version=registry_version,
            base=base_identity,
            head=head_identity,
            diff_verification=diff_verification,
            changed_path_count=len(unique_changed_inputs),
            fallbacks=fallbacks,
        )

    normalised_paths = tuple(
        sorted(
            {
                path
                for value in unique_changed_inputs
                if (path := _normalise_path(value)) is not None
            }
        )
    )
    invalid_changed_count = len(unique_changed_inputs) - sum(
        _normalise_path(value) is not None for value in unique_changed_inputs
    )
    if invalid_changed_count:
        fallbacks.add(
            FallbackReason(
                "invalid_changed_path",
                "One or more changed paths are not bounded repository-relative paths.",
            )
        )

    normalised_tests = tuple(
        sorted(
            {
                path
                for value in unique_ticket_test_inputs
                if (path := _normalise_path(value)) is not None
            }
        )
    )
    if len(normalised_tests) != len(unique_ticket_test_inputs) or any(
        runner_profiles_for_test_path(path) is None for path in normalised_tests
    ):
        fallbacks.add(
            FallbackReason(
                "invalid_ticket_test",
                "Ticket tests must be recognised repository-relative test files.",
            )
        )

    selected: set[str] = set()
    reasons: set[SelectionReason] = set()
    protected: set[ProtectedSurfaceReason] = set()
    unverified_test_set = set(unique_unverified_tests)
    for path in normalised_tests:
        if path in unverified_test_set:
            fallbacks.add(
                FallbackReason(
                    "unverified_ticket_test",
                    f"Ticket test {path} is not a file at the supplied head identity.",
                )
            )

    test_targets = {
        path
        for path in normalised_paths
        if runner_profiles_for_test_path(path) is not None
    }
    test_targets.update(
        path
        for path in normalised_tests
        if runner_profiles_for_test_path(path) is not None
        and path not in unverified_test_set
    )

    for path in normalised_paths:
        matching_rules = tuple(
            rule for rule in registry.path_rules if rule.matches(path)
        )
        if not matching_rules:
            fallbacks.add(
                FallbackReason(
                    "unknown_path", f"No validation rule covers changed path {path}."
                )
            )
        for rule in matching_rules:
            for profile in rule.profiles:
                selected.add(profile)
                reasons.add(
                    SelectionReason(
                        profile, "changed_path", path, rule.rule_id, rule.reason
                    )
                )
        test_profiles = runner_profiles_for_test_path(path)
        if test_profiles is not None:
            for profile in test_profiles:
                selected.add(profile)
                reasons.add(
                    SelectionReason(
                        profile,
                        "changed_test",
                        path,
                        "mandatory-changed-test",
                        "Changed test files are always included.",
                    )
                )
        for protected_rule in registry.protected_rules:
            if protected_rule.matches(path):
                protected.add(
                    ProtectedSurfaceReason(
                        protected_rule.lane,
                        path,
                        protected_rule.rule_id,
                        protected_rule.reason,
                    )
                )

    requirements = tuple(
        sorted(
            {
                requirement
                for requirement in unique_requirement_inputs
                if _REQUIREMENT_NAME.fullmatch(requirement)
            }
        )
    )
    if len(requirements) != len(unique_requirement_inputs):
        fallbacks.add(
            FallbackReason(
                "invalid_ticket_requirement",
                "Ticket requirement names must use the bounded registry id format.",
            )
        )
    for requirement in requirements:
        requirement_profiles = registry.requirement_profiles(requirement)
        if requirement_profiles is None:
            fallbacks.add(
                FallbackReason(
                    "unknown_ticket_requirement",
                    "An explicit ticket requirement is not registered.",
                )
            )
            continue
        for profile in requirement_profiles:
            selected.add(profile)
            reasons.add(
                SelectionReason(
                    profile,
                    "ticket_requirement",
                    requirement,
                    f"ticket-requirement:{requirement}",
                    "Explicit ticket requirements are mandatory.",
                )
            )

    for path in normalised_tests:
        test_profiles = runner_profiles_for_test_path(path)
        if test_profiles is None:
            continue
        for profile in test_profiles:
            selected.add(profile)
            reasons.add(
                SelectionReason(
                    profile,
                    "ticket_test",
                    path,
                    "mandatory-ticket-test",
                    "Explicit ticket test files are always included.",
                )
            )

    python_targets = tuple(
        target
        for target in sorted(test_targets)
        if runner_profiles_for_test_path(target) == ("python",)
    )
    browser_targets = tuple(
        target
        for target in sorted(test_targets)
        if runner_profiles_for_test_path(target) == ("browser",)
    )
    python_requirement_selected = any(
        "python" in requirement_profiles
        for requirement in requirements
        if (requirement_profiles := registry.requirement_profiles(requirement))
        is not None
    )
    uncovered_python_paths = tuple(
        path
        for path in normalised_paths
        if runner_profiles_for_test_path(path) is None
        and any(
            "python" in rule.profiles
            for rule in registry.path_rules
            if rule.matches(path)
        )
        and not _path_has_registry_focused_python_tests(registry, path)
    )
    if (
        "python" in selected
        and "full-sweep" not in selected
        and not python_targets
        and (python_requirement_selected or uncovered_python_paths)
    ):
        fallbacks.add(
            FallbackReason(
                "missing_python_test_target",
                "Python implementation changed without a deterministic focused "
                "Python test target.",
            )
        )
    if "browser" in selected and "full-sweep" not in selected and not browser_targets:
        fallbacks.add(
            FallbackReason(
                "missing_browser_test_target",
                "Browser-system validation was selected without a deterministic "
                "component or end-to-end test target.",
            )
        )
    if fallbacks or "full-sweep" in selected:
        selected.add("full-sweep")

    ordered_profiles = tuple(
        profile for profile in registry.profile_order if profile in selected
    )
    full_sweep = "full-sweep" in selected
    commands: tuple[str, ...]
    if full_sweep:
        commands = FULL_SWEEP_COMMANDS
    else:
        ordered_targets = tuple(sorted(test_targets))
        commands = _deduplicate_commands(
            tuple(
                command
                for profile in ordered_profiles
                for command in (
                    *registry.profile(profile).commands,
                    *_target_commands(profile, ordered_targets),
                )
            )
        )
    ordered_reasons = tuple(
        sorted(
            reasons,
            key=lambda reason: (
                _profile_order_key(registry, reason.profile),
                reason.source_kind,
                reason.source,
                reason.rule_id,
                reason.detail,
            ),
        )
    )
    return ValidationPlan(
        registry_version=registry.version,
        base=base_identity,
        head=head_identity,
        diff_verification=diff_verification,
        changed_paths=normalised_paths,
        changed_path_count=len(unique_changed_inputs),
        ticket_requirements=requirements,
        ticket_tests=normalised_tests,
        test_targets=tuple(sorted(test_targets)),
        profiles=ordered_profiles,
        commands=commands,
        reasons=ordered_reasons,
        protected_surface_reasons=tuple(sorted(protected)),
        fallback_reasons=tuple(sorted(fallbacks)),
        full_sweep=full_sweep,
    )


__all__ = [
    "FULL_SWEEP_COMMANDS",
    "MAX_CHANGED_PATHS",
    "REGISTRY_SHA256",
    "REGISTRY_VERSION",
    "FallbackReason",
    "RegistryLoadResult",
    "SelectionReason",
    "ValidationPlan",
    "ValidationRegistry",
    "calculate_validation_plan",
    "load_registry_bytes",
    "runner_profiles_for_test_path",
]

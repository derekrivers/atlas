"""Deterministic protected integration-lane classification (ATLAS-258).

Only repository-owned registry data and trusted ticket metadata are consumed.
The classifier has no model-prose, Git, GitHub, Linear, repository or mutation
dependency; callers supply a materialised :class:`Ticket` and parsed registry.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, cast

from atlas.core.models.ticket import Ticket

REGISTRY_VERSION: Final = "protected-integration-lanes/v1"
REGISTRY_SHA256: Final = (
    "1e575a985b323d8ff2f38db8cb42f590fc85ae2169051fa4e9b0ac22083306b4"
)
REGISTRY_PATH: Final = Path(__file__).with_name("protected_lane_registry_v1.json")
MAX_LANES: Final = 32
MAX_RULES: Final = 128
MAX_CAPACITY: Final = 10
MAX_SELECTOR_LENGTH: Final = 128
MAX_PATH_LENGTH: Final = 240


def _canonical_selector(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    canonical = unicodedata.normalize("NFKC", value).strip().casefold()
    if not canonical or len(canonical) > MAX_SELECTOR_LENGTH:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in canonical):
        return None
    return canonical


def _canonical_path(value: object, *, prefix: bool = False) -> str | None:
    if not isinstance(value, str) or not value or len(value) > MAX_PATH_LENGTH:
        return None
    if unicodedata.normalize("NFKC", value) != value:
        return None
    if value.startswith("/") or "\\" in value:
        return None
    if prefix != value.endswith("/"):
        return None
    segments = value.removesuffix("/").split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    return value


@dataclass(frozen=True, order=True)
class ProtectedLane:
    """One bounded, stable protected integration lane."""

    key: str
    capacity: int
    operator_declared: bool


@dataclass(frozen=True, order=True)
class ProtectedLaneRule:
    """Additive exact metadata and canonical path matchers for one lane."""

    rule_id: str
    lane: str
    components: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    exact_paths: tuple[str, ...] = ()
    path_prefixes: tuple[str, ...] = ()

    def matches(self, kind: str, declaration: str) -> bool:
        if kind == "component":
            return declaration in self.components
        if kind == "tag":
            return declaration in self.tags
        if kind == "path":
            return declaration in self.exact_paths or any(
                declaration.startswith(prefix) for prefix in self.path_prefixes
            )
        return False


@dataclass(frozen=True)
class ProtectedLaneRegistry:
    """Normalised semantic registry with an order-independent fingerprint."""

    version: str
    lanes: tuple[ProtectedLane, ...]
    rules: tuple[ProtectedLaneRule, ...]

    def lane(self, key: str) -> ProtectedLane:
        return next(lane for lane in self.lanes if lane.key == key)

    def canonical_bytes(self) -> bytes:
        payload = {
            "lanes": [
                {
                    "capacity": lane.capacity,
                    "key": lane.key,
                    "operator_declared": lane.operator_declared,
                }
                for lane in self.lanes
            ],
            "registry_version": self.version,
            "rules": [
                {
                    "components": list(rule.components),
                    "exact_paths": list(rule.exact_paths),
                    "id": rule.rule_id,
                    "lane": rule.lane,
                    "path_prefixes": list(rule.path_prefixes),
                    "tags": list(rule.tags),
                }
                for rule in self.rules
            ],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


@dataclass(frozen=True)
class ProtectedLaneRegistryLoadResult:
    """Fail-closed result of parsing digest-pinned repository policy."""

    registry: ProtectedLaneRegistry | None
    error: str | None


class ProtectedLaneClassificationCode(StrEnum):
    """Closed defects in trusted ticket lane declarations."""

    INVALID_COMPONENT = "invalid_component"
    INVALID_TAG = "invalid_tag"
    INVALID_DECLARED_PATH = "invalid_declared_path"
    AMBIGUOUS_DECLARATION = "ambiguous_declaration"
    CONTRADICTORY_DECLARATION = "contradictory_declaration"


@dataclass(frozen=True, order=True)
class ProtectedLaneClassificationIssue:
    """One bounded declaration defect; no ticket prose is retained."""

    code: ProtectedLaneClassificationCode
    source_kind: str
    declaration: str


@dataclass(frozen=True, order=True)
class ProtectedLaneMatch:
    """One lane plus every declaration/rule pair that selected it."""

    lane: str
    declarations: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True)
class ProtectedLaneClassification:
    """Complete deterministic classification for one materialised ticket."""

    registry_version: str
    registry_fingerprint: str
    ticket_key: str
    matches: tuple[ProtectedLaneMatch, ...]
    issues: tuple[ProtectedLaneClassificationIssue, ...]

    @property
    def lanes(self) -> tuple[str, ...]:
        return tuple(match.lane for match in self.matches)

    def canonical_bytes(self) -> bytes:
        payload = {
            "issues": [
                {
                    "code": issue.code.value,
                    "declaration": issue.declaration,
                    "source_kind": issue.source_kind,
                }
                for issue in self.issues
            ],
            "matches": [
                {"declarations": match.declarations, "lane": match.lane}
                for match in self.matches
            ],
            "registry_fingerprint": self.registry_fingerprint,
            "registry_version": self.registry_version,
            "ticket_key": self.ticket_key,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


@dataclass(frozen=True)
class ProtectedLaneClassifierInput:
    """Exact trusted declarations consumed by the repository classifier.

    This pure materialisation seam lets bounded offline validators classify an
    already selected ticket projection without manufacturing unrelated
    persistence and lifecycle fields on :class:`Ticket`.
    """

    ticket_key: str
    component: str | None = None
    tags: tuple[str, ...] = ()
    relevant_docs: tuple[str, ...] = ()
    documentation_requirements: tuple[str, ...] = ()


def _string_list(value: object, *, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a string list")
    return cast(list[str], value)


def _normalised_selectors(value: object, *, field: str) -> tuple[str, ...]:
    raw = _string_list(value, field=field)
    canonical = [_canonical_selector(item) for item in raw]
    if any(item is None for item in canonical):
        raise ValueError(f"{field} contains an invalid selector")
    values = cast(list[str], canonical)
    if len(values) != len(set(values)):
        raise ValueError(f"{field} contains an ambiguous selector")
    return tuple(sorted(values))


def _normalised_paths(
    value: object, *, field: str, prefix: bool = False
) -> tuple[str, ...]:
    raw = _string_list(value, field=field)
    canonical = [_canonical_path(item, prefix=prefix) for item in raw]
    if any(item is None for item in canonical):
        raise ValueError(f"{field} contains an invalid path")
    values = cast(list[str], canonical)
    if len(values) != len(set(values)):
        raise ValueError(f"{field} contains an ambiguous path")
    return tuple(sorted(values))


def parse_protected_lane_registry(document: object) -> ProtectedLaneRegistry:
    """Parse and normalise one semantic v1 registry document."""

    if not isinstance(document, dict):
        raise ValueError("protected lane registry root must be an object")
    data = cast(dict[str, Any], document)
    if (
        data.get("schema_version") != 1
        or data.get("registry_version") != REGISTRY_VERSION
    ):
        raise ValueError("protected lane registry version is unsupported")

    lane_values = data.get("lanes")
    if not isinstance(lane_values, list) or not 1 <= len(lane_values) <= MAX_LANES:
        raise ValueError("protected lane registry has an invalid lane set")
    lanes: list[ProtectedLane] = []
    for raw in lane_values:
        if not isinstance(raw, dict):
            raise ValueError("protected lane definition must be an object")
        item = cast(dict[str, Any], raw)
        key = _canonical_selector(item.get("key"))
        capacity = item.get("capacity")
        operator_declared = item.get("operator_declared")
        if (
            key is None
            or type(capacity) is not int
            or not 1 <= capacity <= MAX_CAPACITY
            or type(operator_declared) is not bool
        ):
            raise ValueError("protected lane definition is invalid")
        lanes.append(ProtectedLane(key, capacity, operator_declared))
    if len({lane.key for lane in lanes}) != len(lanes):
        raise ValueError("protected lane keys are duplicate or ambiguous")
    lanes.sort()

    rule_values = data.get("rules")
    if not isinstance(rule_values, list) or not 1 <= len(rule_values) <= MAX_RULES:
        raise ValueError("protected lane registry has an invalid rule set")
    lane_keys = {lane.key for lane in lanes}
    rules: list[ProtectedLaneRule] = []
    for index, raw in enumerate(rule_values):
        if not isinstance(raw, dict):
            raise ValueError("protected lane rule must be an object")
        item = cast(dict[str, Any], raw)
        rule_id = _canonical_selector(item.get("id"))
        lane = _canonical_selector(item.get("lane"))
        rule = ProtectedLaneRule(
            rule_id=rule_id or "",
            lane=lane or "",
            components=_normalised_selectors(
                item.get("components"), field=f"rules[{index}].components"
            ),
            tags=_normalised_selectors(item.get("tags"), field=f"rules[{index}].tags"),
            exact_paths=_normalised_paths(
                item.get("exact_paths"), field=f"rules[{index}].exact_paths"
            ),
            path_prefixes=_normalised_paths(
                item.get("path_prefixes"),
                field=f"rules[{index}].path_prefixes",
                prefix=True,
            ),
        )
        if (
            not rule.rule_id
            or rule.lane not in lane_keys
            or not (
                rule.components or rule.tags or rule.exact_paths or rule.path_prefixes
            )
        ):
            raise ValueError("protected lane rule is invalid")
        rules.append(rule)
    if len({rule.rule_id for rule in rules}) != len(rules):
        raise ValueError("protected lane rule ids are duplicate or ambiguous")
    if {rule.lane for rule in rules} != lane_keys:
        raise ValueError("every protected lane must have a classification rule")
    rules.sort()
    return ProtectedLaneRegistry(REGISTRY_VERSION, tuple(lanes), tuple(rules))


def load_protected_lane_registry_bytes(
    content: bytes, *, expected_sha256: str = REGISTRY_SHA256
) -> ProtectedLaneRegistryLoadResult:
    """Validate digest and schema without performing filesystem I/O."""

    if hashlib.sha256(content).hexdigest() != expected_sha256:
        return ProtectedLaneRegistryLoadResult(
            None, "protected lane registry digest mismatch"
        )
    try:
        return ProtectedLaneRegistryLoadResult(
            parse_protected_lane_registry(json.loads(content)), None
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        return ProtectedLaneRegistryLoadResult(
            None, "protected lane registry schema drift"
        )


def load_packaged_protected_lane_registry() -> ProtectedLaneRegistryLoadResult:
    """Read and validate the repository-owned registry for one decision seam."""

    try:
        content = REGISTRY_PATH.read_bytes()
    except OSError:
        return ProtectedLaneRegistryLoadResult(
            None, "protected lane registry is unavailable"
        )
    return load_protected_lane_registry_bytes(content)


def _ticket_declarations(
    classifier_input: ProtectedLaneClassifierInput,
) -> tuple[tuple[tuple[str, str], ...], tuple[ProtectedLaneClassificationIssue, ...]]:
    declarations: set[tuple[str, str]] = set()
    issues: set[ProtectedLaneClassificationIssue] = set()

    if classifier_input.component is not None:
        component = _canonical_selector(classifier_input.component)
        if component is None:
            issues.add(
                ProtectedLaneClassificationIssue(
                    ProtectedLaneClassificationCode.INVALID_COMPONENT,
                    "component",
                    "invalid",
                )
            )
        else:
            declarations.add(("component", component))

    canonical_tags: dict[str, str] = {}
    for raw in classifier_input.tags:
        tag = _canonical_selector(raw)
        if tag is None:
            issues.add(
                ProtectedLaneClassificationIssue(
                    ProtectedLaneClassificationCode.INVALID_TAG, "tag", "invalid"
                )
            )
            continue
        prior = canonical_tags.get(tag)
        if prior is not None and prior != raw:
            issues.add(
                ProtectedLaneClassificationIssue(
                    ProtectedLaneClassificationCode.CONTRADICTORY_DECLARATION,
                    "tag",
                    tag,
                )
            )
        canonical_tags[tag] = raw
        declarations.add(("tag", tag))

    for raw in (
        *classifier_input.relevant_docs,
        *classifier_input.documentation_requirements,
    ):
        path = _canonical_path(raw)
        if path is None:
            issues.add(
                ProtectedLaneClassificationIssue(
                    ProtectedLaneClassificationCode.INVALID_DECLARED_PATH,
                    "path",
                    "invalid",
                )
            )
            continue
        declarations.add(("path", path))
    return tuple(sorted(declarations)), tuple(sorted(issues))


def classify_protected_lane_inputs(
    classifier_input: ProtectedLaneClassifierInput,
    registry: ProtectedLaneRegistry,
) -> ProtectedLaneClassification:
    """Classify exact component, tag and path inputs; never inspect prose."""

    declarations, initial_issues = _ticket_declarations(classifier_input)
    issues = set(initial_issues)
    matches: dict[str, set[tuple[str, str, str]]] = {}
    for kind, declaration in declarations:
        matching_rules = tuple(
            rule for rule in registry.rules if rule.matches(kind, declaration)
        )
        matching_lanes = {rule.lane for rule in matching_rules}
        if len(matching_lanes) > 1:
            issues.add(
                ProtectedLaneClassificationIssue(
                    ProtectedLaneClassificationCode.AMBIGUOUS_DECLARATION,
                    kind,
                    declaration,
                )
            )
        for rule in matching_rules:
            matches.setdefault(rule.lane, set()).add((kind, declaration, rule.rule_id))
    ordered_matches = tuple(
        ProtectedLaneMatch(lane, tuple(sorted(evidence)))
        for lane, evidence in sorted(matches.items())
    )
    return ProtectedLaneClassification(
        registry_version=registry.version,
        registry_fingerprint=registry.fingerprint,
        ticket_key=classifier_input.ticket_key,
        matches=ordered_matches,
        issues=tuple(sorted(issues)),
    )


def classify_ticket_protected_lanes(
    ticket: Ticket, registry: ProtectedLaneRegistry
) -> ProtectedLaneClassification:
    """Classify one materialised ticket through the pure declaration seam."""

    return classify_protected_lane_inputs(
        ProtectedLaneClassifierInput(
            ticket_key=ticket.key,
            component=ticket.component,
            tags=tuple(ticket.tags),
            relevant_docs=tuple(ticket.relevant_docs),
            documentation_requirements=tuple(ticket.documentation_requirements),
        ),
        registry,
    )


_PACKAGED = load_packaged_protected_lane_registry()
if _PACKAGED.registry is None:  # checked-in bytes must be internally coherent
    raise RuntimeError(_PACKAGED.error or "protected lane registry is invalid")
DEFAULT_PROTECTED_LANE_REGISTRY: Final = _PACKAGED.registry


__all__ = [
    "DEFAULT_PROTECTED_LANE_REGISTRY",
    "REGISTRY_SHA256",
    "REGISTRY_VERSION",
    "ProtectedLane",
    "ProtectedLaneClassification",
    "ProtectedLaneClassificationCode",
    "ProtectedLaneClassificationIssue",
    "ProtectedLaneClassifierInput",
    "ProtectedLaneMatch",
    "ProtectedLaneRegistry",
    "ProtectedLaneRegistryLoadResult",
    "ProtectedLaneRule",
    "classify_protected_lane_inputs",
    "classify_ticket_protected_lanes",
    "load_packaged_protected_lane_registry",
    "load_protected_lane_registry_bytes",
    "parse_protected_lane_registry",
]

"""YAML serialisation layer for docs/planning renders (ATLAS-17).

Implements knowledge-core.md "Planning render format": deterministic,
declaration-ordered, block-style, alias-free YAML for every canonical
model, plus the planning-render document shape (plural top-level key,
ascending entries, generated header comment).

This layer renders to caller-supplied paths only. ``atlas apply``
(ATLAS-27) is the sole legal writer of ``docs/planning/`` (ADR-0007);
nothing here ever touches that directory.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, TypeVar
from uuid import UUID

import yaml
from pydantic import BaseModel

from atlas.core.keys import natural_key

M = TypeVar("M", bound=BaseModel)

_DEPENDENCY_SORT_FIELDS = (
    "source_ticket_id",
    "target_entity_type",
    "target_entity_id",
    "dependency_type",
)


class UnknownFieldError(ValueError):
    """An entry carries a key its model does not declare (fail closed)."""

    def __init__(self, model_name: str, where: str, keys: set[str]) -> None:
        super().__init__(
            f"unknown key(s) {sorted(keys)} in {model_name} entry ({where}); "
            "deserialisation is fail-closed and never ignores keys"
        )
        self.model_name = model_name
        self.keys = keys


class RenderFormatError(ValueError):
    """A YAML document does not match the planning-render shape."""


class _RenderDumper(yaml.SafeDumper):
    """Block-style, alias-free dumper with literal multi-line strings."""

    def ignore_aliases(self, data: Any) -> bool:
        return True


# YAML treats these as line breaks and normalises raw occurrences to \n
# on load (silent corruption, found by the ATLAS-19 property tests);
# strings containing them must emit double-quoted with escapes.
_UNICODE_BREAKS = ("\x85", "\u2028", "\u2029")


def _str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    # Multi-line strings emit as literal block scalars for diff
    # readability (knowledge-core "Planning render format").
    if any(break_char in data for break_char in _UNICODE_BREAKS):
        style: str | None = '"'
    elif "\n" in data:
        style = "|"
    else:
        style = None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_RenderDumper.add_representer(str, _str_representer)


def _sorted_mappings(value: Any) -> Any:
    """Mapping-valued fields emit with sorted keys; lists recurse."""
    if isinstance(value, dict):
        return {key: _sorted_mappings(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_sorted_mappings(item) for item in value]
    return value


def _entry_dict(model: BaseModel) -> dict[str, Any]:
    """JSON-mode dump in declaration order, nested mappings sorted."""
    data = model.model_dump(mode="json")
    return {name: _sorted_mappings(data[name]) for name in type(model).model_fields}


def _dump(data: Any) -> str:
    return yaml.dump(
        data,
        Dumper=_RenderDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=4096,
    )


def _check_unknown_keys(
    model_cls: type[BaseModel], mapping: dict[str, Any], where: str
) -> None:
    unknown = set(mapping) - set(model_cls.model_fields)
    if unknown:
        raise UnknownFieldError(model_cls.__name__, where, unknown)


def _sort_key(model: BaseModel) -> tuple[str | int, ...]:
    fields = type(model).model_fields
    if "key" in fields:
        return natural_key(str(model.key))  # type: ignore[attr-defined]
    if set(_DEPENDENCY_SORT_FIELDS) <= fields.keys():
        return tuple(str(getattr(model, name)) for name in _DEPENDENCY_SORT_FIELDS)
    return (str(getattr(model, "id", "")),)


def dump_entity(model: BaseModel) -> str:
    """One model as a deterministic YAML document."""
    return _dump(_entry_dict(model))


def load_entity(model_cls: type[M], text: str) -> M:
    """YAML document -> model; unknown keys raise UnknownFieldError."""
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise RenderFormatError(
            f"expected a mapping for {model_cls.__name__}, got {type(data).__name__}"
        )
    _check_unknown_keys(model_cls, data, "document")
    return model_cls.model_validate(data)


@dataclass(frozen=True)
class RenderHeader:
    """Generated header for planning renders. The two key counters are
    independent (knowledge-core "Key counter"), so the header carries both
    high-water marks, not one; atlas apply (ATLAS-27) supplies the values."""

    plan_run_id: UUID | str
    prompt_version: str
    ticket_key_high_water: int
    epic_key_high_water: int

    def lines(self) -> list[str]:
        return [
            "# Render written only by `atlas apply` (ADR-0006/0007); do not hand-edit.",
            f"# plan_run_id: {self.plan_run_id}",
            f"# prompt_version: {self.prompt_version}",
            f"# ticket_key_high_water: {self.ticket_key_high_water}",
            f"# epic_key_high_water: {self.epic_key_high_water}",
        ]


def render_document(
    plural_key: str, entries: Sequence[BaseModel], header: RenderHeader
) -> str:
    """Planning-render document: header, plural top-level key, entries
    ascending (natural key order; dependency tuple for keyless entries)."""
    ordered = sorted(entries, key=_sort_key)
    body = _dump({plural_key: [_entry_dict(entry) for entry in ordered]})
    return "\n".join([*header.lines(), body])


def parse_document(model_cls: type[M], text: str, plural_key: str) -> list[M]:
    """Planning-render document -> models; fail closed on shape and keys."""
    data = yaml.safe_load(text)
    if not isinstance(data, dict) or set(data) != {plural_key}:
        raise RenderFormatError(f"expected a single top-level key {plural_key!r}")
    entries = data[plural_key]
    if not isinstance(entries, list):
        raise RenderFormatError(f"{plural_key!r} must hold a list of entries")
    models = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise RenderFormatError(f"{plural_key}[{index}] is not a mapping")
        _check_unknown_keys(model_cls, entry, f"{plural_key}[{index}]")
        models.append(model_cls.model_validate(entry))
    return models

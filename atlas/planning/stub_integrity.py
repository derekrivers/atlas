"""Fail-closed integrity validation for committed planning inbox batches.

Phase-planning bundles are externally assembled, but the canonical safety
boundary is Atlas itself.  This module validates every committed stub before
either plan path can persist a PlanRun and is run again by apply before the
operator confirmation gate.

Normal PM follow-up stubs remain supported without a batch manifest.  Ordered
phase stubs (``inbox-stub-NN-*.md``) require exactly one committed
``planning-batch-*.yaml`` manifest so Atlas can prove exact overlay and stub
coverage rather than trusting the handoff tool that produced the files.
"""

from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from atlas.core.anchors import IngestionError, SourceDocument
from atlas.planning.ingestion import processed_path_for
from atlas.planning.promotion import (
    StubPromotionError,
    _depends_on_refs,
    _parse_front_matter,
)
from atlas.planning.reconciler import Backlog

_ORDERED_STUB_RE = re.compile(r"^inbox-stub-(\d{2})-[a-z0-9-]+\.md$")
_TICKET_KEY_RE = re.compile(r"^ATLAS-\d+$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_PATH_RE = re.compile(r"^[A-Za-z0-9._/-]+\.md$")
_FORBIDDEN_PATH_TOKENS = ("*", "?", "[", "]", "{", "}", "\\")


class PlanningBatchIntegrityError(IngestionError):
    """A governed planning batch is incomplete, stale, or ambiguous."""


def _git(repo_root: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or error.stdout.strip()
        raise PlanningBatchIntegrityError(
            f"planning batch integrity check could not run git {' '.join(args)}: "
            f"{detail}"
        ) from error


def _exact_path(value: object, *, stub_path: str, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise StubPromotionError(
            stub_path, field, "must contain non-empty repository-relative paths"
        )
    if value != value.strip():
        raise StubPromotionError(
            stub_path, field, f"path has surrounding whitespace: {value!r}"
        )
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or ".." in pure.parts
        or not _PATH_RE.fullmatch(value)
        or any(token in value for token in _FORBIDDEN_PATH_TOKENS)
    ):
        raise StubPromotionError(
            stub_path,
            field,
            f"must contain exact repository-relative Markdown paths, got {value!r}",
        )
    return value


def _path_list(data: dict[str, object], field: str, stub_path: str) -> list[str]:
    if field not in data:
        return []
    raw = data[field]
    if not isinstance(raw, list):
        raise StubPromotionError(stub_path, field, f"{field} must be a list")
    paths = [_exact_path(value, stub_path=stub_path, field=field) for value in raw]
    if len(paths) != len(set(paths)):
        raise StubPromotionError(stub_path, field, f"{field} contains duplicate paths")
    return paths


def _manifest_stub_paths(raw: object, manifest_path: str) -> list[str]:
    if not isinstance(raw, list) or not raw:
        raise PlanningBatchIntegrityError(
            f"planning batch manifest {manifest_path!r} must list at least one stub"
        )
    paths: list[str] = []
    for entry in raw:
        value = entry.get("path") if isinstance(entry, dict) else entry
        if not isinstance(value, str) or not value:
            raise PlanningBatchIntegrityError(
                f"planning batch manifest {manifest_path!r} has a stub entry "
                "without a non-empty path"
            )
        paths.append(value)
    if len(paths) != len(set(paths)):
        raise PlanningBatchIntegrityError(
            f"planning batch manifest {manifest_path!r} lists a stub more than once"
        )
    return paths


def _manifest_path_list(raw: object, *, field: str, manifest_path: str) -> list[str]:
    if not isinstance(raw, list) or not all(
        isinstance(entry, str) and entry for entry in raw
    ):
        raise PlanningBatchIntegrityError(
            f"planning batch manifest {manifest_path!r} field {field!r} must be "
            "a list of non-empty paths"
        )
    values = list(raw)
    if len(values) != len(set(values)):
        raise PlanningBatchIntegrityError(
            f"planning batch manifest {manifest_path!r} field {field!r} "
            "contains duplicates"
        )
    for value in values:
        pure = PurePosixPath(value)
        if pure.is_absolute() or ".." in pure.parts or value != value.strip():
            raise PlanningBatchIntegrityError(
                f"planning batch manifest {manifest_path!r} field {field!r} "
                f"contains unsafe path {value!r}"
            )
    return values


def _load_manifest(document: SourceDocument) -> dict[str, Any]:
    try:
        data = yaml.safe_load(document.content)
    except yaml.YAMLError as error:
        raise PlanningBatchIntegrityError(
            f"planning batch manifest {document.path!r} is invalid YAML: {error}"
        ) from error
    if not isinstance(data, dict):
        raise PlanningBatchIntegrityError(
            f"planning batch manifest {document.path!r} must be a mapping"
        )
    if data.get("schema_version") != 1:
        raise PlanningBatchIntegrityError(
            f"planning batch manifest {document.path!r} must declare schema_version: 1"
        )
    return data


def _validate_manifest(
    *,
    repo_root: Path,
    document: SourceDocument,
    inbox_documents: list[SourceDocument],
    tracked_paths: set[str],
) -> set[str]:
    data = _load_manifest(document)
    base_commit = data.get("base_commit")
    if not isinstance(base_commit, str) or not _SHA_RE.fullmatch(base_commit):
        raise PlanningBatchIntegrityError(
            f"planning batch manifest {document.path!r} has invalid base_commit"
        )
    _git(repo_root, "merge-base", "--is-ancestor", base_commit, "HEAD")

    repository_files = _manifest_path_list(
        data.get("repository_files"),
        field="repository_files",
        manifest_path=document.path,
    )
    expected_files = set(repository_files)
    actual_files = {
        line
        for line in _git(
            repo_root, "diff", "--name-only", f"{base_commit}..HEAD"
        ).splitlines()
        if line
    }
    if actual_files != expected_files:
        raise PlanningBatchIntegrityError(
            f"planning batch manifest {document.path!r} does not cover the exact "
            f"committed overlay: missing={sorted(actual_files - expected_files)}, "
            f"extra={sorted(expected_files - actual_files)}"
        )
    absent = sorted(expected_files - tracked_paths)
    if absent:
        raise PlanningBatchIntegrityError(
            f"planning batch manifest {document.path!r} lists paths absent at HEAD: "
            f"{absent}"
        )
    if document.path not in expected_files:
        raise PlanningBatchIntegrityError(
            f"planning batch manifest {document.path!r} does not list itself in "
            "repository_files"
        )

    manifest_stubs = _manifest_stub_paths(data.get("stubs"), document.path)
    actual_stubs = [stub.path for stub in inbox_documents]
    if manifest_stubs != actual_stubs:
        raise PlanningBatchIntegrityError(
            f"planning batch manifest {document.path!r} stub coverage/order differs "
            f"from the committed inbox: manifest={manifest_stubs}, "
            f"inbox={actual_stubs}"
        )
    if not set(actual_stubs).issubset(expected_files):
        raise PlanningBatchIntegrityError(
            f"planning batch manifest {document.path!r} repository_files omits "
            f"stub(s): {sorted(set(actual_stubs) - expected_files)}"
        )

    future_paths = _manifest_path_list(
        data.get("future_document_paths", []),
        field="future_document_paths",
        manifest_path=document.path,
    )
    for path in future_paths:
        if not _PATH_RE.fullmatch(path):
            raise PlanningBatchIntegrityError(
                f"planning batch manifest {document.path!r} has non-Markdown "
                f"future_document_path {path!r}"
            )
    return set(future_paths)


def _validate_acyclic(graph: dict[str, list[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, trail: list[str]) -> None:
        if node in visiting:
            raise PlanningBatchIntegrityError(
                "planning inbox dependency cycle: " + " -> ".join([*trail, node])
            )
        if node in visited:
            return
        visiting.add(node)
        for target in graph.get(node, []):
            visit(target, [*trail, node])
        visiting.remove(node)
        visited.add(node)

    for node in sorted(graph):
        visit(node, [])


def validate_inbox_batch_integrity(
    *,
    repo_root: Path,
    inbox_documents: list[SourceDocument],
    manifest_documents: list[SourceDocument],
    backlog: Backlog,
) -> None:
    """Validate path, identity, order, cycle and manifest invariants.

    The function is deliberately side-effect free.  Any failure occurs before
    a PlanRun is persisted or an apply confirmation is shown.
    """
    if not inbox_documents:
        if manifest_documents:
            raise PlanningBatchIntegrityError(
                "planning batch manifest is active but the committed inbox has no stubs"
            )
        return

    ordered_matches = [
        _ORDERED_STUB_RE.fullmatch(PurePosixPath(document.path).name)
        for document in inbox_documents
    ]
    has_ordered = any(match is not None for match in ordered_matches)
    if has_ordered and not all(match is not None for match in ordered_matches):
        raise PlanningBatchIntegrityError(
            "an ordered phase batch cannot mix inbox-stub-NN names with "
            "unversioned follow-up stub names"
        )
    if has_ordered and len(manifest_documents) != 1:
        raise PlanningBatchIntegrityError(
            "ordered phase stubs require exactly one committed "
            "docs/planning/inbox/planning-batch-*.yaml manifest"
        )
    if len(manifest_documents) > 1:
        raise PlanningBatchIntegrityError(
            "the planning inbox contains multiple active planning-batch manifests"
        )

    tracked_paths = {
        line
        for line in _git(repo_root, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
        if line
    }
    future_paths: set[str] = set()
    if manifest_documents:
        future_paths = _validate_manifest(
            repo_root=repo_root,
            document=manifest_documents[0],
            inbox_documents=inbox_documents,
            tracked_paths=tracked_paths,
        )

    names = [PurePosixPath(document.path).name for document in inbox_documents]
    index_by_name = {name: index for index, name in enumerate(names)}
    if len(index_by_name) != len(names):  # defensive: one directory cannot do this
        raise PlanningBatchIntegrityError("planning inbox has duplicate stub basenames")

    if has_ordered:
        numbers = [
            int(match.group(1)) for match in ordered_matches if match is not None
        ]
        expected = list(range(1, len(numbers) + 1))
        if numbers != expected:
            raise PlanningBatchIntegrityError(
                f"ordered phase stubs must be contiguous from 01: got {numbers}"
            )

    existing_ticket_keys = {ticket.key for ticket in backlog.tickets}
    durable_stub_paths = {
        processed_path_for(document.path) for document in inbox_documents
    }
    graph: dict[str, list[str]] = defaultdict(list)
    front_matter = [_parse_front_matter(document) for document in inbox_documents]

    for index, (document, data) in enumerate(
        zip(inbox_documents, front_matter, strict=True)
    ):
        for field in ("relevant_docs", "documentation_requirements"):
            for path in _path_list(data, field, document.path):
                allowed = path in tracked_paths or path in durable_stub_paths
                if field == "documentation_requirements":
                    allowed = allowed or path in future_paths
                if not allowed:
                    raise StubPromotionError(
                        document.path,
                        field,
                        f"path {path!r} is not tracked at HEAD"
                        + (
                            " and is not declared in future_document_paths"
                            if field == "documentation_requirements"
                            else ""
                        ),
                    )

        for dependency in _depends_on_refs(document, data):
            if dependency.endswith(".md"):
                target_index = index_by_name.get(dependency)
                if target_index is None:
                    raise StubPromotionError(
                        document.path,
                        "depends_on",
                        f"names sibling stub {dependency!r}, which is not in this "
                        "inbox batch",
                    )
                if target_index == index:
                    raise StubPromotionError(
                        document.path,
                        "depends_on",
                        f"names the stub itself ({dependency!r})",
                    )
                graph[names[index]].append(dependency)
            else:
                if not _TICKET_KEY_RE.fullmatch(dependency):
                    raise StubPromotionError(
                        document.path,
                        "depends_on",
                        f"dependency {dependency!r} is neither an existing "
                        "ATLAS-N key nor a sibling stub filename",
                    )
                if dependency not in existing_ticket_keys:
                    raise StubPromotionError(
                        document.path,
                        "depends_on",
                        f"names existing ticket {dependency!r}, which is absent "
                        "from the current backlog",
                    )

    _validate_acyclic(graph)

    # Sibling edges must point backward in the committed deterministic order.
    # Run this after cycle detection so a cycle is reported as the stronger
    # graph defect rather than as whichever forward edge happened to be read.
    for source, targets in graph.items():
        source_index = index_by_name[source]
        for target in targets:
            if index_by_name[target] >= source_index:
                source_path = inbox_documents[source_index].path
                raise StubPromotionError(
                    source_path,
                    "depends_on",
                    f"sibling dependency must point to an earlier ordered stub: "
                    f"{target!r}",
                )

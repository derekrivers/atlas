"""ATLAS-81: the repo-owned Symphony ``WORKFLOW.md`` is the Atlas execution
contract, not Symphony's reference defaults.

These are config tests over the static ``WORKFLOW.md``: split the ``---`` front
matter, parse it with the project's YAML facility (``yaml.safe_load`` — the same
loader ``atlas.verification.rules`` uses for ``required_checks.yaml``; no new
dependency), and string-search the Jinja prompt body. Read from the WORKING TREE
(like ``tests.test_ingestion.corpus_index``) so the suite holds mid-session,
before the file is committed.

AC-1 is pinned to ``docs/atlas/symphony-integration.md`` — the "State mapping"
table and the front-matter state lists it spells out — not to a bare literal, so
renaming a state in either the doc or ``WORKFLOW.md`` breaks the tie.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / "WORKFLOW.md"
SYMPHONY_DOC = REPO_ROOT / "docs" / "atlas" / "symphony-integration.md"

_FRONT_MATTER_RE = re.compile(r"\A---\n(?P<front>.*?)\n---\n(?P<body>.*)\Z", re.DOTALL)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _split() -> tuple[dict[str, Any], str]:
    """(front-matter mapping, prompt body) for the working-tree WORKFLOW.md."""
    match = _FRONT_MATTER_RE.match(_read(WORKFLOW_PATH))
    assert match is not None, "WORKFLOW.md must open with a `---` YAML front matter"
    front = yaml.safe_load(match.group("front"))
    assert isinstance(front, dict), "front matter must parse as a YAML mapping"
    return front, match.group("body")


# --- AC-1 fixtures: the expected states, derived from the doc, not a literal ---


def _doc_table_states() -> tuple[list[str], set[str]]:
    """(active Linear states in row order, terminal Linear states) parsed from
    the "State mapping" table in symphony-integration.md.

    A table row is ``| atlas status | linear state | classification |``; active
    rows are those whose classification cell starts with "active", terminal rows
    those whose cell is exactly "terminal". Handoff/not-fetched rows are neither.
    """
    active: list[str] = []
    terminal: set[str] = set()
    for line in _read(SYMPHONY_DOC).splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 3:
            continue
        _atlas, linear_state, classification = cells
        if linear_state in ("Linear state", "") or set(linear_state) <= {"-", " "}:
            continue  # header / separator row
        if classification.startswith("active"):
            active.append(linear_state)
        elif classification == "terminal":
            terminal.add(linear_state)
    return active, terminal


def _doc_list(name: str) -> list[str]:
    """The doc's literal ``<name>: [a, b, c]`` front-matter line (the "State
    mapping" section spells out the exact lists WORKFLOW.md must carry)."""
    match = re.search(rf"{name}:\s*\[([^\]]+)\]", _read(SYMPHONY_DOC))
    assert match is not None, f"symphony-integration.md must state {name}: [...]"
    return [item.strip() for item in match.group(1).split(",")]


# --- AC-1: Atlas states, not Symphony defaults --------------------------------

SYMPHONY_DEFAULTS = {"Todo", "Merging", "Rework"}


def test_ac1_active_states_match_the_doc_table() -> None:
    front, _ = _split()
    table_active, _ = _doc_table_states()
    assert table_active == [
        "Ready for Agent",
        "In Progress",
        "PR Open",
        "Changes Requested",
    ]
    assert front["tracker"]["active_states"] == table_active
    # cross-check against the doc's spelled-out front-matter list, too
    assert front["tracker"]["active_states"] == _doc_list("active_states")


def test_ac1_terminal_states_match_the_doc() -> None:
    front, _ = _split()
    _, table_terminal = _doc_table_states()
    terminal = front["tracker"]["terminal_states"]
    assert terminal == _doc_list("terminal_states")
    assert terminal == ["Done", "Cancelled", "Canceled", "Duplicate"]
    # the table's terminal rows are a subset (Canceled/Duplicate are Linear
    # spellings the table does not enumerate but the front matter must accept)
    assert table_terminal <= set(terminal)


def test_ac1_no_symphony_default_states() -> None:
    front, _ = _split()
    assert SYMPHONY_DEFAULTS.isdisjoint(front["tracker"]["active_states"])
    assert SYMPHONY_DEFAULTS.isdisjoint(front["tracker"]["terminal_states"])


# --- AC-2: serialized concurrency ---------------------------------------------


def test_ac2_max_concurrent_agents_is_one() -> None:
    front, _ = _split()
    assert front["agent"]["max_concurrent_agents"] == 1


# --- AC-3: ADR-0007 — no agent-created tickets --------------------------------

_ISSUE_CREATING = (
    "create a separate",
    "file a separate linear issue",
    "separate backlog issue",
)


def test_ac3_follow_up_comment_not_ticket_creation() -> None:
    _, body = _split()
    lowered = body.lower()
    assert "atlas:proposed-follow-up" in body
    for phrase in _ISSUE_CREATING:
        assert phrase not in lowered, (
            f"reference issue-creation phrase leaked: {phrase!r}"
        )
    # the general "create ... issue" instruction, on a single line
    for line in lowered.splitlines():
        assert not re.search(r"create\b.*\bissue", line), (
            f"agent told to create an issue: {line!r}"
        )


# --- AC-4: no self-merge / no self-Done ---------------------------------------


def test_ac4_never_done_never_merge() -> None:
    _, body = _split()
    lowered = body.lower()
    assert "never mark your own work `done`" in lowered
    assert "never merge the pr" in lowered
    for forbidden in ("land", "gh pr merge", "move the issue to `done`"):
        assert forbidden not in lowered, (
            f"agent merge/Done action leaked: {forbidden!r}"
        )


# --- AC-5: pack reaches the agent; Atlas handoff routing ----------------------


def test_ac5_pack_injection_and_handoff_routing() -> None:
    _, body = _split()
    assert "{{ issue.description }}" in body
    assert "Review Required" in body
    assert "Needs Human" in body


# --- AC-7: workspace targets Atlas, not Symphony ------------------------------


def test_ac7_workspace_clones_atlas_with_operator_placeholder() -> None:
    front, _ = _split()
    after_create = front["hooks"]["after_create"]
    assert "derekrivers/atlas" in after_create
    assert "openai/symphony" not in after_create
    assert front["tracker"]["project_slug"] == "atlas-REPLACE_ME"


# --- canonical-contract marker (optional, per the gate addition) --------------


def test_canonical_contract_marker_present() -> None:
    match = _FRONT_MATTER_RE.match(_read(WORKFLOW_PATH))
    assert match is not None
    front_text = match.group("front").lower()
    assert "canonical" in front_text
    assert "project_slug" in front_text and "after_create" in front_text

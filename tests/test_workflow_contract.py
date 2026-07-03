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

# The preflight's own model parser (D6): AC6 pins it against the *live*
# codex.command so parser and command cannot silently drift apart.
from atlas.linear.preflight import _parse_model

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
    assert terminal == ["Done", "Canceled", "Duplicate"]
    # the table's terminal rows are a subset (Duplicate is a Linear spelling the
    # table does not enumerate but the front matter must accept)
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


def test_ac7_workspace_clones_atlas_with_operator_slug() -> None:
    front, _ = _split()
    after_create = front["hooks"]["after_create"]
    assert "derekrivers/atlas" in after_create
    assert "openai/symphony" not in after_create
    # project_slug is the operator's per-product knob; the placeholder has been
    # filled with the operator's real Linear project slug.
    assert front["tracker"]["project_slug"] == "26cc58f4bc91"


# --- F2 (ATLAS-136): the agent's inherited environment is narrowed ------------
#
# The single per-ticket WORKFLOW.md edit: the codex command must no longer
# inherit the WHOLE operator environment (which carried every Linear secret into
# the dispatched agent). AC2.1 reverts-red: restoring `inherit=all` breaks it.

_LINEAR_SECRET_NAMES = ("LINEAR_API_KEY", "LINEAR_STATE_MAP")


def test_f2_codex_inherit_is_narrowed_not_all() -> None:
    front, _ = _split()
    command = front["codex"]["command"]
    assert "shell_environment_policy.inherit=all" not in command
    assert "shell_environment_policy.inherit=core" in command


def test_f2_no_linear_secret_in_codex_command() -> None:
    # With inherit=core the secrets are dropped; this guards against a future
    # edit re-introducing one via an include_only/set clause on the command.
    front, _ = _split()
    command = front["codex"]["command"]
    for secret in _LINEAR_SECRET_NAMES:
        assert secret not in command, (
            f"Linear secret {secret!r} must not be re-introduced into the codex "
            "command env (include_only/set)"
        )


# --- canonical-contract marker (optional, per the gate addition) --------------


def test_canonical_contract_marker_present() -> None:
    match = _FRONT_MATTER_RE.match(_read(WORKFLOW_PATH))
    assert match is not None
    front_text = match.group("front").lower()
    assert "canonical" in front_text
    assert "project_slug" in front_text and "after_create" in front_text


# --- Smoke A finding T1: the codex model requirement (C6) ---------------------

_INSTALL_URL = "https://chatgpt.com/codex/install.sh"


def test_ac1_codex_model_requirement_documented_in_raw_text() -> None:
    # The requirement note lives as a YAML `#` comment adjacent to codex.command.
    # It MUST be asserted against the RAW file, not _split()'s parsed mapping:
    # yaml.safe_load strips comments, so a parsed-dict assertion would pass
    # vacuously or never find the note. The version/URL strings below appear
    # ONLY in the note (not in the command), so they evidence it directly.
    raw = _read(WORKFLOW_PATH)
    assert 'model="gpt-5.5"' in raw  # the pinned model is named
    assert "0.142.5" in raw  # the known-good Codex CLI version
    assert "0.114.0" in raw  # the snap cap that cannot run the pin
    assert _INSTALL_URL in raw  # how to obtain a working CLI


def test_ac6_pinned_model_parseable_from_live_command() -> None:
    # The live codex.command's model form must be covered by the preflight
    # parser, or C6 would silently *skip* against the real contract (parser↔
    # command drift). Pins the two together.
    front, _ = _split()
    command = front["codex"]["command"]
    assert _parse_model(command) == "gpt-5.5"


# --- Smoke A finding T2: the pack is optional, the description is the contract -
#
# The renderer (`render_definition_description`) emits a bare objective + `##`
# sections with NO `ATLAS CONTEXT PACK v1` marker today — the pack only arrives
# with ATLAS-82. The prompt must therefore not assert as *fact* that a pack is
# present; it must degrade: pack-if-present, else the definition fields are the
# contract. These pin the wording (there is no cheaper handle for a prompt
# contract than the words themselves).


def test_pack_optional_no_unconditional_assertion() -> None:
    # AC1: the unconditional "a pack is embedded" claim is gone. The full
    # assertion — not just a fragment — must be absent, so restoring the old
    # wording reverts this red.
    _, body = _split()
    assert "carries an embedded" not in body
    assert "carries an embedded Atlas context pack" not in body


def test_pack_optional_fallback_present() -> None:
    # AC2: the conditional fallback is spelled out — pack-if-present, else the
    # description's definition fields are binding. Vaguely-conditional prose
    # that never names the definition-fields fallback must not satisfy this.
    # The marker phrase spans a line wrap in the (verbatim) prompt text, so
    # collapse whitespace before searching — the words, not the wrapping, are
    # the contract.
    _, body = _split()
    flowed = " ".join(body.split())
    assert "If it contains an `ATLAS CONTEXT PACK v1`" in flowed
    assert "Otherwise" in flowed
    assert "definition fields" in flowed


def test_empty_description_blocker_unchanged() -> None:
    # AC3: the adjacent empty-description blocker branch is untouched. Pinned
    # verbatim so an edit that nudges the neighbouring Jinja block goes red.
    _, body = _split()
    blocker = (
        "{% else %}\n"
        "No description provided — treat this as a blocker (see Hard limits).\n"
        "{% endif %}"
    )
    assert blocker in body


# --- ATLAS-143: the PR-title instruction sources the embedded Atlas key --------
#
# The `In Progress` routing bullet must tell the agent to take the PR-title key
# from the `ATLAS-<n>` prefix embedded at the start of the issue title (which the
# sync now writes), NOT from Linear's `{{ issue.identifier }}` — the homonym seam
# this ticket closes. Scoped to that bullet so the retained line-1 display
# reference to `{{ issue.identifier }}` does not false-pass.


def test_pr_title_instruction_uses_embedded_atlas_key_not_identifier() -> None:
    _, body = _split()
    flowed = " ".join(body.split())
    start = flowed.index("`In Progress` — implement against the pack")
    end = flowed.index("`PR Open` — keep the PR healthy")
    bullet = flowed[start:end]
    # the PR-title source is no longer Linear's identifier ...
    assert "{{ issue.identifier }}" not in bullet
    # ... it is the Atlas key embedded at the start of the title.
    assert "ATLAS-<n>" in bullet
    assert "prefix before the first `:`" in bullet


def test_issue_identifier_survives_only_as_display_prose() -> None:
    # AC-4: `{{ issue.identifier }}` may remain for display/logging (the opening
    # "working a single Linear ticket, `{{ issue.identifier }}`" line) but nowhere
    # as the PR-title source. Exactly one occurrence remains, and it is display.
    _, body = _split()
    assert body.count("{{ issue.identifier }}") == 1
    flowed = " ".join(body.split())
    assert "working a single Linear ticket, `{{ issue.identifier }}`" in flowed


def test_pack_reword_scope_confined_to_body() -> None:
    # AC4: the reword stayed in the prompt body — the front matter parses
    # unchanged. States and the codex.command model pin are identical to base,
    # so a reword that drifted into config/routing would break here.
    front, _ = _split()
    assert front["tracker"]["active_states"] == [
        "Ready for Agent",
        "In Progress",
        "PR Open",
        "Changes Requested",
    ]
    assert front["tracker"]["terminal_states"] == ["Done", "Canceled", "Duplicate"]
    assert _parse_model(front["codex"]["command"]) == "gpt-5.5"

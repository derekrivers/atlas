"""ATLAS-136 (F1): the vendored `linear` skill teaches the name→stateId
resolution path, and the merge skill is NOT vendored.

Symphony serves the `linear_graphql` tool to every app-server session; a
repo-resident skill is what teaches its use (elixir/README.md step 4). The one
genuinely non-obvious gotcha is that Linear's `issueUpdate` takes a `stateId`
(UUID), not the display name the Atlas prompt routes by — so the skill must
teach reading the team states and moving by the resolved id. These are config
tests over the static SKILL.md, read from the WORKING TREE so they hold
mid-session before the file is committed.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_PATH = REPO_ROOT / ".codex" / "skills" / "linear" / "SKILL.md"
SKILLS_ROOT = REPO_ROOT / ".codex" / "skills"

_FRONT_MATTER_RE = re.compile(r"\A---\n(?P<front>.*?)\n---\n(?P<body>.*)\Z", re.DOTALL)


def _split() -> tuple[dict[str, object], str]:
    match = _FRONT_MATTER_RE.match(SKILL_PATH.read_text(encoding="utf-8"))
    assert match is not None, "SKILL.md must open with a `---` YAML front matter"
    front = yaml.safe_load(match.group("front"))
    assert isinstance(front, dict)
    return front, match.group("body")


# --- AC1.1: the skill exists with the right name ------------------------------


def test_ac1_1_skill_exists_named_linear() -> None:
    assert SKILL_PATH.is_file()
    front, _ = _split()
    assert front["name"] == "linear"


# --- AC1.2: it teaches the name→stateId resolution path -----------------------


def test_ac1_2_teaches_stateid_resolution_not_move_by_name() -> None:
    body = SKILL_PATH.read_text(encoding="utf-8")
    # the orchestrator-served tool
    assert "linear_graphql" in body
    # the team-states read that yields the id for a display name
    assert "IssueTeamStates" in body
    # the mutation that consumes the resolved stateId — stripping `stateId` here
    # (leaving only "move by name") is the wrong answer this pins red.
    assert re.search(
        r"issueUpdate\(\s*id:\s*\$id,\s*input:\s*\{\s*stateId:\s*\$stateId\s*\}\s*\)",
        body,
    ), "SKILL.md must teach issueUpdate with a resolved stateId, not a name"


# --- AC1.3: the merge skill is NOT vendored (hard-limit guard) ----------------


def test_ac1_3_land_skill_is_not_vendored_anywhere() -> None:
    # Vendoring `land` would arm the agent against its own contract ("never merge
    # the PR"). Anyone who later vendors the merge skill makes this red.
    assert not (SKILLS_ROOT / "land").exists()
    land_dirs = [path for path in REPO_ROOT.glob("**/skills/land") if path.is_dir()]
    assert land_dirs == [], f"a `land` skill was vendored: {land_dirs}"

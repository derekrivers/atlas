"""Repository contracts for Atlas's composable Codex skill layer.

Symphony serves the `linear_graphql` tool to every app-server session; a
repo-resident skill is what teaches its use (elixir/README.md step 4). The one
genuinely non-obvious gotcha is that Linear's `issueUpdate` takes a `stateId`
(UUID), not the display name the Atlas prompt routes by — so the skill must
teach reading the team states and moving by the resolved id. These are config
tests over static SKILL.md files, read from the WORKING TREE so they hold
mid-session before files are committed.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_PATH = REPO_ROOT / ".codex" / "skills" / "linear" / "SKILL.md"
SKILLS_ROOT = REPO_ROOT / ".codex" / "skills"
WORKFLOW_PATH = REPO_ROOT / "WORKFLOW.md"
EXECUTION_RUNBOOK_PATH = REPO_ROOT / "docs" / "runbooks" / "symphony-agent-execution.md"
AGENTS_PATH = REPO_ROOT / "AGENTS.md"

REQUIRED_SKILLS = (
    "linear",
    "atlas-investigate",
    "atlas-validation",
    "atlas-ticket-planning",
    "atlas-planning-apply",
    "atlas-ticket-execution",
    "atlas-ticket-remediation",
    "atlas-pr-review",
    "atlas-pr-acceptance",
)
ATLAS_WORKFLOW_SKILLS = REQUIRED_SKILLS[1:]

SYMPHONY_STATE_SKILL_ROUTES = (
    ("Ready for Agent", "atlas-ticket-execution"),
    ("In Progress", "atlas-ticket-execution"),
    ("PR Open", "atlas-ticket-execution"),
    ("Changes Requested", "atlas-ticket-remediation"),
)

AGENT_TASK_SKILL_ROUTES = (
    ("Current-state or repository investigation", "atlas-investigate"),
    ("Candidate validation", "atlas-validation"),
    ("Ratified design or phase decomposition", "atlas-ticket-planning"),
    ("Operator planning plan/apply", "atlas-planning-apply"),
    ("Ordinary dispatched ticket implementation", "atlas-ticket-execution"),
    ("Changes Requested remediation", "atlas-ticket-remediation"),
    ("PR semantic review", "atlas-pr-review"),
    ("PR acceptance", "atlas-pr-acceptance"),
    ("Bounded Linear operations", "linear"),
)

AUTHORITY_CONTRACT = (
    "Read the current canonical authority and follow it. This skill owns "
    "procedure and navigation, not policy. If this skill conflicts with "
    "canonical repository authority, the repository authority wins and this "
    "skill is defective."
)

REQUIRED_AUTHORITY_REFERENCES = {
    "atlas-investigate": (
        "AGENTS.md",
        "docs/MANIFEST.md",
        "docs/runbooks/operational-practice.md",
        "docs/runbooks/reviewer-session.md",
    ),
    "atlas-validation": (
        "docs/runbooks/local-development.md",
        "docs/runbooks/symphony-agent-execution.md",
        "docs/decisions/0008-ci-sourced-evidence-with-trust-tiers.md",
        "uv run atlas validation-plan",
    ),
    "atlas-ticket-planning": (
        "docs/runbooks/planning-phases-and-ticket-stubs.md",
        "docs/atlas/planning-engine-specification.md",
        "docs/decisions/0007-generative-planning-with-deterministic-reconciliation.md",
    ),
    "atlas-planning-apply": (
        "docs/runbooks/running-atlas-plan.md",
        "docs/runbooks/planning-phases-and-ticket-stubs.md",
        "docs/atlas/planning-engine-specification.md",
        "docs/decisions/0006-source-of-truth-hierarchy.md",
        "docs/decisions/0007-generative-planning-with-deterministic-reconciliation.md",
        "uv run atlas plan --stubs-only",
        "uv run atlas apply",
    ),
    "atlas-ticket-execution": (
        "WORKFLOW.md",
        "docs/runbooks/symphony-agent-execution.md",
        "docs/runbooks/operational-practice.md",
        "docs/decisions/0008-ci-sourced-evidence-with-trust-tiers.md",
    ),
    "atlas-ticket-remediation": (
        "docs/runbooks/symphony-agent-execution.md",
        "WORKFLOW.md",
        "docs/atlas/symphony-integration.md",
    ),
    "atlas-pr-review": (
        "docs/runbooks/review-doctrine.md",
        "docs/runbooks/reviewer-session.md",
        "docs/decisions/0008-ci-sourced-evidence-with-trust-tiers.md",
    ),
    "atlas-pr-acceptance": (
        "docs/runbooks/pr-acceptance.md",
        "docs/atlas/symphony-integration.md",
        "docs/decisions/0009-single-operator-governance.md",
    ),
}

EXPECTED_COMPOSITION = {
    "atlas-investigate": (),
    "atlas-validation": (),
    "atlas-ticket-planning": ("atlas-planning-apply",),
    "atlas-planning-apply": (),
    "atlas-ticket-execution": ("linear", "atlas-validation"),
    "atlas-ticket-remediation": ("linear", "atlas-validation"),
    "atlas-pr-review": ("atlas-validation",),
    "atlas-pr-acceptance": ("atlas-pr-review",),
}

_FRONT_MATTER_RE = re.compile(r"\A---\n(?P<front>.*?)\n---\n(?P<body>.*)\Z", re.DOTALL)


def _split(skill_name: str = "linear") -> tuple[dict[str, object], str]:
    skill_path = SKILLS_ROOT / skill_name / "SKILL.md"
    match = _FRONT_MATTER_RE.match(skill_path.read_text(encoding="utf-8"))
    assert match is not None, f"{skill_path} must open with a `---` YAML front matter"
    front = yaml.safe_load(match.group("front"))
    assert isinstance(front, dict), f"{skill_path} front matter must be a mapping"
    return front, match.group("body")


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _markdown_h2_section(path: Path, heading: str) -> str:
    text = path.read_text(encoding="utf-8")
    marker = f"## {heading}\n"
    assert text.count(marker) == 1, f"{path} must contain one {marker.strip()!r}"
    start = text.index(marker) + len(marker)
    end = text.find("\n## ", start)
    return text[start:] if end == -1 else text[start:end]


def _backticked_route_rows(section: str) -> tuple[tuple[str, str], ...]:
    return tuple(re.findall(r"^\| `([^`]+)` \| `([^`]+)` \|$", section, re.MULTILINE))


# --- Composable Atlas skill inventory and authority --------------------------


def test_required_skill_inventory_exists() -> None:
    for skill_name in REQUIRED_SKILLS:
        skill_dir = SKILLS_ROOT / skill_name
        assert skill_dir.is_dir(), f"missing required skill directory: {skill_dir}"
        assert (skill_dir / "SKILL.md").is_file(), (
            f"missing required skill file: {skill_dir / 'SKILL.md'}"
        )


def test_required_skills_have_exact_front_matter() -> None:
    for skill_name in REQUIRED_SKILLS:
        front, _ = _split(skill_name)
        assert set(front) == {"name", "description"}
        assert front["name"] == skill_name
        description = front["description"]
        assert isinstance(description, str)
        assert description.strip()


def test_atlas_skills_defer_to_canonical_repository_authority() -> None:
    for skill_name in ATLAS_WORKFLOW_SKILLS:
        _, body = _split(skill_name)
        assert AUTHORITY_CONTRACT in _normalized(body)


def test_atlas_skills_name_their_exact_canonical_authorities() -> None:
    for skill_name, references in REQUIRED_AUTHORITY_REFERENCES.items():
        body = (SKILLS_ROOT / skill_name / "SKILL.md").read_text(encoding="utf-8")
        for reference in references:
            assert reference in body, f"{skill_name} is missing authority {reference}"


def test_atlas_skills_declare_only_the_approved_composition_edges() -> None:
    for skill_name, targets in EXPECTED_COMPOSITION.items():
        _, body = _split(skill_name)
        referenced_skills = {
            candidate for candidate in REQUIRED_SKILLS if f"`{candidate}`" in body
        }
        assert referenced_skills == set(targets)


def test_workflow_prompt_routes_active_states_to_exact_procedural_skills() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    section = _markdown_h2_section(WORKFLOW_PATH, "Procedural skill routing")

    assert (
        text.index("## Canonical execution doctrine")
        < text.index("## Procedural skill routing")
        < text.index("## Executable lifecycle spine")
    )
    assert _backticked_route_rows(section) == SYMPHONY_STATE_SKILL_ROUTES
    assert "cannot override `WORKFLOW.md` or the\nexecution runbook" in section
    assert "`CI Pending` is not an active route and has no procedural skill" in section


def test_execution_runbook_routes_initial_and_remediation_work_exactly() -> None:
    section = _markdown_h2_section(EXECUTION_RUNBOOK_PATH, "Procedural skill routing")

    assert _backticked_route_rows(section) == SYMPHONY_STATE_SKILL_ROUTES
    assert (
        "Ordinary initial ticket execution therefore uses `atlas-ticket-execution`"
        in section
    )
    assert (
        "semantic `Changes Requested` remediation uses `atlas-ticket-remediation`"
        in section
    )
    assert "`atlas-ticket-execution` composes `linear`" in section
    assert "and `atlas-validation` after candidate freeze" in section
    assert "`atlas-ticket-remediation` composes the same two skills" in section
    assert "`CI Pending` is not an active skill route" in section


def test_agents_exposes_the_complete_repository_skill_map() -> None:
    section = _markdown_h2_section(AGENTS_PATH, "Repository Codex skills")
    rows = tuple(
        (task.replace("`", ""), skill)
        for task, skill in re.findall(
            r"^\| ([^|]+?) \| `([^`]+)` \|$", section, re.MULTILINE
        )
    )

    assert rows == AGENT_TASK_SKILL_ROUTES
    assert "procedural adapters beneath the\ncanonical documents" in section


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

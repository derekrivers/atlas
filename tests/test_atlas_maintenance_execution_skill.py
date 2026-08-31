"""Repository contracts for governed hand-dispatched Codex maintenance."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CODEX_CONFIG = REPO_ROOT / ".codex" / "config.toml"
AGENTS_ROOT = REPO_ROOT / ".codex" / "agents"
SKILL_PATH = (
    REPO_ROOT / ".codex" / "skills" / "atlas-maintenance-execution" / "SKILL.md"
)
AGENTS_MD = REPO_ROOT / "AGENTS.md"
OPERATIONAL_PRACTICE = REPO_ROOT / "docs" / "runbooks" / "operational-practice.md"
HAND_DISPATCH_RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "agent-ticket-prompt.md"

ROLE_NAMES = (
    "atlas-explorer",
    "atlas-recovery-auditor",
    "atlas-test-strategist",
    "atlas-reviewer",
    "atlas-maintenance-worker",
)
READ_ONLY_ROLES = {
    "atlas-explorer",
    "atlas-recovery-auditor",
    "atlas-test-strategist",
    "atlas-reviewer",
}

_FRONT_MATTER_RE = re.compile(r"\A---\n(?P<front>.*?)\n---\n(?P<body>.*)\Z", re.DOTALL)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _toml(path: Path) -> dict[str, object]:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def _skill() -> tuple[dict[str, object], str]:
    match = _FRONT_MATTER_RE.match(_read(SKILL_PATH))
    assert match is not None, "maintenance skill must have YAML front matter"
    front = yaml.safe_load(match.group("front"))
    assert isinstance(front, dict)
    return front, match.group("body")


def _h2(path: Path, heading: str) -> str:
    text = _read(path)
    marker = f"## {heading}\n"
    assert text.count(marker) == 1
    start = text.index(marker) + len(marker)
    end = text.find("\n## ", start)
    return text[start:] if end == -1 else text[start:end]


def test_project_config_enables_bounded_multi_agent_without_permission_weakening() -> (
    None
):
    config = _toml(CODEX_CONFIG)

    agents = config["agents"]
    assert isinstance(agents, dict)
    assert agents["enabled"] is True
    assert agents["max_concurrent_threads_per_session"] == 4
    assert "approval_policy" not in config
    assert "sandbox_mode" not in config


def test_required_custom_agents_use_only_documented_role_fields() -> None:
    role_paths = {path.stem for path in AGENTS_ROOT.glob("*.toml")}
    assert set(ROLE_NAMES) <= role_paths

    for role_name in ROLE_NAMES:
        role = _toml(AGENTS_ROOT / f"{role_name}.toml")
        assert set(role) <= {
            "name",
            "description",
            "developer_instructions",
            "sandbox_mode",
        }
        assert role["name"] == role_name
        assert isinstance(role["description"], str) and role["description"]
        assert isinstance(role["developer_instructions"], str)
        assert role["developer_instructions"].strip()
        assert "approval_policy" not in role
        assert role.get("sandbox_mode") != "danger-full-access"


def test_specialists_default_read_only_and_worker_inherits_parent_policy() -> None:
    for role_name in sorted(READ_ONLY_ROLES):
        assert _toml(AGENTS_ROOT / f"{role_name}.toml")["sandbox_mode"] == "read-only"

    for role_name in ("atlas-recovery-auditor", "atlas-test-strategist"):
        specialist = _normalized(
            str(_toml(AGENTS_ROOT / f"{role_name}.toml")["developer_instructions"])
        )
        assert "return findings to the existing primary writer" in specialist
        assert "assigns a finding as an independent implementation unit" in specialist
        assert "start atlas-maintenance-worker as the sole writer" in specialist

    worker = _toml(AGENTS_ROOT / "atlas-maintenance-worker.toml")
    instructions = _normalized(str(worker["developer_instructions"]))
    assert "sandbox_mode" not in worker
    assert "isolated worktree" in instructions
    assert "sole writer" in instructions
    assert "inherits the parent session's sandbox and approval policy" in instructions
    assert "Never interpret ATLAS-NNNM as a canonical ticket" in instructions


def test_maintenance_skill_has_exact_identity_authorities_and_phases() -> None:
    front, body = _skill()
    flowed = _normalized(body)

    assert set(front) == {"name", "description"}
    assert front["name"] == "atlas-maintenance-execution"
    for authority in (
        "AGENTS.md",
        "docs/MANIFEST.md",
        "docs/runbooks/operational-practice.md",
        "docs/runbooks/agent-ticket-prompt.md",
    ):
        assert authority in body
    for phase in "ABCDEFG":
        assert body.count(f"## Phase {phase} ") == 1
    assert "`ATLAS-NNNM` maintenance meta-label is non-canonical" in flowed
    assert "creates no ticket YAML" in flowed
    assert "grants no Linear mutation authority" in flowed


def test_maintenance_and_symphony_execution_are_distinct() -> None:
    distinction = _normalized(_h2(SKILL_PATH, "Authorities and distinction"))
    dispatch = _normalized(
        _h2(
            HAND_DISPATCH_RUNBOOK,
            "Hand-dispatched Codex maintenance units and campaigns",
        )
    )

    assert "This is not `atlas-ticket-execution`" in distinction
    assert "canonical-ticket path dispatched by Symphony" in distinction
    assert "Do not compose `linear`" in distinction
    assert "not a canonical Atlas ticket key" in dispatch
    assert "must not create a ticket YAML, mutate Linear" in dispatch
    assert "does not grant merge or lifecycle authority" in dispatch


def test_parallelism_contract_separates_cognition_from_mutation() -> None:
    _, skill = _skill()
    flowed_skill = _normalized(skill)
    agents = _normalized(_h2(AGENTS_MD, "Codex delegation and write isolation"))
    topology = _normalized(_h2(SKILL_PATH, "Phase C — Implementation topology"))

    assert "Subagents parallelise cognition; worktrees parallelise mutation" in agents
    assert "The primary agent retains authority" in agents
    assert "waits for every requested result" in agents
    assert "one question, explicit scope and prohibited actions" in agents
    assert "One mutable checkout has one writer" in agents
    assert "isolated worktrees" in agents
    assert "Serialize units whose mutable path ownership overlaps" in agents
    assert "If two units require the same mutable path, serialize them" in topology
    assert "Optimistic merge-conflict resolution is not a concurrency model" in topology
    assert (
        "Perform no implementation edits while requested discovery lanes are active"
        in skill
    )
    assert "cannot select a project custom-agent name" in flowed_skill
    assert "never claim read-only enforcement from the role file alone" in flowed_skill
    assert "live parent permission overrides may widen a child" in flowed_skill


def test_parent_and_child_authority_limits_are_explicit() -> None:
    agents = _normalized(_h2(AGENTS_MD, "Codex delegation and write isolation"))
    discovery = _normalized(_h2(SKILL_PATH, "Phase B — Parallel discovery"))

    for prohibited in (
        "mint canonical tickets",
        "mutate Linear",
        "operate managed services",
        "mutate production databases",
        "merge PRs",
        "broaden scope",
    ):
        assert prohibited in agents
    assert "Subagents advise; they do not vote" in discovery
    assert "Resolve disagreement against canonical Atlas authority" in discovery

    implementation = _normalized(_h2(SKILL_PATH, "Phase D — Implementation"))
    assert "start `atlas-maintenance-worker` as the sole writer" in implementation
    assert "do not promote a read-only specialist in place" in implementation


def test_recovery_role_pins_tier0_retrospective_liveness_contract() -> None:
    recovery = _normalized(
        str(
            _toml(AGENTS_ROOT / "atlas-recovery-auditor.toml")["developer_instructions"]
        )
    )

    for seam in (
        "authoritative source",
        "local durable state",
        "external side effect",
        "crash immediately before and after the external write",
        "ambiguous provider outcome",
        "restart behavior",
        "retry predicate",
        "idempotency",
        "duplicate-effect prevention",
        "provider advancement while Atlas is down",
        "retrospective reconciliation",
        "starvation and fairness",
        "durable diagnosis",
        "eventual convergence",
    ):
        assert seam in recovery
    assert "fail closed on one action" in recovery
    assert "permanently fail-stopped as a system" in recovery


def test_validation_and_review_remain_owned_by_existing_atlas_skills() -> None:
    validation = _normalized(_h2(SKILL_PATH, "Phase E — Validation"))
    review = _normalized(_h2(SKILL_PATH, "Phase F — Independent review"))

    assert "`atlas-validation` as the sole validation authority" in validation
    assert "Apply `atlas-pr-review`" in review
    assert "disposable isolated reviewer checkout" in review
    assert "rather than fabricating evidence" in review

    review_skill = _normalized(
        _read(REPO_ROOT / ".codex" / "skills" / "atlas-pr-review" / "SKILL.md")
    )
    assert "frozen pre-publication maintenance candidate" in review_skill
    assert "disposable isolated checkout of the exact frozen head" in review_skill


def test_canonical_runbook_records_maintenance_publication_boundary() -> None:
    maintenance = _normalized(
        _h2(OPERATIONAL_PRACTICE, "3.1 Hand-dispatched Codex maintenance")
    )

    assert "separate operating path from the minted-ticket Symphony loop" in maintenance
    assert "One mutable checkout has one writer" in maintenance
    assert "unique labels and branches" in maintenance
    assert "disjoint owned and excluded paths" in maintenance
    assert "fetch current `origin/main` again" in maintenance
    assert "Do not create a closing relationship, mutate Linear" in maintenance

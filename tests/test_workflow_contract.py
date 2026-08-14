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

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

# The preflight's own model parser (D6): AC6 pins it against the *live*
# codex.command so parser and command cannot silently drift apart.
from atlas.linear.preflight import _parse_model
from atlas.tools.doc_linter import (
    SYMPHONY_MILESTONE_BRANCH,
    SYMPHONY_MILESTONE_LEVELS,
    SymphonyMilestoneValidation,
    check_symphony_ceiling_contract,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / "WORKFLOW.md"
SYMPHONY_DOC = REPO_ROOT / "docs" / "atlas" / "symphony-integration.md"
DELIVERY_CONTROL_DOC = REPO_ROOT / "docs" / "atlas" / "multi-agent-delivery-control.md"
OPERATOR_ENVIRONMENT_DOC = REPO_ROOT / "docs" / "runbooks" / "operator-environment.md"
PHASE_15_CLOSURE_DOC = REPO_ROOT / "docs" / "closure" / "phase-15-closure-report.md"

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


def _symphony_milestone_validation() -> SymphonyMilestoneValidation | None:
    raw_level = os.environ.get("ATLAS_SYMPHONY_MILESTONE_LEVEL")
    if raw_level is None:
        return None
    assert raw_level in {str(level) for level in SYMPHONY_MILESTONE_LEVELS}
    branch = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return SymphonyMilestoneValidation(branch=branch, level=int(raw_level))


def _phase_15_is_closed() -> bool:
    closure = _read(PHASE_15_CLOSURE_DOC) if PHASE_15_CLOSURE_DOC.is_file() else ""
    return bool(
        re.search(r"^Status:\s*CLOSED\b", closure, re.IGNORECASE | re.MULTILINE)
    )


def _expected_symphony_ceiling() -> int:
    if _phase_15_is_closed():
        return 10
    milestone = _symphony_milestone_validation()
    return milestone.level if milestone is not None else 1


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


def test_atlas_255_ci_pending_is_a_distinct_non_active_handoff() -> None:
    front, _ = _split()
    table_active, table_terminal = _doc_table_states()

    assert "CI Pending" not in table_active
    assert "CI Pending" not in table_terminal
    assert "CI Pending" not in front["tracker"]["active_states"]
    assert "CI Pending" not in front["tracker"]["terminal_states"]
    assert "CI Pending is intentionally absent" in _read(WORKFLOW_PATH)


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


def test_ac2_max_concurrent_agents_matches_ruling() -> None:
    """AC-2 originally pinned serialized concurrency at 1. ATLAS-041M
    raised it to 3 by operator ruling; ATLAS-054M restores serialized
    execution and lowers the per-run turn cap to 10. ATLAS-252 permits the
    concurrency pin to move only to exactly ten with its CLOSED Phase 15
    report on ordinary main. Its explicit milestone context validates only the
    exact dedicated branch and declared level; those values remain red in the
    ordinary context.
    """
    front, _ = _split()
    ceiling = front["agent"]["max_concurrent_agents"]
    assert ceiling == _expected_symphony_ceiling()
    assert ceiling <= 10
    assert front["agent"]["max_turns"] == 10


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
    assert "project_slug" not in front["tracker"]
    assert front["tracker"]["provider"]["project_slug"] == "26cc58f4bc91"


# --- ATLAS-168: mainline freshness before PRs, pushes, and handoff ------------


def _integration_section() -> str:
    _, body = _split()
    flowed = " ".join(body.split())
    start = flowed.index("## Integration discipline")
    end = flowed.index("## How to move the ticket")
    return flowed[start:end]


def test_atlas_168_hooks_fetch_current_main_and_full_clone() -> None:
    front, _ = _split()
    hooks = front["hooks"]
    before_run = hooks["before_run"]
    before_run_lines = [
        line.strip() for line in before_run.splitlines() if line.strip()
    ]
    assert before_run_lines[0] == "git fetch origin main"
    assert "git clone https://github.com/derekrivers/atlas ." in hooks["after_create"]
    assert "--depth" not in hooks["after_create"]
    assert "https://github.com/derekrivers/atlas" not in before_run


def test_atlas_171_before_run_probe_runs_after_origin_main_fetch() -> None:
    front, _ = _split()
    before_run = front["hooks"]["before_run"]
    assert before_run.index("git fetch origin main") < before_run.index(
        "git push --dry-run origin"
    )
    assert "GIT_TERMINAL_PROMPT=0 git push --dry-run origin" in before_run
    assert 'probe_ref="refs/heads/atlas-write-access-probe-${head_short}"' in before_run
    assert '"HEAD:${probe_ref}"' in before_run
    assert "git rev-parse --short=12 HEAD" in before_run


def test_atlas_171_before_run_failure_message_names_credential_boundary() -> None:
    front, _ = _split()
    before_run = front["hooks"]["before_run"]
    assert "GitHub write-access probe failed for ${repo_for_message}" in before_run
    assert 'cat "${probe_output}" >&2' in before_run
    assert before_run.index('cat "${probe_output}" >&2') < before_run.index(
        'rm -f "${probe_output}"'
    )
    assert "shell_environment_policy.inherit=core" in before_run
    assert "operator's exported GITHUB_TOKEN is not visible here" in before_run
    assert "The most likely cause is" in before_run
    assert "on-disk GitHub credential lacks write access" in before_run
    assert (
        "non-mutating GitHub write-access probe failed before agent work began"
        in before_run
    )
    assert "shell_environment_policy.set" not in before_run


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )


def test_atlas_171_git_push_dry_run_fixture_creates_no_remote_ref(
    tmp_path: Path,
) -> None:
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    probe_ref = "refs/heads/atlas-write-access-probe-fixture"

    _git(["init", "--bare", str(remote)], cwd=tmp_path)
    _git(["init", str(work)], cwd=tmp_path)
    _git(["config", "user.email", "atlas@example.invalid"], cwd=work)
    _git(["config", "user.name", "Atlas Test"], cwd=work)
    (work / "README.md").write_text("probe fixture\n", encoding="utf-8")
    _git(["add", "README.md"], cwd=work)
    _git(["commit", "-m", "probe fixture"], cwd=work)
    _git(["remote", "add", "origin", str(remote)], cwd=work)

    _git(["push", "--dry-run", "origin", f"HEAD:{probe_ref}"], cwd=work)

    refs = _git(["for-each-ref", "--format=%(refname)", probe_ref], cwd=remote)
    assert refs.stdout == ""


def test_atlas_168_contract_rebases_before_pr_push_and_handoff() -> None:
    section = _integration_section()
    assert "before opening the PR" in section
    assert "before every push" in section
    assert "before moving to `Review Required`" in section
    assert "git fetch origin main && git rebase origin/main" in section
    assert "context pack's scope" in section
    assert "PR description" in section
    assert "outside that scope" in section
    assert "`Needs Human`" in section


def test_atlas_168_contract_pins_adr0008_ordering() -> None:
    section = _integration_section()
    assert "ADR-0008" in section
    assert "rebase precedes push precedes CI" in section
    assert "system-tier evidence pins to a head" in section
    assert "After entering `Review Required`, never rebase on your own" in section
    assert "Phase 12" in section
    assert "mechanical staleness" in section
    assert "leaves the ticket in `Review Required`" in section
    assert "`Changes Requested`" in section
    assert "semantic remediation" in section
    assert "falls behind" in section
    assert "acceptance chain restarts at the new exact head" in section


def test_atlas_168_symphony_doc_records_design_rationale() -> None:
    doc = _read(SYMPHONY_DOC)
    assert "### Mainline freshness discipline" in doc
    assert "hooks.before_run" in doc
    assert "git fetch origin main && git rebase origin/main" in doc
    assert "depth-1 clone can lack the merge base" in doc
    assert "Phase 8 closure report §5" in doc
    assert "#188 conflict class" in doc
    assert "GitHub merge queue" in doc
    assert "deferred from v1" in doc


def test_atlas_171_symphony_doc_records_probe_failure_path() -> None:
    doc = _read(SYMPHONY_DOC)
    flowed = " ".join(doc.split())
    assert "### GitHub write-access probe" in doc
    assert "GitHub write-access probe failed for <repo>" in doc
    assert "<git push --dry-run stderr/stdout>" in doc
    assert "the Git output above is the evidence for the exact failure" in doc
    assert "shell_environment_policy.inherit=core" in doc
    assert "operator's exported GITHUB_TOKEN is not visible here" in doc
    assert "Symphony aborts the attempt before any implementation work starts" in flowed
    assert "ATLAS-102 on 2026-07-16" in doc
    assert "commit `286dc9a`" in doc
    assert "failed handoff left work only in the workspace" in doc


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
    assert 'model="gpt-5.6-sol"' in raw  # the pinned model is named
    assert "0.142.5" in raw  # the known-good Codex CLI version
    assert "0.114.0" in raw  # the snap cap that cannot run the pin
    assert _INSTALL_URL in raw  # how to obtain a working CLI


def test_ac6_pinned_model_parseable_from_live_command() -> None:
    # The live codex.command's model form must be covered by the preflight
    # parser, or C6 would silently *skip* against the real contract (parser↔
    # command drift). Pins the two together.
    front, _ = _split()
    command = front["codex"]["command"]
    assert _parse_model(command) == "gpt-5.6-sol"


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
    assert _parse_model(front["codex"]["command"]) == "gpt-5.6-sol"


# --- ATLAS-252: governed Symphony ceiling and controlled ramp -----------------


def _ceiling_runbook() -> str:
    document = _read(OPERATOR_ENVIRONMENT_DOC)
    heading = "## Symphony ceiling controlled-ramp runbook"
    assert heading in document
    return document[document.index(heading) :]


def test_atlas_252_ac1_one_operator_ceiling_is_distinct_from_budgets_and_slots() -> (
    None
):
    front, _ = _split()
    workflow = _read(WORKFLOW_PATH)
    symphony = " ".join(_read(SYMPHONY_DOC).split())
    delivery = " ".join(_read(DELIVERY_CONTROL_DOC).split())

    assert front["agent"]["max_concurrent_agents"] == _expected_symphony_ceiling()
    assert front["agent"]["max_turns"] == 10
    assert "single controlling Symphony worker" in workflow
    assert "The operator is the sole owner of this value" in workflow
    assert "It is not a second ceiling" in symphony
    assert "Actual occupied slots" in symphony
    assert (
        "Historical migration `0025` and policy revision one remain immutable"
        in symphony
    )
    assert "There is one operator-owned Symphony ceiling" in delivery
    assert "actual occupied slots are observed Symphony sessions" in delivery
    assert "Revision one is immutable historical bootstrap data" in delivery


def test_atlas_252_ac2_runbook_pins_branch_edit_window_receipt_and_gate_order() -> None:
    runbook = _ceiling_runbook()
    assert "phase-15-atlas-253-ceiling-ramp" in runbook
    assert "max_concurrent_agents: <next-level>" in runbook
    assert "`1 -> 3`, `3 -> 5`, `5 -> 7`, then `7 -> 10`" in runbook
    assert "one fixed 60-minute window" in runbook
    assert "atlas:symphony-ceiling-gate v1" in runbook
    for field in (
        "origin_main_sha:",
        "merge_base_sha:",
        "head_sha:",
        "workflow_blob_sha:",
        "max_turns: 10",
        "policy_revision:",
        "pm_sync_receipt_ids:",
        "symphony_session_ids_start_peak_end:",
        "acceptance_session_ids:",
        "outcome:",
        "retained_or_restored_level:",
    ):
        assert field in runbook

    headings = [
        "### Gate 1 — serialized baseline admission, pause and rework",
        "### Gate 3 — first controlled increase and review pressure",
        "### Gate 5 — stable review and stale-write protection",
        "### Gate 7 — lanes, recovery and acceptance capacity",
        "### Gate 10 — maximum, not target, and closure",
    ]
    assert [runbook.index(heading) for heading in headings] == sorted(
        runbook.index(heading) for heading in headings
    )


def test_atlas_252_ac3_higher_levels_require_preceding_exact_evidence() -> None:
    runbook = _ceiling_runbook()
    flowed = " ".join(runbook.split())
    assert "Gate 3 cannot begin without the Gate 1 PASS receipt" in flowed
    assert "serialized baseline admission, pause and rework" in flowed
    assert "Gate 5 cannot begin without the Gate 3 PASS receipt" in flowed
    assert "first controlled increase and review pressure" in flowed
    assert "Gate 7 cannot begin without the Gate 5 PASS receipt" in flowed
    assert "stable-review and stale-write evidence" in flowed
    assert (
        "Gate 10 cannot begin without the Gate 7 PASS receipt, Phase 14 closure"
        in flowed
    )
    assert "at least three distinct acceptance sessions" in flowed
    assert "exact-head completions are at least" in flowed


def test_atlas_252_ac4_failure_rolls_back_without_terminating_or_closing() -> None:
    runbook = _ceiling_runbook()
    stop = runbook[runbook.index("### Stop, rollback and non-closure") :]
    flowed = " ".join(stop.split())
    assert "back to the last proven value" in flowed
    assert "posts the FAIL receipt with the rollback commit" in flowed
    assert "do not terminate sessions" in flowed
    assert "cancel workers or delete workspaces" in flowed
    assert "milestone PR stays unmerged" in flowed
    assert "Phase 15 remains open" in flowed
    assert "If `origin/main` advances" in flowed
    assert "restart at Gate 1" in flowed
    assert "no prior PASS carries across the rebase" in flowed


def test_atlas_252_ac5_open_phase_is_one_and_closure_can_only_be_exactly_ten() -> None:
    front, _ = _split()
    milestone = _symphony_milestone_validation()
    assert front["agent"]["max_concurrent_agents"] == _expected_symphony_ceiling()
    assert front["agent"]["max_turns"] == 10
    ceiling_findings = check_symphony_ceiling_contract(
        REPO_ROOT,
        milestone=milestone,
    )
    assert ceiling_findings == []


def test_atlas_252_ac6_runbook_exposes_no_atlas_or_agent_mutation_path() -> None:
    runbook = _ceiling_runbook()
    flowed = " ".join(runbook.split())
    assert (
        "The ramp adds no endpoint, CLI, agent action or automation that edits "
        "delivery policy" in flowed
    )
    assert "existing governed Phase 15 policy-revision boundary" in flowed
    assert (
        "No Atlas endpoint, CLI, agent or automation may edit `WORKFLOW.md`, "
        "Symphony configuration, acceptance evidence or milestone receipts" in flowed
    )
    assert not re.search(
        r"(?im)^\s*(?:GET|POST|PUT|PATCH|DELETE)\s+/|/api/|linear_graphql",
        runbook,
    )
    assert "never starts a live worker from CI" in flowed


def test_atlas_252_ac7_reconciles_current_policy_without_rewriting_history() -> None:
    runbook = " ".join(_ceiling_runbook().split())
    delivery = " ".join(_read(DELIVERY_CONTROL_DOC).split())

    assert "Migration `0025` and policy revision one" in runbook
    assert "remain immutable history" in runbook
    assert "must not be cited as the current live policy" in runbook
    assert "approved_symphony_ceiling=1" in runbook
    assert "working_budget=1" in runbook
    assert "Before any Phase 15 milestone activity" in delivery
    assert "move the active pointer to that revision" in delivery


def test_atlas_252_ac8_intermediate_values_are_milestone_branch_only() -> None:
    runbook = " ".join(_ceiling_runbook().split())

    assert "Values 3, 5 and 7 are valid only on that branch" in runbook
    assert "never independently mergeable to `main`" in runbook
    assert "ordinary committed `main` remains at one" in runbook


def test_atlas_252_ac9_milestone_validation_is_explicit_and_branch_pinned() -> None:
    runbook = " ".join(_ceiling_runbook().split())

    assert SYMPHONY_MILESTONE_BRANCH in runbook
    assert "--symphony-milestone-level <1|3|5|7|10>" in runbook
    assert "ATLAS_SYMPHONY_MILESTONE_LEVEL=<1|3|5|7|10>" in runbook
    assert "Ordinary CI omits this context" in runbook
    assert (
        "milestone validation is preflight evidence, never merge authority" in runbook
    )

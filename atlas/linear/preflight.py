"""Operator preflight for the Symphony integration (ATLAS-136; F3 + A1 + A2).

A read-only check the operator runs *before* dispatching agents, to catch the
silent no-dispatch traps the Phase-8 spec-conformance review surfaced: a Linear
workflow-state name that drifted from the WORKFLOW.md contract (so Symphony's
case-sensitive poll quietly matches nothing), a status map that no longer
coheres with the team's states (team-scoped since ATLAS-148 — a foreign team's
same-named state can no longer satisfy a check), an ambiguous transition
target, a project
slug still on its placeholder or misaligned with ``LINEAR_PROJECT_ID``, and an
accidentally-set ``LINEAR_ASSIGNEE`` that narrows the poll.

Design (ATLAS-136):

- **D1 — returns data, never raises.** :func:`run_preflight` returns a
  :class:`PreflightReport` of ordered :class:`Finding` records; the CLI renders
  them and sets the exit code. ``LinearStatusMap.validate_against_states``
  *does* raise; this module **catches** :class:`LinearStatusMapError` and folds
  it into a finding rather than letting it propagate. (Transport failures from
  the injected client are not a validation concern and propagate to the CLI,
  which maps them to a clean precondition exit.)
- **D2 — spine.** Lives in ``atlas.linear``, consuming ``ownership`` and
  ``client`` (same layer) and ``atlas.core`` (below). No spine change.
- **D3 — F3 name match is case-SENSITIVE.** Symphony feeds ``active_states``
  straight into Linear's ``state: {name: {in: ...}}`` filter with no
  lowercasing, and Linear's ``in`` is case-sensitive. So C1 requires exact-cased
  name equality and reports a case-only near-miss as its own distinct finding
  (the silent poll trap).
- **D4 — uniqueness for every written status.** C3 proves every transition
  *target* the agent or PM engine writes resolves via ``state_id_for`` to
  exactly one state — including the handoff states that never appear in
  WORKFLOW.md's state lists.
- **D6 — front-matter parsing.** Match the two fence lines (a line that is
  exactly ``---``); never naively ``split("---")``.

Known limitation (v1): C2 folds ``validate_against_states``, which raises on the
*first* map mismatch, so a run surfaces only the first such error. Enumerating
all map errors in one pass is deferred; re-run after each fix.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml

from atlas.core.models.ticket import TicketStatus
from atlas.linear.client import LinearClient, WorkflowState
from atlas.linear.ownership import LinearStatusMap, LinearStatusMapError

ASSIGNEE_ENV = "LINEAR_ASSIGNEE"

# The placeholder marker the canonical WORKFLOW.md ships with; a project slug
# still carrying it means the operator has not pointed the contract at a real
# Linear project (so C4 must fail loudly rather than treat it as configured).
_PROJECT_SLUG_PLACEHOLDER = "REPLACE_ME"

# D4: every status the agent or PM engine *writes* must resolve to exactly one
# Linear state. Includes the two handoff states (`Review Required`,
# `Needs Human`) that never appear in WORKFLOW.md's active/terminal lists.
_WRITTEN_STATUSES: tuple[TicketStatus, ...] = (
    TicketStatus.READY_FOR_AGENT,
    TicketStatus.IN_PROGRESS,
    TicketStatus.PR_OPEN,
    TicketStatus.CI_PENDING,
    TicketStatus.REVIEW_REQUIRED,
    TicketStatus.CHANGES_REQUESTED,
    TicketStatus.NEEDS_HUMAN_DECISION,
    TicketStatus.DONE,
)

# Contract handoff names the prompt body transitions *into* but that appear in
# neither `active_states` nor `terminal_states`; C1 must still require each as
# an exact-cased Linear state, or the agent stalls silently at handoff.
_HANDOFF_STATE_NAMES: tuple[str, ...] = (
    "CI Pending",
    "Review Required",
    "Needs Human",
)

# C6 (model reachability). The probe answer must be *computed*, never a token
# the prompt already contains: a codex banner / prompt-echo / verbose replay in
# stdout must not be able to fake a pass - only a genuine model response can.
# So we ask for an arithmetic result the prompt does not state and match on it.
# (Putting a literal success token in the prompt and grepping for it would
# reintroduce the exact "empty turn that looks clean" failure C6 exists to
# catch.) The `ATLAS_PREFLIGHT_OK` brand lives in the finding message, never the
# probe.
_PROBE_PROMPT = "Reply with only the result of 6 * 7, and nothing else."
_PROBE_EXPECTED = "42"
_MODEL_PROBE_TIMEOUT_DEFAULT = 60.0

# D6: extract the pinned model from `codex.command`, tolerating every authoring
# form (double-quoted, single-quoted, bare) so a differently-quoted command
# does not silently never probe. The real command is `--config 'model="gpt-5.5"'`
# - double-quoted inside single quotes - which the first alternative captures.
_MODEL_RE = re.compile(r"""model\s*=\s*(?:"([^"]+)"|'([^']+)'|([^\s"']+))""")


class PreflightError(ValueError):
    """The WORKFLOW.md front matter could not be read or parsed.

    Internal to this module: :func:`run_preflight` catches it and folds it into
    a failing finding (D1), so it never escapes to the caller."""


@dataclass(frozen=True)
class Finding:
    """One ordered preflight result: a stable ``check_id``, an ``ok`` flag, a
    ``skipped`` flag, and a human-readable ``message``.

    Three outcomes (C6 is the only check that can be *skipped*; C1-C5 are always
    a clean pass/fail):

    - **pass** - ``ok=True, skipped=False``
    - **fail** - ``ok=False, skipped=False`` (drives ``report.ok`` False)
    - **skip** - ``ok=True, skipped=True`` (the check could not run - e.g. no
      ``codex`` binary, unauthenticated, timeout; does *not* fail ``report.ok``
      but is surfaced and flagged, and the CLI maps it to EXIT_PRECONDITION)."""

    check_id: str
    ok: bool
    message: str
    skipped: bool = False


@dataclass(frozen=True)
class PreflightReport:
    """The ordered findings of one preflight run."""

    findings: tuple[Finding, ...]

    @property
    def ok(self) -> bool:
        """True iff every finding passed (the all-clear). A *skip* has
        ``ok=True``, so a run whose only non-pass is a skip is still ``ok``."""
        return all(finding.ok for finding in self.findings)

    @property
    def skipped(self) -> bool:
        """True iff any finding was skipped (a check that could not run)."""
        return any(finding.skipped for finding in self.findings)


def _extract_front_matter(text: str) -> str:
    """Return the YAML block between the first two fence lines (D6).

    Matches a line that is *exactly* ``---`` (the leading YAML-comment banner
    inside the front matter is ignored by the YAML parser, never by a naive
    ``split``). Raises :class:`PreflightError` if two fences are not found."""

    lines = text.splitlines()
    fences = [index for index, line in enumerate(lines) if line == "---"]
    if len(fences) < 2:
        raise PreflightError(
            "WORKFLOW.md has no `---` front-matter fence pair to parse"
        )
    start, end = fences[0], fences[1]
    return "\n".join(lines[start + 1 : end])


def _load_front_matter(workflow_md_path: Path) -> dict[str, object]:
    """Read and parse WORKFLOW.md's front matter into a mapping (D6)."""

    try:
        text = workflow_md_path.read_text(encoding="utf-8")
    except OSError as error:
        raise PreflightError(f"cannot read {workflow_md_path}: {error}") from error
    try:
        parsed = yaml.safe_load(_extract_front_matter(text))
    except yaml.YAMLError as error:
        raise PreflightError(
            f"{workflow_md_path} front matter is not valid YAML: {error}"
        ) from error
    if not isinstance(parsed, dict):
        raise PreflightError(
            f"{workflow_md_path} front matter did not parse to a mapping"
        )
    return parsed


def _contract_state_names(front_matter: dict[str, object]) -> list[str]:
    """The names C1 requires: ``active_states`` and ``terminal_states`` plus the
    two handoff names, de-duplicated in first-seen order."""

    tracker = front_matter.get("tracker")
    tracker = tracker if isinstance(tracker, dict) else {}
    names: list[str] = []
    for key in ("active_states", "terminal_states"):
        value = tracker.get(key)
        if isinstance(value, list):
            names.extend(str(item) for item in value)
    names.extend(_HANDOFF_STATE_NAMES)
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def _check_state_names(
    front_matter: dict[str, object], states: Iterable[WorkflowState]
) -> list[Finding]:
    """C1 (F3): every contract state name (active and terminal lists plus the
    two handoff names) exists as an exact-cased Linear state.

    A name that matches only case-insensitively is a distinct *case-drift*
    finding naming the Linear spelling found (the silent poll trap, D3); a name
    absent entirely is a missing-state finding. One finding per required name so
    every gap is reported, not just the first."""

    state_names = [state.name for state in states]
    exact = set(state_names)
    by_lower: dict[str, list[str]] = {}
    for name in state_names:
        by_lower.setdefault(name.lower(), []).append(name)

    findings: list[Finding] = []
    for required in _contract_state_names(front_matter):
        if required in exact:
            findings.append(
                Finding("C1", True, f"contract state {required!r} exists in Linear")
            )
        elif required.lower() in by_lower:
            found = sorted(set(by_lower[required.lower()]))
            findings.append(
                Finding(
                    "C1",
                    False,
                    f"contract state {required!r} has no exact-cased Linear state; "
                    f"a case-only near-miss exists as {found} — Symphony's poll is "
                    "case-sensitive and will silently match nothing",
                )
            )
        else:
            findings.append(
                Finding(
                    "C1",
                    False,
                    f"contract state {required!r} does not exist among the Linear "
                    "workflow states",
                )
            )
    return findings


def _check_map_coherence(
    status_map: LinearStatusMap, states: Iterable[WorkflowState]
) -> Finding:
    """C2: fold ``validate_against_states`` into a finding (D1). Surfaces only
    the first map error per run (known v1 limitation; see module docstring)."""

    try:
        status_map.validate_against_states(states)
    except LinearStatusMapError as error:
        return Finding("C2", False, f"status map is incoherent: {error}")
    return Finding("C2", True, "status map coheres with the Linear workflow states")


def _check_unique_targets(status_map: LinearStatusMap) -> list[Finding]:
    """C3 (D4): every written status resolves to exactly one Linear state.
    Zero or ambiguous → a failing finding (``state_id_for`` raises; caught)."""

    findings: list[Finding] = []
    for status in _WRITTEN_STATUSES:
        try:
            state_id = status_map.state_id_for(status)
        except LinearStatusMapError as error:
            findings.append(Finding("C3", False, str(error)))
        else:
            findings.append(
                Finding(
                    "C3",
                    True,
                    f"{status.value!r} resolves to a unique state ({state_id})",
                )
            )
    return findings


def _check_project(
    front_matter: dict[str, object], client: LinearClient, project_id: str
) -> Finding:
    """C4 (A2): the project slug is real (not the placeholder) and the
    ``LINEAR_PROJECT_ID`` UUID resolves to a project whose ``slug_id`` matches
    it. A UUID that resolves to no project is a failing finding, never a
    crash."""

    tracker = front_matter.get("tracker")
    tracker = tracker if isinstance(tracker, dict) else {}
    provider = tracker.get("provider")
    provider = provider if isinstance(provider, dict) else {}
    project_slug = provider.get("project_slug")
    if not isinstance(project_slug, str) or not project_slug:
        return Finding(
            "C4",
            False,
            "WORKFLOW.md tracker.provider.project_slug is missing or empty",
        )
    if _PROJECT_SLUG_PLACEHOLDER in project_slug:
        return Finding(
            "C4",
            False,
            f"tracker.provider.project_slug {project_slug!r} is still the "
            "placeholder; "
            "point the contract at a real Linear project slug",
        )
    project = client.fetch_project(project_id)
    if project is None:
        return Finding(
            "C4",
            False,
            f"LINEAR_PROJECT_ID {project_id!r} does not resolve to a Linear project",
        )
    if project.slug_id != project_slug:
        return Finding(
            "C4",
            False,
            f"LINEAR_PROJECT_ID resolves to slug {project.slug_id!r}, which does "
            "not match WORKFLOW.md tracker.provider.project_slug "
            f"{project_slug!r}; issues Atlas creates would not be visible to "
            "Symphony's poll",
        )
    return Finding(
        "C4",
        True,
        f"LINEAR_PROJECT_ID resolves to slug {project.slug_id!r}, matching the "
        "contract",
    )


def _check_assignee(allow_assignee: bool) -> Finding:
    """C5 (A1): ``LINEAR_ASSIGNEE`` is unset, unless ``--allow-assignee`` was
    passed (then a pass-with-note). A silently-set assignee narrows Symphony's
    poll and is the easiest no-dispatch trap to miss."""

    assignee = os.environ.get(ASSIGNEE_ENV, "").strip()
    if not assignee:
        return Finding("C5", True, f"{ASSIGNEE_ENV} is unset")
    if allow_assignee:
        return Finding(
            "C5",
            True,
            f"{ASSIGNEE_ENV} is set to {assignee!r}; allowed by --allow-assignee",
        )
    return Finding(
        "C5",
        False,
        f"{ASSIGNEE_ENV} is set to {assignee!r}; it narrows Symphony's poll and "
        "would silently scope out dispatch — unset it, or pass --allow-assignee "
        "to acknowledge it",
    )


@dataclass(frozen=True)
class ProbeResult:
    """The raw outcome of one model probe, *before* scoring.

    There is deliberately **no** ``ok`` field: reject-vs-pass is decided by the
    pure classifier in :func:`_check_model` from ``output``/``error``, so the
    echo defence (a prompt-echo must not score as a pass) is falsifiable in
    tests with a fake probe - a runner-supplied verdict would let it go
    untested.

    - ``output`` - the model's stdout (what it actually returned).
    - ``error`` - a non-empty runner error (nonzero exit / an ``ERROR:`` line /
      transport), surfaced *verbatim* on reject; ``None`` when the process
      completed cleanly.
    - ``timed_out`` / ``binary_missing`` / ``auth_missing`` - the three
      couldn't-run conditions that each map to a *skip* (EXIT_PRECONDITION)."""

    output: str = ""
    error: str | None = None
    timed_out: bool = False
    binary_missing: bool = False
    auth_missing: bool = False


# A model probe takes the pinned model name and returns a raw ProbeResult. The
# real runner shells out (D5); tests inject a fake and never touch subprocess.
ModelProbe = Callable[[str], ProbeResult]


def _codex_command(front_matter: dict[str, object]) -> str | None:
    """The ``codex.command`` string from the front matter, or ``None`` if the
    ``codex`` block or its ``command`` is missing (-> C6 skips as unparseable)."""

    codex = front_matter.get("codex")
    codex = codex if isinstance(codex, dict) else {}
    command = codex.get("command")
    return command if isinstance(command, str) and command else None


def _parse_model(command: str) -> str | None:
    """Extract the pinned model from a ``codex.command`` string (D6), tolerating
    double-quoted, single-quoted, and bare forms. ``None`` if no ``model=``
    clause is present (-> C6 skips rather than hard-failing)."""

    match = _MODEL_RE.search(command)
    if match is None:
        return None
    return next((group for group in match.groups() if group), None)


def _real_model_probe(model: str, *, timeout: float) -> ProbeResult:
    """The default probe (D5): check auth, then invoke the pinned model
    non-interactively and capture its output. Never raises - every failure mode
    becomes a :class:`ProbeResult` field. Not unit-tested (tests inject a fake);
    exercised by the operator's live ``--check-model`` run."""

    # Auth first: an unauthenticated Codex is a couldn't-run *skip* (issue 2),
    # decided before the billable probe so it is never mis-scored as a rejection.
    try:
        auth = subprocess.run(
            ["codex", "login", "status"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return ProbeResult(binary_missing=True)
    except subprocess.TimeoutExpired:
        return ProbeResult(timed_out=True)
    if auth.returncode != 0:
        return ProbeResult(auth_missing=True)

    try:
        proc = subprocess.run(
            [
                "codex",
                "exec",
                "--skip-git-repo-check",
                "--config",
                f'model="{model}"',
                _PROBE_PROMPT,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return ProbeResult(binary_missing=True)
    except subprocess.TimeoutExpired:
        return ProbeResult(timed_out=True)

    stdout = proc.stdout or ""
    combined = (stdout + (proc.stderr or "")).strip()
    error: str | None = None
    if proc.returncode != 0 or "ERROR:" in combined:
        error = combined or f"codex exec exited {proc.returncode}"
    return ProbeResult(output=stdout, error=error)


def _check_model(front_matter: dict[str, object], model_probe: ModelProbe) -> Finding:
    """C6 (model reachability): probe the pinned model and classify the outcome.

    Classification precedence is **normative** and stops at the first match - an
    implementer who checked the ``42`` token first would score a timeout or an
    auth failure as a rejection:

    1. model unparseable from ``codex.command`` -> *skip* (never probed);
    2. ``binary_missing`` -> *skip*;
    3. ``auth_missing`` -> *skip* (unauthenticated is couldn't-run, not a
       rejection - the most likely first-run state);
    4. ``timed_out`` -> *skip* (transient; must not read as "model rejected");
    5. **reject** - a non-empty ``error`` (nonzero exit / ``ERROR:`` line), *or*
       the expected ``42`` absent from ``output``. The reject signal is checked
       *before* the token, so a response carrying both an error and a
       coincidental ``42`` scores as reject. The runner's raw text is surfaced
       verbatim so the operator can tell "upgrade CLI" from "request
       entitlement" from "wrong model name";
    6. otherwise (``42`` present, no error) -> **pass** (ATLAS_PREFLIGHT_OK)."""

    command = _codex_command(front_matter)
    model = _parse_model(command) if command else None
    if model is None:
        return Finding(
            "C6",
            True,
            "codex.command has no parseable model=… clause; model reachability "
            "not verified",
            skipped=True,
        )

    result = model_probe(model)
    if result.binary_missing:
        return Finding(
            "C6",
            True,
            "codex binary not found; model reachability not verified",
            skipped=True,
        )
    if result.auth_missing:
        return Finding(
            "C6",
            True,
            "Codex is not authenticated (run `codex login`); model reachability "
            "not verified",
            skipped=True,
        )
    if result.timed_out:
        return Finding(
            "C6",
            True,
            "model probe timed out; reachability not verified",
            skipped=True,
        )
    if result.error:
        return Finding(
            "C6",
            False,
            f"pinned model {model!r} rejected the probe: {result.error}",
        )
    if _PROBE_EXPECTED not in result.output:
        return Finding(
            "C6",
            False,
            f"pinned model {model!r} rejected the probe: {result.output!r}",
        )
    return Finding(
        "C6",
        True,
        f"pinned model {model!r} is reachable (ATLAS_PREFLIGHT_OK)",
    )


def run_preflight(
    *,
    workflow_md_path: Path,
    client: LinearClient,
    status_map: LinearStatusMap,
    team_id: str,
    project_id: str,
    allow_assignee: bool,
    check_model: bool = False,
    model_probe: ModelProbe | None = None,
    model_probe_timeout: float = _MODEL_PROBE_TIMEOUT_DEFAULT,
) -> PreflightReport:
    """Run the operator preflight and return an ordered :class:`PreflightReport`.

    Never raises for a validation outcome (D1): a malformed contract, an
    incoherent map, an ambiguous target, an unresolved project, or a set
    assignee each become a *finding*. The findings are ordered C1→C5, then C6
    when ``check_model`` is set.

    C6 (D3) is **opt-in** and runs *last*: C1-C5 stay offline and fast, and C6
    only then makes a live, billable, auth-requiring model call. When
    ``check_model`` is set and no ``model_probe`` is injected, the real
    subprocess runner is constructed lazily with ``model_probe_timeout``. The
    CLI wrapper renders the findings and sets the exit code."""

    findings: list[Finding] = []
    try:
        front_matter = _load_front_matter(workflow_md_path)
    except PreflightError as error:
        # Front matter underpins C1 and C4; fold the parse failure into one
        # finding and skip the checks that need it, still running the map and
        # assignee checks that do not.
        findings.append(Finding("workflow-md", False, str(error)))
        front_matter = None

    # Team-scoped (ATLAS-148): only the configured team's states, so a foreign
    # team's same-named state can neither satisfy C1 nor collide in C2.
    states = client.fetch_workflow_states(team_id)

    if front_matter is not None:
        findings.extend(_check_state_names(front_matter, states))
    findings.append(_check_map_coherence(status_map, states))
    findings.extend(_check_unique_targets(status_map))
    if front_matter is not None:
        findings.append(_check_project(front_matter, client, project_id))
    findings.append(_check_assignee(allow_assignee))

    if check_model:
        # C6 runs last (D3). With no front matter there is no `codex.command` to
        # parse, so the probe cannot run -> skip, consistent with _parse_model's
        # unparseable branch.
        if front_matter is None:
            findings.append(
                Finding(
                    "C6",
                    True,
                    "WORKFLOW.md front matter did not parse; model reachability "
                    "not verified",
                    skipped=True,
                )
            )
        elif model_probe is not None:
            findings.append(_check_model(front_matter, model_probe))
        else:

            def real_probe(model: str) -> ProbeResult:
                return _real_model_probe(model, timeout=model_probe_timeout)

            findings.append(_check_model(front_matter, real_probe))

    return PreflightReport(findings=tuple(findings))

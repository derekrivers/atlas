#!/usr/bin/env python
"""ATLAS-141: CI gate — every PR title must resolve at least one real ticket key.
ATLAS-160: meta-label discipline — a reused ``ATLAS-00xM`` label fails the gate.

PRs #137-#139 reached main with no key or the literal ``(ATLAS-NN)`` placeholder
in their titles, so the verification mapper (which requires digits) could not map
them to tickets — a provenance hole in the phase whose milestone is the closed
evidence chain. The ``(ATLAS-NN)`` convention lived only in prose; this makes it
a gate.

The gate IS the mapper (D1): :func:`evaluate_title` delegates to
``atlas.verification.reports.parse_close_set`` and carries NO real-key regex of
its own, so the check and the mapper can never disagree about what a ticket key
is. It is title-only (D2): ``parse_close_set`` is called with ``body=None``,
because the PR title is the day-one convention and the mapper's primary source;
the body ``Closes ATLAS-NN`` path stays future-proofing and does not satisfy the
gate.

Invalidity is DATA, not an exception (D3): :func:`evaluate_title` and
:func:`evaluate_meta` never raise and return verdict dataclasses; only
:func:`main` maps verdicts to exit codes (0 pass, 1 fail, 2 usage). This script
sits outside the ``atlas`` import spine, so it is unconstrained by the layer
contract.

Meta labels are NON-keys (D7, ATLAS-160): the ``ATLAS-00xM`` form tags records
PRs (closure reports, runbook notes, stub landings) that deliberately close no
ticket. D1 governs real keys only, so the meta pattern lives HERE, not in the
mapper — the mapper's job is to never mistake the M-form for a key (its trailing
boundary, same ticket), and this script's job is to police the meta namespace:
a label already present in merged history fails naming the colliding PR and the
next free label; a fresh label passes; any label-less failure suggests the next
free label. History arrives ONLY via the explicit ``--history`` flag (a file of
merged squash subjects, one per line); with no flag the script behaves exactly
as it did before ATLAS-160 — no subprocess, no network, deterministic under
test (D8).

Usage::

    python scripts/check_pr_title.py "feat(at8): thing (ATLAS-142)"
    git log --format=%s origin/main > merged-titles.txt
    python scripts/check_pr_title.py "docs: record (ATLAS-017M)" \\
        --history merged-titles.txt
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from atlas.verification.reports import parse_close_set

# The failure message names the convention explicitly (AC6): a real ticket
# NUMBER, in the PR TITLE, not the (ATLAS-NN) placeholder.
_CONVENTION = (
    "PR title must carry a real ticket number like (ATLAS-142), "
    "not the (ATLAS-NN) placeholder."
)

# An ATLAS-00xM meta label: digits + M, not followed by another letter or digit.
# Case-insensitive (ATLAS-003m is real merged history) and compared NUMERICALLY,
# so padding never splits the namespace: atlas-3m, ATLAS-03M, and ATLAS-003M are
# the same label (D7).
_META_RE = re.compile(r"ATLAS-(\d+)M(?![A-Za-z0-9])", re.IGNORECASE)

# Legacy divergence floor (operator-ratified at the ATLAS-160 gate): PR titles
# burned ATLAS-000M..ATLAS-016M, but single-commit squash merges took the commit
# message as the subject, so main's subject history carries only 002M/003m/004M.
# A subject-only scan would therefore re-suggest (or re-admit) title-burned
# numbers. Enumerated from merged PR titles on 2026-07-12 — highest usage
# ATLAS-016M on PR #171; 007M exists only as a debt-register designation.
# Labels numerically below this floor are rejected as burned even when absent
# from subject history. Becomes a fossil once the repository's squash setting
# defaults to the PR title and subjects converge with titles.
_META_FLOOR = 17


def _format_meta(number: int) -> str:
    """Canonical meta-label form: three-digit zero-padded, uppercase M."""
    return f"ATLAS-{number:03d}M"


def _suggestion(next_free: str) -> str:
    """The next-free hint appended to meta and label-less failures (AC3)."""
    return (
        "If this is a keyless records/stub-landing PR, use the next free "
        f"meta label: ({next_free})."
    )


@dataclass(frozen=True)
class TitleVerdict:
    """The outcome of validating one PR title (pure data — never raised).

    Attributes:
        ok: whether the title resolves at least one real ticket key.
        keys: the resolved keys, canonical uppercase in mapper order (empty
            when ``ok`` is False).
        reason: a human-readable explanation for the CLI to print.
    """

    ok: bool
    keys: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class MetaVerdict:
    """The outcome of the meta-label check (pure data — never raised; D7).

    Attributes:
        state: ``"absent"`` (no meta label in the title), ``"fresh"`` (an
            unused label at or above the floor), ``"collision"`` (the label
            appears in merged subject history), or ``"burned"`` (numerically
            below ``_META_FLOOR`` — title-burned legacy range).
        label: the canonical form of the title's meta label (``None`` when
            absent).
        colliding_ref: the merged PR reference (``#N``) whose subject carries
            the label, or the colliding subject itself when it has no trailing
            ``(#N)``; ``None`` unless ``state == "collision"``.
        next_free: the next free label, ``max(subject max + 1, _META_FLOOR)``,
            canonical form.
        reason: a human-readable explanation for the CLI to print.
    """

    state: str
    label: str | None
    colliding_ref: str | None
    next_free: str
    reason: str


def evaluate_title(title: str) -> TitleVerdict:
    """Validate a PR title by delegating to the verification mapper (D1/D2).

    Passes iff ``parse_close_set(title, None)`` resolves at least one key. Title
    only — the body argument is fixed at ``None`` so body ``Closes`` matches
    cannot satisfy the gate. Never raises, for any input.
    """
    keys = parse_close_set(title, None)
    if keys:
        return TitleVerdict(ok=True, keys=keys, reason=f"resolves {', '.join(keys)}")
    return TitleVerdict(ok=False, keys=(), reason=_CONVENTION)


def evaluate_meta(
    title: str, merged_titles: list[str], *, floor: int = _META_FLOOR
) -> MetaVerdict:
    """Police the meta-label namespace against merged subject history (D7).

    Scans ``merged_titles`` (squash subjects, one per line of ``git log
    --format=%s``) for used meta numbers; keyless subjects contribute nothing.
    A title label that appears in that history is a COLLISION (names the
    colliding PR); one numerically below ``floor`` is BURNED (the legacy
    title/subject divergence); otherwise FRESH. ``next_free`` is
    ``max(subject max + 1, floor)`` in every state, so label-less failures can
    carry the suggestion too. Never raises, for any input.
    """
    used: dict[int, str] = {}
    for subject in merged_titles:
        for match in _META_RE.finditer(subject or ""):
            used.setdefault(int(match.group(1)), subject)
    next_free = _format_meta(max(max(used, default=-1) + 1, floor))

    labels = [int(match.group(1)) for match in _META_RE.finditer(title or "")]
    if not labels:
        return MetaVerdict(
            state="absent",
            label=None,
            colliding_ref=None,
            next_free=next_free,
            reason="no meta label in title",
        )
    for number in labels:
        if number in used:
            subject = used[number]
            ref_match = re.search(r"\(#(\d+)\)\s*$", subject)
            ref = f"#{ref_match.group(1)}" if ref_match else subject
            label = _format_meta(number)
            return MetaVerdict(
                state="collision",
                label=label,
                colliding_ref=ref,
                next_free=next_free,
                reason=(
                    f"meta label {label} is already used by merged PR {ref}; "
                    f"the next free meta label is ({next_free})."
                ),
            )
    for number in labels:
        if number < floor:
            label = _format_meta(number)
            return MetaVerdict(
                state="burned",
                label=label,
                colliding_ref=None,
                next_free=next_free,
                reason=(
                    f"meta label {label} is below the burned-label floor "
                    f"{_format_meta(floor)} (PR titles used 000M-016M but "
                    "single-commit squashes dropped them from subject history; "
                    "see the debt register's ATLAS-004M entry); "
                    f"the next free meta label is ({next_free})."
                ),
            )
    label = _format_meta(labels[0])
    return MetaVerdict(
        state="fresh",
        label=label,
        colliding_ref=None,
        next_free=next_free,
        reason=f"meta label {label} is unused in merged history (records-PR form)",
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: map verdicts to an exit code (D3).

    Returns 0 when the title resolves a key or carries a fresh meta label, 1 on
    any gate failure, and 2 for a usage error (no title argument, an
    empty/whitespace one, or an unusable ``--history`` flag). Without
    ``--history`` the behaviour is exactly the pre-ATLAS-160 gate (D8).
    """
    args = sys.argv[1:] if argv is None else argv

    positional: list[str] = []
    history_path: str | None = None
    index = 0
    while index < len(args):
        if args[index] == "--history":
            if index + 1 >= len(args):
                print("--history requires a file path.", file=sys.stderr)
                return 2
            history_path = args[index + 1]
            index += 2
        else:
            positional.append(args[index])
            index += 1

    if not positional or not positional[0].strip():
        print(
            f"usage: check_pr_title.py <pr-title> [--history <subjects-file>]\n"
            f"{_CONVENTION}",
            file=sys.stderr,
        )
        return 2
    title = positional[0]

    merged_titles: list[str] | None = None
    if history_path is not None:
        try:
            merged_titles = Path(history_path).read_text(encoding="utf-8").splitlines()
        except OSError as error:
            print(f"cannot read --history file: {error}", file=sys.stderr)
            return 2

    if merged_titles is not None:
        meta = evaluate_meta(title, merged_titles)
        if meta.state in ("collision", "burned"):
            print(meta.reason, file=sys.stderr)
            return 1
        if meta.state == "fresh":
            print(f"PR title OK — {meta.reason}")
            return 0
        # absent: the real-key path, with the next-free hint on failure (AC3).
        verdict = evaluate_title(title)
        if verdict.ok:
            print(f"PR title OK — {verdict.reason}")
            return 0
        print(f"{verdict.reason} {_suggestion(meta.next_free)}", file=sys.stderr)
        return 1

    verdict = evaluate_title(title)
    if verdict.ok:
        print(f"PR title OK — {verdict.reason}")
        return 0
    print(verdict.reason, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

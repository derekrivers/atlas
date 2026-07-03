#!/usr/bin/env python
"""ATLAS-141: CI gate — every PR title must resolve at least one real ticket key.

PRs #137-#139 reached main with no key or the literal ``(ATLAS-NN)`` placeholder
in their titles, so the verification mapper (which requires digits) could not map
them to tickets — a provenance hole in the phase whose milestone is the closed
evidence chain. The ``(ATLAS-NN)`` convention lived only in prose; this makes it
a gate.

The gate IS the mapper (D1): :func:`evaluate_title` delegates to
``atlas.verification.reports.parse_close_set`` and carries NO regex of its own,
so the check and the mapper can never disagree about what a ticket key is. It is
title-only (D2): ``parse_close_set`` is called with ``body=None``, because the
PR title is the day-one convention and the mapper's primary source; the body
``Closes ATLAS-NN`` path stays future-proofing and does not satisfy the gate.

Invalidity is DATA, not an exception (D3): :func:`evaluate_title` never raises
and returns a :class:`TitleVerdict`; only :func:`main` maps the verdict to exit
codes (0 pass, 1 fail, 2 usage). This script sits outside the ``atlas`` import
spine, so it is unconstrained by the layer contract.

Usage::

    python scripts/check_pr_title.py "feat(at8): thing (ATLAS-142)"
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from atlas.verification.reports import parse_close_set

# The failure message names the convention explicitly (AC6): a real ticket
# NUMBER, in the PR TITLE, not the (ATLAS-NN) placeholder.
_CONVENTION = (
    "PR title must carry a real ticket number like (ATLAS-142), "
    "not the (ATLAS-NN) placeholder."
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


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: map a title to an exit code (D3).

    Returns 0 when the title resolves a key, 1 when it does not, and 2 for a
    usage error (no title argument, or an empty/whitespace one).
    """
    args = sys.argv[1:] if argv is None else argv
    if not args or not args[0].strip():
        print(
            f"usage: check_pr_title.py <pr-title>\n{_CONVENTION}",
            file=sys.stderr,
        )
        return 2
    verdict = evaluate_title(args[0])
    if verdict.ok:
        print(f"PR title OK — {verdict.reason}")
        return 0
    print(verdict.reason, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

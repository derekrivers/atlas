"""Pure helpers for the ``atlas verify`` CLI (ATLAS-80), per
docs/atlas/verification-engine.md "Reports".

Two pure, unit-testable helpers the verify command composes — the impure
handler that calls GitHub and storage lives in ``atlas.cli`` (this module never
reaches up into github/storage/cli):

- :func:`parse_close_set` resolves WHICH tickets a PR closes (OP-C). Atlas's
  day-one convention is the ``(ATLAS-NN)`` key in the PR TITLE (e.g.
  ``feat(at7): … (ATLAS-77) (#126)``), so the title is the primary source; the
  GitHub closing-keyword grammar over the body is unioned in as harmless
  future-proofing. The ``ATLAS-`` prefix is the disambiguator that keeps the
  ``(#126)`` PR number from being captured as a ticket key.
- :func:`verification_checks_for` maps a pure :class:`PRVerification` into the
  append-only :class:`VerificationCheck` rows the handler persists (OP-B), with
  the id and clock injected so the mapping is deterministic under test.

PURE and layer-faithful: this module imports ``atlas.core`` and its
``atlas.verification`` siblings ONLY — never ``atlas.github``, ``atlas.storage``,
or ``atlas.cli``. Following the evaluators it builds on, invalidity and absence
are DATA, not exceptions: both helpers NEVER raise, for any input (a ``None``
title/body, an empty :class:`PRVerification`, included).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from atlas.core.enums import EvidenceStatus
from atlas.core.models import VerificationCheck
from atlas.verification.pr_completion import PRVerification

# A ticket key: the ``ATLAS-`` prefix (case-insensitive) plus a run of digits,
# NOT followed by a letter. The prefix is the disambiguator — a bare ``#126``
# (the PR number) carries no ``ATLAS-`` prefix and so is never captured as a
# ticket key (R1). The trailing lookahead keeps an ``ATLAS-00xM`` meta label
# (the non-key tag for records PRs; scripts/check_pr_title.py owns that form)
# from misparsing as a real key — ``ATLAS-004M`` is not ``ATLAS-004``, and not
# ``ATLAS-00`` either: the lookahead also forbids a digit, which is reachable
# only by backtracking out of a letter-blocked match (ATLAS-160).
_KEY = r"ATLAS-(\d+)(?![A-Za-z0-9])"

# Bare-key matches anywhere in the PR TITLE — the Atlas day-one convention is
# the ``(ATLAS-NN)`` suffix, but we match the key wherever it appears so a
# reworded title still resolves. Case-insensitive; normalised to uppercase.
_TITLE_KEY_RE = re.compile(_KEY, re.IGNORECASE)

# Body matches require a GitHub closing keyword before the key, so a bare
# mention (``## ATLAS-77 — …`` in a description heading) does NOT close a
# ticket — only an explicit ``Closes ATLAS-77`` does. Harmless future-proofing
# against a "Closes" convention; the title path is what works on day one.
_CLOSING_KEYWORDS = (
    "close",
    "closes",
    "closed",
    "fix",
    "fixes",
    "fixed",
    "resolve",
    "resolves",
    "resolved",
)
_BODY_CLOSE_RE = re.compile(
    rf"\b(?:{'|'.join(_CLOSING_KEYWORDS)})\b\s+{_KEY}",
    re.IGNORECASE,
)

# The statuses at which an evaluation has reached a terminal outcome, so the
# persisted row's ``completed_at`` is stamped (verification-engine.md). A
# PENDING/WARNING check is still open — it stays ``completed_at=None`` (D2).
_TERMINAL_STATUSES = frozenset(
    {
        EvidenceStatus.PASSED,
        EvidenceStatus.FAILED,
        EvidenceStatus.NOT_APPLICABLE,
    }
)


def parse_close_set(title: str | None, body: str | None) -> tuple[str, ...]:
    """Resolve the canonical ticket keys a PR closes (OP-C / R1; never raises).

    Reads the PR TITLE for bare ``ATLAS-NN`` keys (the Atlas day-one
    convention — the ``(ATLAS-NN)`` suffix) and the PR BODY for
    closing-keyword + ``ATLAS-NN`` matches (future-proofing), unions them, and
    returns the keys normalised to canonical uppercase ``ATLAS-NN``, deduped and
    order-preserving (title matches first in title order, then body matches not
    already seen). A ``None`` or empty title/body contributes nothing. The
    ``ATLAS-`` prefix is the disambiguator: a ``(#126)`` PR number is never
    captured as a ticket key.

    Args:
        title: the PR title (``pull_request["title"]``); the primary source.
        body: the PR body (``pull_request["body"]``); closing-keyword matches
            only. Either may be ``None``.
    """
    keys: list[str] = []
    seen: set[str] = set()

    def _add(number: str) -> None:
        key = f"ATLAS-{number}"
        if key not in seen:
            seen.add(key)
            keys.append(key)

    for match in _TITLE_KEY_RE.finditer(title or ""):
        _add(match.group(1))
    for match in _BODY_CLOSE_RE.finditer(body or ""):
        _add(match.group(1))

    return tuple(keys)


def verification_checks_for(
    pr: PRVerification,
    *,
    now: datetime,
    new_id: Callable[[], UUID],
) -> list[VerificationCheck]:
    """Map a :class:`PRVerification` to the rows the handler persists (OP-B/D2).

    One :class:`VerificationCheck` per :class:`CheckOutcome` of every
    :class:`TicketVerification`, in the order the verdict lays them out (tickets
    by key, checks in resolver order). The id (``new_id()``) and clock (``now``)
    are injected so the mapping is deterministic under test. ``summary`` carries
    the outcome's ``reason``; ``evidence_ids`` is the outcome's tuple as a list;
    ``completed_at`` is ``now`` for a TERMINAL outcome (PASSED/FAILED/
    NOT_APPLICABLE) and ``None`` for an open one (PENDING/WARNING). Pure — it
    touches no storage and never raises.

    Args:
        pr: the composed PR verdict (``evaluate_pr`` output).
        now: the run's creation time (the handler captures one
            ``datetime.now(UTC)`` and threads it here).
        new_id: a fresh-UUID factory (``uuid4`` in production; a deterministic
            sequence under test).
    """
    return [
        VerificationCheck(
            id=new_id(),
            ticket_id=ticket.ticket_id,
            check_type=outcome.check_type,
            status=outcome.status,
            summary=outcome.reason,
            required=outcome.required,
            evidence_ids=list(outcome.evidence_ids),
            created_at=now,
            completed_at=now if outcome.status in _TERMINAL_STATUSES else None,
        )
        for ticket in pr.tickets
        for outcome in ticket.checks
    ]

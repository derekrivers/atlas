"""The one natural-key sort primitive (ATLAS-113).

A single key-ordering function shared by every renderer, so the natural
sort that puts ATLAS-2 before ATLAS-10 has exactly one implementation.
This consolidates and replaces ``yaml_io._natural_key`` and
``planning.mermaid._natural`` (the two divergent copies the Phase 3
closure flagged).

The grammar is deliberately the LOOSE form ``^([A-Za-z-]+?)-?(\\d+)$``:
epic keys are ``ATLAS-E<n>`` (``EPIC_PREFIX = "ATLAS-E"``), which the
strict ``^([A-Za-z]+)-(\\d+)$`` does not parse — so epics must sort
naturally too (E1, E2, …, E10), not lexically (E1, E10, E2).

Not ``reconciler._natural``: that sorts composite ``DiffEntry`` identity
strings ("key", "new:<n>", "src -> tgt"), a general identity sort that is
intentionally NOT a key sort and stays where it is.
"""

from __future__ import annotations

import re

_KEY_RE = re.compile(r"^([A-Za-z-]+?)-?(\d+)$")


def natural_key(key: str) -> tuple[str, int]:
    """Sort key giving numeric-within-prefix order: ATLAS-2 before ATLAS-10,
    and ATLAS-E2 before ATLAS-E10. Non-conforming input sorts by ``(key, -1)``
    — deterministic and total, and below any conforming key in its prefix."""
    match = _KEY_RE.match(key)
    if match:
        return (match.group(1), int(match.group(2)))
    return (key, -1)

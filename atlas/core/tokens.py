"""Planning-engine §4 tokenisation, shared without relaxing its contract.

``normalise_tokens`` implements the planning-engine-specification §4
normalisation rule verbatim: casefold; every non-alphanumeric character becomes
a space; whitespace-split into a token set. ``planning.reconciler.similarity``
depends on this primitive, and its deterministic behaviour is an ADR-0007
guarantee. Any behaviour change requires an ADR plus a spec edit; a consumer
that needs different tokenisation adds its own function instead of altering
this one.
"""

from __future__ import annotations

import re

_NON_ALNUM_RE = re.compile(r"[^0-9a-z]+")


def normalise_tokens(text: str) -> frozenset[str]:
    """Casefold, replace non-alphanumeric text with spaces, then split."""

    return frozenset(_NON_ALNUM_RE.sub(" ", text.casefold()).split())

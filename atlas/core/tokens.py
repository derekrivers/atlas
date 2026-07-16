"""Shared deterministic tokenisation primitives."""

from __future__ import annotations

import re

_NON_ALNUM_RE = re.compile(r"[^0-9a-z]+")


def normalise_tokens(text: str) -> frozenset[str]:
    """Casefold, replace non-alphanumeric text with spaces, then split."""

    return frozenset(_NON_ALNUM_RE.sub(" ", text.casefold()).split())

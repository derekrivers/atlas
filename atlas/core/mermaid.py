"""The one Mermaid label-escape primitive (ATLAS-113).

A single quote-safe label escaper shared by every Mermaid renderer, so
``["..."]`` node text is quoted one way only. This consolidates and
replaces ``planning.mermaid._escape`` (moved here verbatim), and is
imported by both ``planning.mermaid`` (the canonical roadmap.mmd render)
and ``dependencies.mermaid`` (the advisory analysis lens) — breaking the
``dependencies → planning`` import edge that copy created.
"""

from __future__ import annotations


def escape_label(text: str) -> str:
    """Quote-safe label text for a Mermaid `["..."]` node."""
    return text.replace("\\", "\\\\").replace('"', "'")

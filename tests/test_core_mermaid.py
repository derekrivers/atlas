"""ATLAS-113: the one Mermaid label-escape primitive, moved verbatim from
planning.mermaid._escape — backslashes double, double quotes become single.
"""

from __future__ import annotations

from atlas.core.mermaid import escape_label


def test_double_quotes_become_single() -> None:
    assert escape_label('say "hi"') == "say 'hi'"


def test_backslashes_double() -> None:
    assert escape_label("a\\b") == "a\\\\b"


def test_plain_text_is_unchanged() -> None:
    assert escape_label("ATLAS core models") == "ATLAS core models"

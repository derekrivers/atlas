"""ATLAS-113: the one natural-key sort primitive orders keys numerically
within a shared prefix, including epic keys (ATLAS-E<n>) the strict grammar
could not parse.
"""

from __future__ import annotations

from atlas.core.keys import natural_key


def test_ticket_keys_sort_numeric_within_prefix() -> None:
    # ATLAS-2 before ATLAS-10: numeric, not lexical.
    assert natural_key("ATLAS-2") < natural_key("ATLAS-10")


def test_adr_style_key_parses() -> None:
    # ADR-0005 parses (zero-padded number collapses to the int 5).
    assert natural_key("ADR-0005") == ("ADR", 5)
    assert natural_key("ADR-0005") < natural_key("ADR-0010")


def test_epic_keys_sort_numeric_within_prefix() -> None:
    # The load-bearing case: ATLAS-E<n> must sort numerically too — the
    # strict ^([A-Za-z]+)-(\d+)$ grammar did NOT parse it, so epics used to
    # sort lexically (E1, E10, E2).
    assert natural_key("ATLAS-E2") < natural_key("ATLAS-E10")
    assert natural_key("ATLAS-E2") == ("ATLAS-E", 2)


def test_epic_ordering_guard_rejects_the_wrong_order() -> None:
    # Seeded wrong-order guard: lexical ordering would put E10 before E2.
    # Natural ordering must NOT — this pins the epic-ordering regression.
    assert not natural_key("ATLAS-E10") < natural_key("ATLAS-E2")


def test_non_conforming_input_is_total_and_deterministic() -> None:
    # A non-conforming key falls back to (key, -1): deterministic, total,
    # and below any conforming key sharing its prefix.
    assert natural_key("loose") == ("loose", -1)
    assert natural_key("loose") == natural_key("loose")
    # The -1 fallback sits below any conforming key sharing the prefix:
    # "ATLAS" -> ("ATLAS", -1) sorts before "ATLAS-1" -> ("ATLAS", 1).
    assert natural_key("ATLAS") < natural_key("ATLAS-1")

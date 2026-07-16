"""Characterisation tests for the shared planning-engine tokeniser."""

from atlas.core.tokens import normalise_tokens


def test_normalise_tokens_pins_planning_engine_section_4_contract() -> None:
    assert normalise_tokens("Straße, WORLD—foo_bar\t123\nrepeat repeat") == {
        "strasse",
        "world",
        "foo",
        "bar",
        "123",
        "repeat",
    }
    assert normalise_tokens("!!!") == frozenset()

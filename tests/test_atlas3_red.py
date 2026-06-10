"""TEMPORARY — ATLAS-3 falsifiability check; reverted in the next commit.

Deliberate failures: an unused import (F401, turns the lint job red) and
a failing test (turns the test job red). lint-types must stay green so
failures attribute to the correct jobs.
"""

import os


def test_deliberately_failing_for_atlas3_falsifiability() -> None:
    raise AssertionError("deliberate red for ATLAS-3 falsifiability check")

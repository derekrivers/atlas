"""Shared test configuration (ATLAS-19).

Hypothesis runs derandomised everywhere — local and CI — so the
milestone properties are reproducible by construction; a flaky
milestone test is worse than none. Deliberate exploration overrides
via HYPOTHESIS_PROFILE=explore.
"""

from hypothesis import settings

settings.register_profile("atlas", derandomize=True, max_examples=50, deadline=None)
settings.register_profile("explore", derandomize=False, max_examples=200)
settings.load_profile("atlas")

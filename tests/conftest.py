"""Shared test configuration (ATLAS-19).

Hypothesis runs derandomised everywhere — local and CI — so the
milestone properties are reproducible by construction; a flaky
milestone test is worse than none. Deliberate exploration overrides
via HYPOTHESIS_PROFILE=explore.
"""

import os

import pytest
from hypothesis import settings
from hypothesis.errors import InvalidArgument

settings.register_profile("atlas", derandomize=True, max_examples=50, deadline=None)
settings.register_profile("explore", derandomize=False, max_examples=200)

_requested_profile = os.environ.get("HYPOTHESIS_PROFILE")
_selected_profile = "atlas" if _requested_profile is None else _requested_profile

try:
    settings.load_profile(_selected_profile)
except InvalidArgument as error:
    raise pytest.UsageError(
        f"Unsupported HYPOTHESIS_PROFILE={_selected_profile!r}: {error}"
    ) from error

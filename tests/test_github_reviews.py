"""PR-review normaliser tests (ATLAS-65): the review-state table, the
NormalisedReview pin triple + deterministic hash + reviewer, the null-commit_id
skip, and that the recorded foreign bare-array payload normalises.

Acceptance criteria 1 and 2; the review-state table below is the seeded-defect
target (criterion 6): break one row and the matching case fails.
"""

from __future__ import annotations

import logging

import pytest
from github_fakes import FakeGitHubClient, load_array_fixture

from atlas.core.enums import EvidenceStatus
from atlas.github import (
    GitHubClient,
    NormalisedReview,
    normalise_review,
    normalise_review_state,
    normalise_reviews,
    payload_hash,
)

COMMIT = "4c64be37e1c4f0c7a6d9f0b3a2e1d0c9b8a7f6e5"


def _review(
    *,
    review_id: int = 7,
    login: str = "octocat",
    state: str = "APPROVED",
    commit_id: str | None = COMMIT,
    html_url: str | None = "https://github.com/o/r/pull/1#pullrequestreview-7",
) -> dict[str, object]:
    """A raw GitHub review payload (the shape the reviews endpoint returns)."""
    return {
        "id": review_id,
        "user": {"login": login},
        "state": state,
        "commit_id": commit_id,
        "html_url": html_url,
    }


# --- the review-state table (criterion 1, seeded-defect target #6) ----------
# Each row is (state, expected). DISMISSED is operator-confirmed NOT_APPLICABLE;
# an unknown/PENDING state is WARNING (never dropped, never a crash).
_STATE_ROWS = [
    ("APPROVED", EvidenceStatus.PASSED),
    ("CHANGES_REQUESTED", EvidenceStatus.FAILED),
    ("COMMENTED", EvidenceStatus.WARNING),
    ("DISMISSED", EvidenceStatus.NOT_APPLICABLE),
]


@pytest.mark.parametrize(("state", "expected"), _STATE_ROWS)
def test_review_state_table_maps_every_state(
    state: str, expected: EvidenceStatus
) -> None:
    assert normalise_review_state(state) == expected


def test_unknown_review_state_maps_to_warning_not_crash() -> None:
    # PENDING (a real GitHub state outside the table) and any made-up state are
    # surfaced as WARNING, never dropped or a KeyError.
    assert normalise_review_state("PENDING") == EvidenceStatus.WARNING
    assert normalise_review_state("TOTALLY_MADE_UP") == EvidenceStatus.WARNING


# --- the pin triple + reviewer + deterministic hash (criterion 2) -----------


def test_normalise_review_pins_triple_and_carries_reviewer() -> None:
    raw = _review(review_id=42, login="alice", state="CHANGES_REQUESTED")
    normalised = normalise_review(raw)
    assert normalised is not None
    # the pin triple = (commit_id, review id, payload-hash)
    assert normalised.commit_sha == COMMIT
    assert normalised.external_run_id == "42"
    assert normalised.payload_hash == payload_hash(raw)
    # reviewer is the review author, status from the state table
    assert normalised.reviewer == "alice"
    assert normalised.status == EvidenceStatus.FAILED
    assert normalised.source_uri == raw["html_url"]
    assert normalised.raw_payload == raw
    # dedup key is (external_run_id, payload_hash), like NormalisedCheck
    assert normalised.dedup_key == (normalised.external_run_id, normalised.payload_hash)
    # a review is NOT a check: it carries no name and no EvidenceType.
    assert not hasattr(normalised, "name")
    assert not hasattr(normalised, "evidence_type")
    assert "evidence_type" not in NormalisedReview.__dataclass_fields__


def test_payload_hash_is_deterministic_regardless_of_key_order() -> None:
    a = {"id": 1, "state": "APPROVED", "commit_id": COMMIT}
    b = {"commit_id": COMMIT, "state": "APPROVED", "id": 1}  # same data, reordered
    assert payload_hash(a) == payload_hash(b)


def test_external_run_id_is_stringified_review_id() -> None:
    # The review id arrives as an int; the pin is the stringified id (D1).
    normalised = normalise_review(_review(review_id=3120928905))
    assert normalised is not None
    assert normalised.external_run_id == "3120928905"


# --- the null-commit_id skip (refinement to D1(b)) --------------------------


def test_review_with_null_commit_id_is_skipped_and_warned(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A review whose commit_id is null cannot be commit-pinned, so it cannot be
    # valid system-tier evidence (ATLAS-61): skip it, never coerce to a "None"
    # pin.
    with caplog.at_level(logging.WARNING, logger="atlas.github.normaliser"):
        assert normalise_review(_review(review_id=99, commit_id=None)) is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "99" in warnings[0].getMessage()


def test_review_with_missing_commit_id_is_skipped() -> None:
    raw = {"id": 5, "user": {"login": "bob"}, "state": "APPROVED"}  # no commit_id
    assert normalise_review(raw) is None


def test_normalise_reviews_filters_out_unpinnable_reviews(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw = [
        _review(review_id=1, state="APPROVED"),
        _review(review_id=2, commit_id=None),  # dropped
        _review(review_id=3, state="CHANGES_REQUESTED"),
    ]
    with caplog.at_level(logging.WARNING, logger="atlas.github.normaliser"):
        normalised = normalise_reviews(raw)
    # the unpinnable one is gone; the two pinned ones survive, in order
    assert [n.external_run_id for n in normalised] == ["1", "3"]
    assert all(n.commit_sha == COMMIT for n in normalised)
    assert any("2" in r.getMessage() for r in caplog.records)


# --- against the recorded foreign bare-array payload ------------------------


def test_recorded_reviews_normalise() -> None:
    reviews = load_array_fixture("pr_reviews.json")
    normalised = normalise_reviews(reviews)
    # the recorded set (cli/cli#11499) carries APPROVED / CHANGES_REQUESTED /
    # COMMENTED, all commit-pinned, so none are dropped.
    assert len(normalised) == len(reviews)
    statuses = {n.status for n in normalised}
    assert EvidenceStatus.PASSED in statuses  # APPROVED
    assert EvidenceStatus.FAILED in statuses  # CHANGES_REQUESTED
    assert EvidenceStatus.WARNING in statuses  # COMMENTED
    assert all(n.commit_sha for n in normalised)
    assert all(n.reviewer for n in normalised)
    assert all(n.dedup_key == (n.external_run_id, n.payload_hash) for n in normalised)


def test_fake_client_satisfies_protocol_and_replays_reviews() -> None:
    client = FakeGitHubClient(pr_reviews=load_array_fixture("pr_reviews.json"))
    assert isinstance(client, GitHubClient)
    reviews = client.fetch_pr_reviews("cli", "cli", 11499)
    assert reviews
    # reviews are PR-scoped: the recorded call carries the PR number, not a SHA.
    assert client.calls[0] == ("pr_reviews", "cli", "cli", 11499)
    # end to end through the fake: raw -> normalised, no network.
    assert normalise_reviews(reviews)

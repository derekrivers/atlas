"""ATLAS-255 CI-pending state identity and single-owner lifecycle contract."""

from pathlib import Path

from atlas.core.models import (
    TicketStatus,
    TicketTransitionOwner,
    ci_pending_transition_owner,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PM_SYNC_DOC = REPO_ROOT / "docs/atlas/pm-engine-and-linear-sync.md"
CI_PENDING_LINEAR_STATE_ID = "85cdfa65-b990-41cc-a4ea-0071868ba27f"


def test_ac1_ci_pending_is_distinct_from_review_rework_and_completion() -> None:
    assert TicketStatus.CI_PENDING.value == "ci_pending"
    assert TicketStatus.CI_PENDING not in {
        TicketStatus.REVIEW_REQUIRED,
        TicketStatus.CHANGES_REQUESTED,
        TicketStatus.DONE,
    }


def test_ac1_agent_entry_requires_the_published_pr_state() -> None:
    assert (
        ci_pending_transition_owner(TicketStatus.PR_OPEN, TicketStatus.CI_PENDING)
        is TicketTransitionOwner.AGENT
    )
    for source in TicketStatus:
        if source is TicketStatus.PR_OPEN:
            continue
        assert ci_pending_transition_owner(source, TicketStatus.CI_PENDING) is None


def test_ac1_atlas_owns_every_ci_pending_exit() -> None:
    expected = {
        TicketStatus.REVIEW_REQUIRED,
        TicketStatus.CHANGES_REQUESTED,
    }
    actual = {
        target
        for target in TicketStatus
        if ci_pending_transition_owner(TicketStatus.CI_PENDING, target)
        is TicketTransitionOwner.ATLAS
    }

    assert actual == expected
    assert all(
        ci_pending_transition_owner(TicketStatus.CI_PENDING, target)
        is not TicketTransitionOwner.AGENT
        for target in TicketStatus
    )
    assert {owner.value for owner in TicketTransitionOwner} == {"agent", "atlas"}


def test_ac1_canonical_linear_mapping_is_exact_and_unique() -> None:
    contract = PM_SYNC_DOC.read_text(encoding="utf-8")
    expected = (
        f"| CI Pending (started) | `{CI_PENDING_LINEAR_STATE_ID}` | `ci_pending` |"
    )

    assert expected in contract
    assert contract.count(CI_PENDING_LINEAR_STATE_ID) == 1

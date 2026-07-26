"""ATLAS-205: sync result summaries print every counter.

Pure formatter coverage over hand-built ``SyncResult`` values only. No database,
Linear client, network, or scheduler path participates here.
"""

from __future__ import annotations

from dataclasses import fields
from typing import get_type_hints

from atlas.cli import _format_sync_result
from atlas.pm import SyncDecisionClassification, SyncResult
from atlas.pm.sync import SyncDecision


def _integer_counter_names() -> list[str]:
    type_hints = get_type_hints(SyncResult)
    return [
        field.name for field in fields(SyncResult) if type_hints.get(field.name) is int
    ]


def test_sync_result_summary_prints_every_integer_counter_name() -> None:
    rendered = _format_sync_result(SyncResult())

    for counter_name in _integer_counter_names():
        assert f"{counter_name}=0" in rendered


def test_sync_result_first_summary_line_is_byte_identical() -> None:
    result = SyncResult(
        status_pulled=3,
        status_unchanged=4,
        unmapped=5,
        anomalies_logged=6,
        pushed_created=1,
        pushed_updated=2,
        push_skipped=2,
        packs_embedded=7,
        push_decisions=[
            SyncDecision(
                phase="push",
                ticket_key="ATLAS-301",
                outcome="skipped",
                reason="status not pushable (done)",
                classification=SyncDecisionClassification.ROUTINE,
            ),
            SyncDecision(
                phase="push",
                ticket_key="ATLAS-302",
                outcome="skipped",
                reason="status not pushable (rejected)",
                classification=SyncDecisionClassification.ROUTINE,
            ),
        ],
    )

    assert _format_sync_result(result).splitlines()[0] == (
        "pm sync: completed; pushes=3 pushed_created=1 pushed_updated=2 "
        "embeds=7 status_pulls=3 status_unchanged=4 anomalies_logged=6 "
        "unmapped_observations=5 push_skipped=2 "
        "(not pushable: done=1, rejected=1)"
    )


def test_sync_result_actions_line_prints_completed_zero_and_one() -> None:
    assert "completed=0" in _format_sync_result(SyncResult()).splitlines()[1]
    assert "completed=1" in _format_sync_result(SyncResult(completed=1)).splitlines()[1]


def test_sync_result_prefixes_no_work_and_completed_only_results() -> None:
    all_zero = _format_sync_result(SyncResult()).splitlines()
    completed_only = _format_sync_result(SyncResult(completed=1)).splitlines()

    assert all_zero[0].startswith("pm sync: no work performed;")
    assert completed_only[0].startswith("pm sync: completed;")

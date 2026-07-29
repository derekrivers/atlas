"""Normaliser tests (ATLAS-62): the status table, deterministic payload
hashing + dedup key, and that real-shaped CI payloads normalise without
crashing or carrying an EvidenceType.

Acceptance criteria 1, 2, and the D1 no-EvidenceType invariant; the
seeded-defect target (criterion 7) is the status table below.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from github_fakes import FakeGitHubClient, load_fixture

from atlas.core.enums import EvidenceStatus
from atlas.github import (
    GitHubClient,
    NormalisedCheck,
    normalise_check_run,
    normalise_check_runs,
    normalise_status,
    normalise_workflow_run,
    normalise_workflow_runs,
    payload_hash,
)

HEAD_SHA = "7de6f0ec2a05242b9e87c0a16a24c68661c4dedb"


# --- the status table (evidence-pipeline.md), every row + the unknown case --
# Each row is (conclusion, expected). A completed run carries a conclusion;
# the in-progress row is conclusion=None. This is the seeded-defect target
# (criterion 7): break one mapping and the matching case fails.
_STATUS_ROWS = [
    ("success", EvidenceStatus.PASSED),
    ("failure", EvidenceStatus.FAILED),
    ("timed_out", EvidenceStatus.FAILED),
    ("cancelled", EvidenceStatus.WARNING),
    ("stale", EvidenceStatus.WARNING),
    ("skipped", EvidenceStatus.NOT_APPLICABLE),
    ("neutral", EvidenceStatus.NOT_APPLICABLE),
]


@pytest.mark.parametrize(("conclusion", "expected"), _STATUS_ROWS)
def test_status_table_maps_every_conclusion(
    conclusion: str, expected: EvidenceStatus
) -> None:
    assert normalise_status("completed", conclusion) == expected


def test_in_progress_run_is_pending() -> None:
    # No conclusion yet (queued / in_progress) -> PENDING.
    assert normalise_status("in_progress", None) == EvidenceStatus.PENDING
    assert normalise_status("queued", "") == EvidenceStatus.PENDING


def test_unknown_conclusion_maps_to_warning_not_crash() -> None:
    # An unrecognised conclusion is surfaced as WARNING, never dropped or a
    # KeyError. `action_required` is a real GitHub conclusion outside the table.
    assert normalise_status("completed", "action_required") == EvidenceStatus.WARNING
    assert normalise_status("completed", "totally_made_up") == EvidenceStatus.WARNING


# --- payload hashing + dedup key (criterion 2) ------------------------------


def test_payload_hash_is_deterministic_regardless_of_key_order() -> None:
    a = {"id": 1, "name": "Tests", "conclusion": "success"}
    b = {"conclusion": "success", "name": "Tests", "id": 1}  # same data, reordered
    assert payload_hash(a) == payload_hash(b)


def test_changed_payload_changes_hash() -> None:
    base = {"id": 1, "name": "Tests", "conclusion": "success"}
    changed = {"id": 1, "name": "Tests", "conclusion": "failure"}
    assert payload_hash(base) != payload_hash(changed)


def test_dedup_key_is_external_run_id_and_payload_hash() -> None:
    run = {"id": 42, "name": "Tests", "status": "completed", "conclusion": "success"}
    normalised = normalise_workflow_run(run, head_sha=HEAD_SHA)
    assert normalised.dedup_key == (normalised.external_run_id, normalised.payload_hash)
    assert normalised.external_run_id == "42"
    assert normalised.payload_hash == payload_hash(run)


def test_rerun_with_changed_payload_gets_a_new_dedup_key() -> None:
    first = {"id": 42, "name": "Tests", "status": "completed", "conclusion": "failure"}
    rerun = {"id": 42, "name": "Tests", "status": "completed", "conclusion": "success"}
    a = normalise_workflow_run(first, head_sha=HEAD_SHA)
    b = normalise_workflow_run(rerun, head_sha=HEAD_SHA)
    assert a.external_run_id == b.external_run_id  # same run id
    assert a.dedup_key != b.dedup_key  # but a new record (payload changed)


# --- the frozen shape carries the right fields, and NO EvidenceType ---------


def test_normalised_shape_pins_commit_and_omits_evidence_type() -> None:
    run = {
        "id": 99,
        "name": "Lint code",
        "status": "completed",
        "conclusion": "success",
        "html_url": "https://github.com/o/r/actions/runs/99",
    }
    normalised = normalise_workflow_run(run, head_sha=HEAD_SHA)
    assert normalised.name == "Lint code"
    assert normalised.status == EvidenceStatus.PASSED
    assert normalised.commit_sha == HEAD_SHA  # pinned to the polled head, D5
    assert normalised.source_uri == "https://github.com/o/r/actions/runs/99"
    assert normalised.raw_payload == run
    # The webhook-swap shape carries no EvidenceType (that mapping is ATLAS-63/64).
    assert not hasattr(normalised, "evidence_type")
    assert "evidence_type" not in NormalisedCheck.__dataclass_fields__


def test_commit_sha_is_the_polled_head_not_the_payload_field() -> None:
    # The payload's own head_sha differs from what we polled; the record pins
    # to the polled head (the exact state Atlas attested; ADR-0008).
    run = {
        "id": 7,
        "name": "Tests",
        "status": "completed",
        "conclusion": "success",
        "head_sha": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    }
    normalised = normalise_workflow_run(run, head_sha=HEAD_SHA)
    assert normalised.commit_sha == HEAD_SHA


def test_check_run_normalises_with_html_url() -> None:
    check = {
        "id": 5,
        "name": "build",
        "status": "completed",
        "conclusion": "neutral",
        "completed_at": "2026-07-29T10:11:12Z",
        "html_url": "https://github.com/o/r/runs/5",
    }
    normalised = normalise_check_run(check, head_sha=HEAD_SHA)
    assert normalised.status == EvidenceStatus.NOT_APPLICABLE
    assert normalised.external_run_id == "5"
    assert normalised.source_uri == "https://github.com/o/r/runs/5"
    assert normalised.source_event_at == datetime(2026, 7, 29, 10, 11, 12, tzinfo=UTC)


def test_workflow_source_time_prefers_updated_at() -> None:
    run = {
        "id": 99,
        "name": "test",
        "status": "completed",
        "conclusion": "success",
        "created_at": "2026-07-29T09:00:00Z",
        "run_started_at": "2026-07-29T09:01:00Z",
        "updated_at": "2026-07-29T09:02:00Z",
    }

    normalised = normalise_workflow_run(run, head_sha=HEAD_SHA)

    assert normalised.source_event_at == datetime(2026, 7, 29, 9, 2, tzinfo=UTC)


def test_malformed_source_time_fails_closed_without_crashing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    run = {
        "id": 99,
        "name": "test",
        "status": "completed",
        "conclusion": "success",
        "updated_at": "not-a-time",
    }

    normalised = normalise_workflow_run(run, head_sha=HEAD_SHA)

    assert normalised.source_event_at is None
    assert "source order unavailable" in caplog.text


# --- against recorded foreign CI payloads -----------------------------------


def test_recorded_workflow_runs_normalise() -> None:
    runs = load_fixture("workflow_runs.json", "workflow_runs")
    normalised = normalise_workflow_runs(runs, head_sha=HEAD_SHA)
    assert len(normalised) == len(runs)
    by_name = {n.name: n for n in normalised}
    # The recorded set carries success / failure / action_required(unknown).
    assert by_name["pre-commit"].status == EvidenceStatus.PASSED
    assert by_name["Tests"].status == EvidenceStatus.FAILED
    assert by_name["CodeQL"].status == EvidenceStatus.WARNING  # unknown -> WARNING
    assert all(n.commit_sha == HEAD_SHA for n in normalised)
    assert all(n.dedup_key == (n.external_run_id, n.payload_hash) for n in normalised)


def test_recorded_check_runs_normalise() -> None:
    checks = load_fixture("check_runs.json", "check_runs")
    normalised = normalise_check_runs(checks, head_sha=HEAD_SHA)
    statuses = {n.status for n in normalised}
    assert EvidenceStatus.PASSED in statuses
    assert EvidenceStatus.FAILED in statuses
    assert EvidenceStatus.NOT_APPLICABLE in statuses  # skipped


# --- the fake satisfies the protocol and replays fixtures -------------------


def test_fake_client_satisfies_protocol_and_replays_fixtures() -> None:
    client = FakeGitHubClient(
        workflow_runs=load_fixture("workflow_runs.json", "workflow_runs"),
        check_runs=load_fixture("check_runs.json", "check_runs"),
    )
    assert isinstance(client, GitHubClient)
    runs = client.fetch_workflow_runs("o", "r", HEAD_SHA)
    checks = client.fetch_check_runs("o", "r", HEAD_SHA)
    assert runs and checks
    assert client.calls[0] == ("workflow_runs", "o", "r", HEAD_SHA)
    # End to end through the fake: raw -> normalised, no network.
    assert normalise_workflow_runs(runs, head_sha=HEAD_SHA)

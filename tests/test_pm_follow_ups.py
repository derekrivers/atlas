"""Follow-up comment scan -> inbox stub (step 4, ATLAS-45).

The PRODUCER half of follow-up ingestion: per synced, non-terminal ticket, the
sync loop reads the issue's Linear comments (read-only ``fetch_comments``) and
writes one inbox stub per comment tagged ``atlas:proposed-follow-up`` to
``<inbox_dir>/<ticket-key>-<n>.md``. These tests are entirely fake-fed (the
``RecordingClient`` over the in-memory Linear, comments seeded with
``seed_comment``) — no network, no secrets. They map to the acceptance criteria:

* AC1 — a tagged comment yields exactly one stub carrying the title, the verbatim
  body, the source reference, and the comment id; an untagged comment yields none
  (:func:`test_tagged_comment_yields_one_stub_untagged_yields_none`).
* AC2 — the dedup holds across N ticks (one stub, not N), and a second distinct
  tagged comment writes a second stub at the next index
  (:func:`test_dedup_across_ticks_and_second_comment_gets_next_index`); the index
  stays monotonic across ``inbox/`` and ``inbox/processed/``
  (:func:`test_index_is_monotonic_across_processed_and_processed_dedups`).
* AC3 — the scan creates no ticket and writes no Atlas/Linear state, and nothing
  is written under the planning root outside ``inbox/``
  (:func:`test_scan_writes_no_ticket_or_linear_state_and_only_under_inbox`).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_models_validation import NOW
from test_pm_sync import RecordingClient, run, seed_ticket

from atlas.core.models.ticket import TicketStatus
from atlas.pm.sync import FOLLOW_UP_TAG
from atlas.storage import Database, TicketRepo

TAGGED_BODY = f"Split the retry path into its own ticket. {FOLLOW_UP_TAG}"
OTHER_TAGGED_BODY = f"Also extract the backoff helper. {FOLLOW_UP_TAG}"
UNTAGGED_BODY = "Looks good, merging after CI."


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def _inbox(tmp_path: Path) -> Path:
    return tmp_path / "docs" / "planning" / "inbox"


def _stub_files(inbox: Path) -> list[Path]:
    return sorted(p for p in inbox.glob("*.md")) if inbox.is_dir() else []


# --- AC1: tagged -> one stub; untagged -> none ------------------------------


def test_tagged_comment_yields_one_stub_untagged_yields_none(
    db: Database, tmp_path: Path
) -> None:
    client = RecordingClient()
    ticket = seed_ticket(db, client, key="ATLAS-200", status=TicketStatus.PLANNED)
    assert ticket.external_linear_id is not None
    comment = client.seed_comment(ticket.external_linear_id, TAGGED_BODY)
    client.seed_comment(ticket.external_linear_id, UNTAGGED_BODY)  # not a follow-up
    inbox = _inbox(tmp_path)

    result = run(db, client, inbox_dir=inbox)

    assert result.follow_ups_stubbed == 1  # the untagged comment is not stubbed
    stubs = _stub_files(inbox)
    assert [p.name for p in stubs] == ["ATLAS-200-1.md"]
    text = stubs[0].read_text(encoding="utf-8")
    # The dedup marker (the comment id), kept separate from the body, on line 1.
    assert text.splitlines()[0] == f"<!-- atlas-source-comment-id: {comment.id} -->"
    assert "# Follow-up from ATLAS-200" in text  # a title
    assert TAGGED_BODY in text  # the verbatim comment body
    assert f"Source comment: {comment.id}" in text  # the comment id, human-readable
    # An honest source reference: the ticket key and the Linear issue id (A3 — no
    # fabricated linear.app URL).
    assert f"Source issue: ATLAS-200 (Linear issue {ticket.external_linear_id})" in text


def test_untagged_only_writes_nothing(db: Database, tmp_path: Path) -> None:
    client = RecordingClient()
    ticket = seed_ticket(db, client, key="ATLAS-201", status=TicketStatus.PLANNED)
    assert ticket.external_linear_id is not None
    client.seed_comment(ticket.external_linear_id, UNTAGGED_BODY)
    inbox = _inbox(tmp_path)

    result = run(db, client, inbox_dir=inbox)

    assert result.follow_ups_stubbed == 0
    assert _stub_files(inbox) == []


# --- AC2: dedup across ticks; second comment -> next index ------------------


def test_dedup_across_ticks_and_second_comment_gets_next_index(
    db: Database, tmp_path: Path
) -> None:
    client = RecordingClient()
    ticket = seed_ticket(db, client, key="ATLAS-200", status=TicketStatus.PLANNED)
    assert ticket.external_linear_id is not None
    client.seed_comment(ticket.external_linear_id, TAGGED_BODY)
    inbox = _inbox(tmp_path)

    # The same tagged comment seen across N ticks must be stubbed exactly once
    # (the wrong answer = N stubs).
    results = [run(db, client, inbox_dir=inbox) for _ in range(5)]
    assert results[0].follow_ups_stubbed == 1  # first sight
    assert all(r.follow_ups_stubbed == 0 for r in results[1:])  # deduped thereafter
    assert [p.name for p in _stub_files(inbox)] == ["ATLAS-200-1.md"]

    # A second, distinct tagged comment writes a second stub at the next index.
    client.seed_comment(ticket.external_linear_id, OTHER_TAGGED_BODY)
    result = run(db, client, inbox_dir=inbox)
    assert result.follow_ups_stubbed == 1
    assert [p.name for p in _stub_files(inbox)] == ["ATLAS-200-1.md", "ATLAS-200-2.md"]
    second = (inbox / "ATLAS-200-2.md").read_text(encoding="utf-8")
    assert OTHER_TAGGED_BODY in second


def test_index_is_monotonic_across_processed_and_processed_dedups(
    db: Database, tmp_path: Path
) -> None:
    # Simulate ATLAS-122 having already moved the first stub to processed/: the
    # next index must skip past it (monotonic), and its comment id must still be
    # recognised as stubbed (the processed-dir dedup).
    client = RecordingClient()
    ticket = seed_ticket(db, client, key="ATLAS-200", status=TicketStatus.PLANNED)
    assert ticket.external_linear_id is not None
    processed_comment = client.seed_comment(
        ticket.external_linear_id, TAGGED_BODY, comment_id="comment-processed"
    )
    inbox = _inbox(tmp_path)
    processed = inbox / "processed"
    processed.mkdir(parents=True)
    (processed / "ATLAS-200-1.md").write_text(
        f"<!-- atlas-source-comment-id: {processed_comment.id} -->\n"
        "# Follow-up from ATLAS-200\n\nalready processed\n",
        encoding="utf-8",
    )
    # A new, distinct tagged comment arrives.
    client.seed_comment(
        ticket.external_linear_id, OTHER_TAGGED_BODY, comment_id="c-new"
    )

    result = run(db, client, inbox_dir=inbox)

    assert result.follow_ups_stubbed == 1  # only the new comment; processed one held
    # Monotonic: the new stub is -2, not a reused -1 that would collide logically.
    assert [p.name for p in _stub_files(inbox)] == ["ATLAS-200-2.md"]
    assert "c-new" in (inbox / "ATLAS-200-2.md").read_text(encoding="utf-8")


# --- AC3: no ticket / no Atlas-or-Linear state; only inbox/ written ----------


def test_scan_writes_no_ticket_or_linear_state_and_only_under_inbox(
    db: Database, tmp_path: Path
) -> None:
    client = RecordingClient()
    # Already synced (linear_synced_at == updated_at) and in a state that maps to
    # its own status, so steps 1-3 are no-ops this tick and the scan is the only
    # thing acting — isolating the scan path. No acceptance criteria, so it is not
    # promotion-ready either.
    ticket = seed_ticket(
        db,
        client,
        key="ATLAS-200",
        status=TicketStatus.PLANNED,
        linear_synced_at=NOW,
    )
    assert ticket.external_linear_id is not None
    client.seed_comment(ticket.external_linear_id, TAGGED_BODY)
    planning_root = tmp_path / "docs" / "planning"
    inbox = planning_root / "inbox"

    tickets_before = {t.key: t.status for t in TicketRepo(db).list()}
    # Clear the recorder so only the scan tick's Linear writes are observed.
    client.creates.clear()
    client.updates.clear()
    client.state_writes.clear()

    result = run(db, client, inbox_dir=inbox)

    assert result.follow_ups_stubbed == 1
    # No ticket created and no Atlas status changed on the scan path.
    tickets_after = {t.key: t.status for t in TicketRepo(db).list()}
    assert tickets_after == tickets_before
    # Nothing crossed Atlas -> Linear: the follow-up scan is a pure read
    # (fetch_comments), and steps 1-3 are no-ops for this already-synced ticket.
    assert client.creates == []
    assert client.updates == []
    assert client.state_writes == []
    # The ONLY filesystem write is under inbox/: the planning root contains
    # nothing but the inbox directory.
    assert list(planning_root.iterdir()) == [inbox]
    assert [p.name for p in _stub_files(inbox)] == ["ATLAS-200-1.md"]


def test_terminal_ticket_is_not_scanned(db: Database, tmp_path: Path) -> None:
    client = RecordingClient()
    ticket = seed_ticket(db, client, key="ATLAS-202", status=TicketStatus.DONE)
    assert ticket.external_linear_id is not None
    client.seed_comment(ticket.external_linear_id, TAGGED_BODY)
    inbox = _inbox(tmp_path)

    result = run(db, client, inbox_dir=inbox, now=NOW)

    assert result.follow_ups_stubbed == 0  # terminal work is closed; not polled
    assert _stub_files(inbox) == []


def test_unjoined_ticket_is_not_scanned(db: Database, tmp_path: Path) -> None:
    client = RecordingClient()
    # No Linear issue: nothing to read.
    seed_ticket(
        db, client, key="ATLAS-203", status=TicketStatus.PLANNED, with_issue=False
    )
    inbox = _inbox(tmp_path)

    result = run(db, client, inbox_dir=inbox)

    assert result.follow_ups_stubbed == 0
    assert _stub_files(inbox) == []

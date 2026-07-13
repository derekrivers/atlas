"""ATLAS-164: the rendered context pack rides the Linear issue description.

Emulator/fixture-driven, ATLAS_LIVE_TESTS=0 — the in-memory Linear fake and a
committed git fixture repo whose corpus the REAL ATLAS-162 collector pair
ingests (exactly the documents provider ``_build_tick_config`` injects), no
network, no secrets. Seeded failing first per B011; each seed was replaced by
its real assertion as the behaviour landed.

The named cases, mapped to the gate's ACs and rulings (PR #180):

- AC-1: a definition push embeds the rendered pack beneath the definition
  fields behind the pinned delimiter/header — for a corpus-anchored ticket AND
  a stub-minted one (anchor under ``inbox/processed/``, the ATLAS-162 class).
- AC-2: the pull over an embedded description changes no Atlas-owned field,
  and a re-push composes from the ticket, never from Linear's stored text.
- AC-3 / D-1: the size boundary — at the limit embeds untruncated; over it the
  PACK tail truncates with a visible marker; definition fields are never cut;
  the push path uses the default pinned constant (100,000 chars).
- AC-4 / D-2: a pack render failure pushes definition-only (today's exact
  payload) with one typed ``PACK_RENDER_FAILURE`` DebtItem and a stamped
  cursor (A-3), and never blocks the remaining tickets; a documents-loader
  failure degrades every embed this tick and the tick completes.
- AC-5: a push tick's request count is exactly pushes + fixed cost — rendering
  adds zero Linear calls (the ATLAS-148 no-op bound itself stays pinned
  UNCHANGED in test_pm_sync.py).
- D-3: an unchanged definition with a changed corpus does not re-push — packs
  refresh on definition change only; corpus staleness is accepted and visible
  via the header's ``rendered_at``.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from context_corpus import (
    ANCHOR,
    ANCHOR_PATH,
    BODY_PHRASE,
    CONTEXT_SPEC,
    PROCESSED_STUB,
    PROCESSED_STUB_PATH,
    STUB_ANCHOR,
    STUB_PHRASE,
    corpus_files,
)
from test_ingestion import git, make_repo
from test_models_validation import NOW, ticket_kwargs
from test_pm_sync import (
    EARLIER,
    LATER,
    PROJECT_ID,
    TEAM_ID,
    CountingClient,
    RecordingClient,
    status_map,
)

from atlas.core.anchors import SourceDocument
from atlas.core.enums import ActorType
from atlas.core.models import AnomalyType, Ticket
from atlas.core.models.context_pack import ContextPack
from atlas.core.models.ticket import TicketStatus
from atlas.linear.ownership import (
    EMBED_DESCRIPTION_LIMIT,
    PACK_HEADER_PREFIX,
    compose_embedded_description,
    render_definition_description,
    render_definition_title,
)
from atlas.planning.ingestion import (
    collect_input_documents,
    collect_processed_documents,
)
from atlas.pm import SyncResult, sync_tick
from atlas.pm.sync import CREATED_BY
from atlas.storage import Database, DebtItemRepo, TicketRepo

# The pinned delimiter/header form (symphony-integration.md "Context pack
# delivery"; gate assumption A-4): blank line, ---, one header line, newline.
# pack_id is the built pack's UUID; rendered_at its created_at isoformat.
_HEADER_RE = re.compile(
    r"\n\n---\n"
    r"ATLAS CONTEXT PACK v1 \| "
    r"pack_id: [0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12} \| "
    r"rendered_at: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00\n"
)

INBOX_DIR = Path("docs/planning/inbox")


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """The committed corpus plus one retired stub at its durable processed/
    address — both anchor classes resolvable, exactly as gate 4 sees them."""
    return make_repo(tmp_path, corpus_files() | {PROCESSED_STUB_PATH: PROCESSED_STUB})


def collector_pair(repo: Path) -> Callable[[], list[SourceDocument]]:
    """EXACTLY the documents provider ``_build_tick_config`` injects (the
    ATLAS-162 pair): the §2.1 corpus plus the committed ``processed/`` stubs,
    both from HEAD, fail-closed on a dirty tree."""

    def documents() -> list[SourceDocument]:
        return collect_input_documents(repo) + collect_processed_documents(
            repo, INBOX_DIR
        )

    return documents


def seed(
    db: Database,
    client: RecordingClient,
    *,
    key: str,
    source_anchor: str = ANCHOR,
    with_issue: bool = True,
    updated_at: datetime = NOW,
    linear_synced_at: datetime | None = None,
    **overrides: Any,
) -> Ticket:
    """Insert a pushable ``planned`` ticket whose ``source_anchor`` resolves in
    the fixture corpus (default) — no acceptance criteria, so promotion never
    fires and the request arithmetic below stays pull + pushes only. Joined to
    a fake issue in the unstarted default state (maps back to ``planned``:
    the pull is set-to-same). Recorder lists are cleared after seeding so only
    the tick's own writes are observed."""

    external_id: str | None = None
    if with_issue:
        issue = client.create_issue(
            {"title": "Linear Title", "description": "linear"},
            team_id=TEAM_ID,
            project_id=PROJECT_ID,
        )
        external_id = issue.id
    ticket = Ticket(
        **ticket_kwargs()
        | {
            "id": uuid4(),
            "key": key,
            "status": TicketStatus.PLANNED,
            "source_anchor": source_anchor,
            "external_linear_id": external_id,
            "created_at": updated_at,
            "updated_at": updated_at,
            "linear_synced_at": linear_synced_at,
        }
        | overrides
    )
    TicketRepo(db).add(ticket)
    client.creates.clear()
    client.create_scopes.clear()
    client.updates.clear()
    client.state_writes.clear()
    client.comment_scans.clear()
    return ticket


def run(
    db: Database,
    client: RecordingClient,
    documents: Callable[[], list[SourceDocument]],
    *,
    now: datetime = NOW,
) -> SyncResult:
    return sync_tick(
        tickets=TicketRepo(db),
        db=db,
        client=client,
        status_map=status_map(),
        team_id=TEAM_ID,
        project_id=PROJECT_ID,
        inbox_dir=Path(tempfile.mkdtemp()),
        documents=documents,
        now=now,
    )


def make_pack(ticket: Ticket, rendered_markdown: str) -> ContextPack:
    """A minimal pack for composer-level tests: only the fields the composer
    reads (``id``, ``created_at``, ``rendered_markdown``) matter."""

    return ContextPack(
        id=uuid4(),
        product_id=ticket.product_id,
        ticket_id=ticket.id,
        title=ticket.title,
        objective=ticket.objective,
        rendered_markdown=rendered_markdown,
        created_at=datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC),
    )


def expected_prefix(ticket: Ticket, pack: ContextPack) -> str:
    """The pinned composition prefix (A-4): definition render, blank line,
    ``---``, the header line naming the pack, newline — the markdown follows."""

    return (
        render_definition_description(ticket)
        + f"\n\n---\n{PACK_HEADER_PREFIX} | pack_id: {pack.id} | "
        + f"rendered_at: {pack.created_at.isoformat()}\n"
    )


# --- AC-1: the embed, both anchor classes -----------------------------------


def test_pushed_description_embeds_pack_for_corpus_anchored_ticket(
    db: Database, repo: Path
) -> None:
    client = RecordingClient()
    ticket = seed(db, client, key="ATLAS-900")

    result = run(db, client, collector_pair(repo))

    assert result.pushed_updated == 1
    assert result.packs_embedded == 1  # wrong answer: 0 (definition-only push)
    assert result.pack_render_failures == 0
    ((_, definition),) = client.updates
    # The single owned key set: content widened, no new key over the wire.
    assert set(definition) == {"title", "description"}
    assert definition["title"] == render_definition_title(ticket)
    description = definition["description"]
    assert isinstance(description, str)
    # Section order: the definition render comes FIRST, byte-identical to the
    # definition-only form, then the pinned delimiter/header, then the pack.
    rendered_definition = render_definition_description(ticket)
    assert description.startswith(rendered_definition)
    match = _HEADER_RE.search(description)
    assert match is not None  # the exact delimiter/header form (A-4)
    assert match.start() == len(rendered_definition)
    assert len(_HEADER_RE.findall(description)) == 1
    # Pack content rides beneath: the anchored corpus section's body phrase.
    pack_body = description[match.end() :]
    assert BODY_PHRASE in pack_body
    assert "## Objective" in pack_body


def test_pushed_description_embeds_pack_for_stub_minted_ticket(
    db: Database, repo: Path
) -> None:
    # The ATLAS-162 class, pinned live: the anchor resolves under the durable
    # inbox/processed/ address through the real collector pair.
    client = RecordingClient()
    ticket = seed(db, client, key="ATLAS-901", source_anchor=STUB_ANCHOR)

    result = run(db, client, collector_pair(repo))

    assert result.packs_embedded == 1
    assert result.pack_render_failures == 0  # wrong answer: UnknownDocumentError
    ((_, definition),) = client.updates
    description = definition["description"]
    assert isinstance(description, str)
    rendered_definition = render_definition_description(ticket)
    assert description.startswith(rendered_definition)
    match = _HEADER_RE.search(description)
    assert match is not None
    assert match.start() == len(rendered_definition)
    assert STUB_PHRASE in description[match.end() :]


# --- AC-2: pull-side safety over an embedded description --------------------


def test_pull_over_embedded_description_changes_no_atlas_owned_field(
    db: Database, repo: Path
) -> None:
    client = RecordingClient()
    seed(db, client, key="ATLAS-902")
    provider = collector_pair(repo)
    first = run(db, client, provider)  # embeds; Linear now holds the pack
    assert first.packs_embedded == 1
    before = TicketRepo(db).get_by_key("ATLAS-902")
    assert before is not None

    second = run(db, client, provider)  # the pull fixture over the embed

    # Every Atlas-owned field byte-stable: the pull reads only state_id, so
    # nothing of the embedded description can flow back (the wrong answer:
    # any stored field absorbing pack content or the cursor moving).
    after = TicketRepo(db).get_by_key("ATLAS-902")
    assert after is not None
    assert after.model_dump() == before.model_dump()
    # And the cursor held: no second push, no second render.
    assert second.pushed_updated == 0
    assert second.packs_embedded == 0
    assert len(client.updates) == 1


def test_repush_composes_from_ticket_never_from_linear(
    db: Database, repo: Path
) -> None:
    # A definition-changed RE-push (cursor EARLIER, ticket LATER): the pushed
    # description is composed from the ticket and a fresh pack — never read
    # back from Linear (structurally: the pull DTO carries no description) —
    # so exactly ONE header appears, never an accumulated second pack.
    client = RecordingClient()
    ticket = seed(
        db,
        client,
        key="ATLAS-903",
        updated_at=LATER,
        linear_synced_at=EARLIER,
    )

    result = run(db, client, collector_pair(repo))

    assert result.pushed_updated == 1
    assert result.packs_embedded == 1
    ((_, definition),) = client.updates
    description = definition["description"]
    assert isinstance(description, str)
    assert description.startswith(render_definition_description(ticket))
    assert description.count(PACK_HEADER_PREFIX) == 1  # wrong answer: 2 (accumulation)
    # No pack content leaks into owned Atlas fields: the stored ticket is
    # untouched except the stamped cursor.
    after = TicketRepo(db).get_by_key("ATLAS-903")
    assert after is not None
    assert PACK_HEADER_PREFIX not in after.title
    assert PACK_HEADER_PREFIX not in after.objective
    assert after.linear_synced_at == LATER  # push-then-stamp
    # Byte-stable otherwise: only the cursor and the pull's observed-state
    # signal move — never a definition field.
    sync_signals = {"linear_synced_at", "last_observed_linear_state_id"}
    assert after.model_dump(exclude=sync_signals) == ticket.model_dump(
        exclude=sync_signals
    )


# --- AC-3 / D-1: the size boundary, both sides plus wiring ------------------


def test_description_at_pinned_limit_embeds_untruncated() -> None:
    ticket = Ticket(**ticket_kwargs())
    pack = make_pack(ticket, "")
    prefix = expected_prefix(ticket, pack)
    limit = len(prefix) + 500
    pack = pack.model_copy(update={"rendered_markdown": "x" * 500})

    composed = compose_embedded_description(ticket, pack, limit=limit)

    # Exactly AT the limit is not over it: untruncated, byte-exact form.
    assert composed.pack_truncated is False  # wrong answer: truncating at ==
    assert composed.description == prefix + "x" * 500
    assert len(composed.description) == limit


def test_description_over_pinned_limit_truncates_pack_with_marker() -> None:
    ticket = Ticket(**ticket_kwargs())
    pack = make_pack(ticket, "")
    prefix = expected_prefix(ticket, pack)
    limit = len(prefix) + 500
    pack = pack.model_copy(update={"rendered_markdown": "x" * 501})

    composed = compose_embedded_description(ticket, pack, limit=limit)

    assert composed.pack_truncated is True
    marker = (
        f"\n[pack truncated at {limit} chars — "
        f"full pack: atlas context render {ticket.key}]"
    )
    kept = 500 - len(marker)
    # Only the pack tail was cut; the marker names the truncation and the
    # full-pack route (A-2); the result honours the limit exactly.
    assert composed.description == prefix + "x" * kept + marker
    assert len(composed.description) == limit


def test_truncation_never_cuts_definition_fields() -> None:
    # A limit smaller than the definition itself: the composition refuses to
    # cut definition fields (the stronger rule) — the pack is gone entirely,
    # the marker is visible, and every definition byte survives.
    ticket = Ticket(**ticket_kwargs())
    pack = make_pack(ticket, "PACK CONTENT " * 100)

    composed = compose_embedded_description(ticket, pack, limit=10)

    assert composed.pack_truncated is True
    assert composed.description.startswith(render_definition_description(ticket))
    assert "PACK CONTENT" not in composed.description
    assert "pack truncated at 10 chars" in composed.description
    assert f"atlas context render {ticket.key}" in composed.description


def test_push_path_uses_default_pinned_limit(db: Database, repo: Path) -> None:
    # Sync-level wiring: the push path composes at EMBED_DESCRIPTION_LIMIT.
    # implementation_notes is rendered into the DEFINITION but not the pack,
    # so a ~99.6k-char note overflows the composed description while the pack
    # itself stays comfortably under its token budget — a valid pack, a
    # too-large composition: exactly the D-1 case (overflow is not failure).
    # Sized so the definition + marker still fit under the pin (the composer
    # never cuts definition fields) while definition + pack exceeds it.
    client = RecordingClient()
    ticket = seed(
        db,
        client,
        key="ATLAS-904",
        implementation_notes=["n" * 99_600],
    )

    result = run(db, client, collector_pair(repo))

    assert result.packs_embedded == 1  # truncated still counts as embedded
    assert result.packs_truncated == 1  # wrong answer: 0 (no default limit wired)
    assert result.pack_render_failures == 0
    ((_, definition),) = client.updates
    description = definition["description"]
    assert isinstance(description, str)
    assert len(description) <= EMBED_DESCRIPTION_LIMIT
    assert f"[pack truncated at {EMBED_DESCRIPTION_LIMIT} chars" in description
    # The definition fields were never cut: the full render is intact.
    assert description.startswith(render_definition_description(ticket))


# --- AC-4 / D-2: render-failure posture --------------------------------------


def test_pack_render_failure_pushes_definition_only_with_typed_anomaly(
    db: Database, repo: Path
) -> None:
    client = RecordingClient()
    # The document resolves; the slug does not: UnknownAnchorError, enumerated.
    ticket = seed(
        db, client, key="ATLAS-905", source_anchor=f"{ANCHOR_PATH}#no-such-slug"
    )
    provider = collector_pair(repo)

    result = run(db, client, provider)

    # The payload is TODAY'S exact definition-only form — byte-identical to
    # what the pre-164 push sent (the D-2 fallback IS definition_payload).
    assert result.pushed_updated == 1
    assert result.packs_embedded == 0
    assert result.pack_render_failures == 1
    ((_, definition),) = client.updates
    assert definition == {
        "title": render_definition_title(ticket),
        "description": render_definition_description(ticket),
    }
    # One typed, system-attributed DebtItem naming key, class, and posture.
    items = DebtItemRepo(db).list()
    assert len(items) == 1
    (item,) = items
    assert item.anomaly_type == AnomalyType.PACK_RENDER_FAILURE
    assert item.created_by_type == ActorType.SYSTEM
    assert item.created_by_id == CREATED_BY
    assert item.ticket_id == ticket.id
    assert "ATLAS-905" in item.summary
    assert "UnknownAnchorError" in item.summary
    assert "definition-only" in item.summary
    assert item.observed_at == NOW
    # The cursor STAMPED on the fallback (A-3): the next tick is a no-op — no
    # per-tick render retry, no update_issue drip, no DebtItem storm.
    second = run(db, client, provider)
    assert second.pushed_updated == 0
    assert second.pack_render_failures == 0
    assert len(client.updates) == 1
    assert len(DebtItemRepo(db).list()) == 1


def test_pack_render_failure_does_not_block_remaining_tickets(
    db: Database, repo: Path
) -> None:
    client = RecordingClient()
    seed(db, client, key="ATLAS-906", source_anchor=f"{ANCHOR_PATH}#no-such-slug")
    healthy = seed(db, client, key="ATLAS-907")

    result = run(db, client, collector_pair(repo))  # must not raise

    # The tick completed: both pushed; the healthy sibling EMBEDS.
    assert result.pushed_updated == 2
    assert result.pack_render_failures == 1
    assert result.packs_embedded == 1
    by_issue = dict(client.updates)
    assert healthy.external_linear_id is not None
    healthy_description = by_issue[healthy.external_linear_id]["description"]
    assert isinstance(healthy_description, str)
    assert _HEADER_RE.search(healthy_description) is not None
    assert BODY_PHRASE in healthy_description


def test_documents_loader_failure_degrades_every_embed_this_tick(
    db: Database, repo: Path
) -> None:
    # A dirty corpus file: the provider's fail-closed DirtyInputError is
    # tick-level — logged once at the load, and EVERY embedding push this tick
    # degrades to definition-only with its own DebtItem; the tick completes.
    client = RecordingClient()
    first = seed(db, client, key="ATLAS-908")
    second = seed(db, client, key="ATLAS-909")
    (repo / ANCHOR_PATH).write_text(CONTEXT_SPEC + "\ndirty edit\n", encoding="utf-8")

    result = run(db, client, collector_pair(repo))  # must not raise

    assert result.pushed_updated == 2
    assert result.packs_embedded == 0
    assert result.pack_render_failures == 2
    for _, definition in client.updates:
        description = definition["description"]
        assert isinstance(description, str)
        assert PACK_HEADER_PREFIX not in description
    items = DebtItemRepo(db).list()
    assert len(items) == 2
    assert {item.ticket_id for item in items} == {first.id, second.id}
    assert all("DirtyInputError" in item.summary for item in items)


# --- AC-5: request budget on a push tick -------------------------------------


def test_push_tick_request_count_equals_pushes_plus_fixed_cost(
    db: Database, repo: Path
) -> None:
    # The ATLAS-148 suite, extended: K stale-cursor tickets cost exactly K
    # update_issue calls over the one batched pull — rendering (which succeeds
    # and embeds for every ticket here) adds ZERO Linear calls of any kind.
    # The no-op bound itself stays pinned UNCHANGED at REQUEST_BUDGET = 12 in
    # test_pm_sync.py: embedding adds nothing to a tick that pushes nothing.
    client = CountingClient()
    stale = 7
    for index in range(stale):
        seed(
            db,
            client,
            key=f"ATLAS-{910 + index}",
            updated_at=LATER,
            linear_synced_at=EARLIER,
        )
    client.calls.clear()  # count the tick only, not the seeding

    result = run(db, client, collector_pair(repo))

    assert result.pushed_updated == stale
    assert result.packs_embedded == stale  # every push genuinely embedded
    assert client.calls["update_issue"] == stale
    assert client.calls["fetch_project_issues"] == 1
    assert client.calls.get("fetch_issue", 0) == 0  # nothing per-ticket
    assert client.calls.get("fetch_comments", 0) == 0  # planned: not scanned
    assert client.total_calls() == stale + 1, client.calls


# --- D-3: staleness ruling ----------------------------------------------------


def test_unchanged_definition_changed_corpus_does_not_repush(
    db: Database, repo: Path
) -> None:
    client = RecordingClient()
    seed(db, client, key="ATLAS-920")
    provider = collector_pair(repo)
    first = run(db, client, provider)
    assert first.packs_embedded == 1

    # A real corpus change, committed (heading kept so the anchor resolves).
    mutated = CONTEXT_SPEC.replace(BODY_PHRASE, BODY_PHRASE + " Now revised.")
    (repo / ANCHOR_PATH).write_text(mutated, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "mutate anchor doc")

    second = run(db, client, provider)

    # Packs refresh on definition change ONLY (D-3): the cursor sees no
    # definition change, so no re-push and no re-render — the accepted
    # staleness the embedded rendered_at header keeps visible. The wrong
    # answer: a corpus edit re-pushing O(affected-tickets) update_issue calls.
    assert second.pushed_updated == 0
    assert second.packs_embedded == 0
    assert len(client.updates) == 1

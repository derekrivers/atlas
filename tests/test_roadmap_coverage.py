"""ATLAS-115: roadmap-coverage sensor (architecture-fitness, second steal-list).

Invariant: every ATLAS-NN ticket referenced in any ``docs/closure/*.md`` must
appear in ``docs/atlas/implementation-roadmap.md``. The closure reports — not
git history (fragile under shallow clones and squash merges) — are the source
of "what was delivered"; this is a superset check that the roadmap names every
ticket a closure report mentions.

Retired tickets satisfy the invariant via the roadmap's ``Retired:`` lines, so
the check makes no delivered-vs-retired distinction — mere token presence is
enough. This is exactly the drift that let ATLAS-108..111 merge (closure §3)
without roadmap lines; the seeded-defect test below proves the sensor fires on
that class of regression.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ROADMAP = REPO_ROOT / "docs" / "atlas" / "implementation-roadmap.md"
CLOSURE_DIR = REPO_ROOT / "docs" / "closure"

_KEY_RE = re.compile(r"ATLAS-\d+")


def _keys(text: str) -> set[str]:
    """Every ATLAS-NN key token in ``text`` (epic keys like ATLAS-E1 do not
    match — the trailing ``\\d+`` requires a numeric id)."""
    return set(_KEY_RE.findall(text))


def missing_from_roadmap(roadmap_text: str, closure_texts: list[str]) -> set[str]:
    """Keys referenced in any closure report but absent from the roadmap.

    The single check both the live assertion and the seeded-defect guard call,
    so the guard exercises the same code path that protects the roadmap."""
    roadmap_keys = _keys(roadmap_text)
    referenced: set[str] = set()
    for text in closure_texts:
        referenced |= _keys(text)
    return referenced - roadmap_keys


def _closure_texts() -> list[str]:
    return [p.read_text(encoding="utf-8") for p in sorted(CLOSURE_DIR.glob("*.md"))]


def test_every_closure_ticket_appears_in_roadmap() -> None:
    missing = missing_from_roadmap(
        ROADMAP.read_text(encoding="utf-8"), _closure_texts()
    )
    assert missing == set(), (
        f"closure reports reference tickets absent from the roadmap: {sorted(missing)}"
    )


def test_sensor_fires_when_a_delivered_ticket_is_dropped() -> None:
    # Reproduce the original drift: remove the ATLAS-108 line (a Phase 2.5
    # live-discovered fix recorded in the closure) from a COPY of the roadmap.
    # The real file is never touched.
    roadmap_text = ROADMAP.read_text(encoding="utf-8")
    lines = roadmap_text.splitlines(keepends=True)
    pruned = [line for line in lines if not line.startswith("ATLAS-108 ")]
    assert len(pruned) < len(lines), "expected an 'ATLAS-108 ' line to remove"

    missing = missing_from_roadmap("".join(pruned), _closure_texts())

    assert "ATLAS-108" in missing, (
        "the sensor must flag ATLAS-108 once its roadmap line is removed"
    )
    # And the real roadmap (line restored) is clean — same helper, no leak.
    assert missing_from_roadmap(roadmap_text, _closure_texts()) == set()

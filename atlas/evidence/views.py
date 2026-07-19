"""JSON-shaped view records over evidence-domain objects, shared by any front-end."""

from atlas.core.models.evidence import Evidence


def evidence_summary(record: Evidence) -> dict[str, object]:
    """A concise per-row projection for `evidence list` (D4/D7): the identifying
    and triage fields, NOT the verbatim ``raw_payload`` (which `show` carries).
    Keeps a list of many rows readable and its JSON small."""
    return {
        "id": str(record.id),
        "evidence_type": record.evidence_type.value,
        "status": record.status.value,
        "commit_sha": record.commit_sha,
        "summary": record.summary,
        "created_at": record.created_at.isoformat(),
    }
